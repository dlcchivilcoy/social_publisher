// Adaptadores HTTP sin dependencias; credenciales solo en el servidor.
export function createServices(env, http = fetch) {
  const sbUrl = env('SUPABASE_URL'), sbKey = env('SUPABASE_SERVICE_ROLE_KEY');
  // EDITORIAL_TARGET_FOLDER_ID (opcional): destino de ENSAYO fuera del árbol vigilado, para probar el recorrido
  // completo sin que el desgrabador lo procese. Sin esa variable, el destino es la carpeta de corresponsales.
  const staging = env('EDITORIAL_STAGING_FOLDER_ID');
  const target = env('EDITORIAL_TARGET_FOLDER_ID') || env('DRIVE_CORRESPONSALES_FOLDER_ID');
  if (!sbUrl || !sbKey || !staging || !target || staging === target) throw new Error('missing_configuration');
  const headers = { apikey: sbKey, Authorization: `Bearer ${sbKey}`, 'Content-Type': 'application/json' };
  async function db(path = '', method = 'GET', body, extra = {}) {
    const response = await http(`${sbUrl}/rest/v1/editorial_ingresos${path}`, {
      method, headers: { ...headers, ...extra }, body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) throw new Error('database_error');
    return response.status === 204 ? [] : response.json();
  }
  const one = rows => { if (!rows?.[0]) throw new Error('missing_receipt'); return rows[0]; };
  const where = id => `?id=eq.${encodeURIComponent(id)}`;
  const patch = (id, data) => db(where(id), 'PATCH', data, { Prefer: 'return=representation' }).then(one);
  const store = {
    get: async id => (await db(`${where(id)}&select=*`))[0] || null,
    reserve: async row => {
      const rows = await db('?on_conflict=id', 'POST', row,
        { Prefer: 'resolution=ignore-duplicates,return=representation' });
      return rows.length ? { created: true, row: rows[0] }
        : { created: false, row: one(await db(`${where(row.id)}&select=*`)) };
    },
    attachFolder: (id, folder_id) => patch(id, { folder_id }),
    received: id => patch(id, { estado: 'received', received_at: new Date().toISOString() }),
    failed: id => patch(id, { estado: 'failed' }),
  };
  // El token es local a esta petición, nunca queda en una respuesta ni en un recibo.
  let accessToken;
  async function token() {
    if (accessToken) return accessToken;
    const r = await http('https://oauth2.googleapis.com/token', { method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({
        client_id: env('GOOGLE_OAUTH_CLIENT_ID'), client_secret: env('GOOGLE_OAUTH_CLIENT_SECRET'),
        refresh_token: env('GOOGLE_OAUTH_REFRESH_TOKEN'), grant_type: 'refresh_token',
      }) });
    const result = r.ok ? await r.json() : {};
    if (!result.access_token) throw new Error('google_auth_failed');
    accessToken = result.access_token;
    return accessToken;
  }
  async function driveCall(path, method = 'GET', body) {
    const response = await http(`https://www.googleapis.com/drive/v3/files${path}`, {
      method, headers: { Authorization: `Bearer ${await token()}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) throw new Error('drive_error');
    return response.status === 204 ? {} : response.json();
  }
  const drive = {
    create: async name => {
      const result = await driveCall('?fields=id&supportsAllDrives=true', 'POST', {
        name, mimeType: 'application/vnd.google-apps.folder', parents: [staging],
      });
      if (!result.id) throw new Error('drive_folder_missing');
      return result.id;
    },
    upload: async (folder, name, mime, bytes) => {
      const boundary = 'editorial-' + crypto.randomUUID();
      const body = new Blob([
        `--${boundary}\r\nContent-Type: application/json; charset=utf-8\r\n\r\n`,
        JSON.stringify({ name, parents: [folder] }),
        `\r\n--${boundary}\r\nContent-Type: ${mime}\r\n\r\n`, bytes, `\r\n--${boundary}--\r\n`,
      ]);
      const response = await http('https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true', {
        method: 'POST', headers: { Authorization: `Bearer ${await token()}`,
          'Content-Type': `multipart/related; boundary=${boundary}` }, body,
      });
      if (!response.ok) throw new Error('drive_upload_failed');
      await response.body?.cancel();
    },
    release: folder => driveCall(`/${encodeURIComponent(folder)}?addParents=${encodeURIComponent(target)}` +
      `&removeParents=${encodeURIComponent(staging)}&fields=id&supportsAllDrives=true`, 'PATCH', {}),
    isReleased: async folder => {
      const f = await driveCall(`/${encodeURIComponent(folder)}?fields=parents,trashed&supportsAllDrives=true`);
      return !f.trashed && (f.parents || []).includes(target);
    },
    discard: folder => driveCall(`/${encodeURIComponent(folder)}?fields=id&supportsAllDrives=true`, 'PATCH', { trashed: true }),
  };
  return { store, drive };
}
