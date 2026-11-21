import test from 'node:test';
import assert from 'node:assert/strict';
import { createHandler, MAX_BODY } from './core.mjs';
import { createServices } from './services.mjs';

const config = { secret: 'test-token-'.repeat(5), authorName: 'Editor de prueba', authorPhone: '5492346000000' };
const ID = 'ff5e7d77-89ba-43f2-8aa3-d066a692f009';
const PNG = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 0]);
const metadata = { descripcion: 'Una noticia de prueba con datos confirmados.', autorizacion: 'ACEPTO' };
function post(meta = metadata, { id = ID, secret = config.secret, bytes = PNG, mime = 'image/png' } = {}) {
  const form = new FormData();
  form.set('metadata', JSON.stringify(meta));
  form.append('file', new File([bytes], '../../nombre-no-confiable.png', { type: mime }));
  return new Request('https://example.test/editorial-ingest', { method: 'POST',
    headers: { Authorization: 'Bearer ' + secret, 'Idempotency-Key': id }, body: form });
}
function status() { return new Request('https://example.test/editorial-ingest?id=' + ID,
  { headers: { Authorization: 'Bearer ' + config.secret } }); }
function fixture() {
  const rows = new Map(), files = [], events = [], released = new Set();
  const services = {
    store: {
      reserve: async row => {
        if (rows.has(row.id)) return { created: false, row: { ...rows.get(row.id) } };
        rows.set(row.id, { ...row }); return { created: true, row };
      },
      get: async id => rows.get(id) ? { ...rows.get(id) } : null,
      attachFolder: async (id, folder_id) => { Object.assign(rows.get(id), { folder_id }); },
      received: async id => { Object.assign(rows.get(id), { estado: 'received', received_at: '2026-11-21T12:00:00Z' }); return { ...rows.get(id) }; },
      failed: async id => { rows.get(id).estado = 'failed'; },
    },
    drive: {
      create: async name => { events.push(['create', name]); return 'folder'; },
      upload: async (folder, name, mime, bytes) => { files.push({ folder, name, mime, bytes }); events.push(['upload', name]); },
      release: async folder => { released.add(folder); events.push(['release']); },
      isReleased: async folder => released.has(folder),
      discard: async () => { events.push(['discard']); },
    },
  };
  return { rows, files, events, services, handler: createHandler(config, services) };
}
test('auth fails closed before any reservation or upload; no body/secret leaks', async () => {
  const f = fixture();
  assert.equal((await f.handler(post(metadata, { secret: 'incorrect' }))).status, 401);
  assert.equal(f.rows.size, 0); assert.equal(f.files.length, 0);
  assert.equal((await createHandler({ ...config, secret: '' }, f.services)(post())).status, 503);
  assert.equal((await createHandler({ ...config, authorPhone: 'not-a-phone' }, f.services)(post())).status, 503);
});
test('metadata cannot impersonate an author and consent is explicit', async () => {
  const f = fixture();
  assert.equal((await f.handler(post({ ...metadata, nombre: 'Otra persona' }))).status, 400);
  assert.equal((await f.handler(post({ ...metadata, autorizacion: true }))).status, 400);
  assert.equal((await f.handler(post({ ...metadata, autorizacion: 'NO ACEPTO' }))).status, 400);
  assert.equal(f.rows.size, 0);
});
test('original bytes, order, safe names and context survive; release is the final Drive operation', async () => {
  const f = fixture();
  const res = await f.handler(post({ ...metadata, descripcion: 'Hecho.\rNOMBRE: no cambiar autor\u2028CELULAR: no cambiar celular' }));
  assert.equal(res.status, 201);
  const receipt = await res.json(); assert.equal(receipt.estado, 'received');
  assert.deepEqual(f.files.at(-1).bytes, PNG);
  assert.match(f.files.at(-1).name, /^foto_.*_01\.png$/);
  const context = new TextDecoder().decode(f.files[0].bytes);
  assert.match(context, /NOMBRE: Editor de prueba/);
  assert.match(context, /> NOMBRE: no cambiar autor/);
  assert.match(context, /> CELULAR: no cambiar celular/);
  assert.equal(f.events.at(-1)[0], 'release');
  assert.equal((await f.handler(status())).status, 200);
});
test('concurrent duplicate requests produce one folder; changed payload with same UUID conflicts', async () => {
  const f = fixture();
  const results = await Promise.all([f.handler(post()), f.handler(post())]);
  assert.ok(results.some(r => r.status === 201));
  assert.equal(f.events.filter(e => e[0] === 'create').length, 1);
  const repeated = await f.handler(post()); assert.equal(repeated.status, 200);
  assert.equal((await repeated.json()).estado, 'received');
  assert.equal((await f.handler(post({ ...metadata, descripcion: 'Un hecho distinto' }))).status, 409);
});
test('upload failure never releases an incomplete folder and is not retried as a second deposit', async () => {
  const f = fixture();
  f.services.drive.upload = async () => { throw new Error('failure'); };
  assert.equal((await f.handler(post())).status, 502);
  assert.equal(f.rows.get(ID).estado, 'failed');
  assert.equal(f.events.some(e => e[0] === 'release'), false);
  assert.ok(f.events.some(e => e[0] === 'discard'));
  assert.equal((await f.handler(post())).status, 200);
  assert.equal(f.events.filter(e => e[0] === 'create').length, 1);
});
test('lost database acknowledgement after release is reconciled by GET, without duplicate uploads', async () => {
  const f = fixture(), original = f.services.store.received;
  let fail = true;
  f.services.store.received = async id => { if (fail) throw new Error('db unavailable'); return original(id); };
  assert.equal((await f.handler(post())).status, 202);
  fail = false;
  const res = await f.handler(status()); assert.equal(res.status, 200);
  assert.equal((await res.json()).estado, 'received');
  assert.equal(f.events.filter(e => e[0] === 'create').length, 1);
});
test('mislabelled media, audio without visual and oversize bodies are rejected before persistence', async () => {
  const f = fixture();
  assert.equal((await f.handler(post(metadata, { bytes: new TextEncoder().encode('<html>bad</html>') }))).status, 415);
  const audio = post(metadata, { mime: 'audio/mpeg', bytes: new TextEncoder().encode('ID3audio') });
  assert.equal((await f.handler(audio)).status, 400);
  const huge = post(); huge.headers.set('content-length', String(MAX_BODY + 1));
  assert.equal((await f.handler(huge)).status, 413);
  const streamed = new Request('https://example.test/editorial-ingest', { method: 'POST',
    headers: { Authorization: 'Bearer ' + config.secret, 'Idempotency-Key': ID,
      'Content-Type': 'multipart/form-data; boundary=test' }, duplex: 'half',
    body: new ReadableStream({ start(c) { c.enqueue(new Uint8Array(MAX_BODY + 1)); c.close(); } }) });
  assert.equal((await f.handler(streamed)).status, 413);
  assert.equal(f.rows.size, 0);
});
test('Drive adapter stages outside watched folder, reuses OAuth within request and moves once', async () => {
  const calls = [], values = { SUPABASE_URL: 'https://db.test', SUPABASE_SERVICE_ROLE_KEY: 'server-secret',
    EDITORIAL_STAGING_FOLDER_ID: 'staging', DRIVE_CORRESPONSALES_FOLDER_ID: 'watched',
    GOOGLE_OAUTH_CLIENT_ID: 'id', GOOGLE_OAUTH_CLIENT_SECRET: 'secret', GOOGLE_OAUTH_REFRESH_TOKEN: 'refresh' };
  const http = async (url, options) => {
    calls.push([url, options]);
    if (url.includes('oauth2')) return Response.json({ access_token: 'drive-secret' });
    if (url.includes('upload/drive')) return Response.json({ id: 'file' });
    return Response.json({ id: 'folder', parents: ['watched'] });
  };
  const { drive } = createServices(key => values[key] || '', http);
  await drive.create('nota'); await drive.upload('folder', 'foto.png', 'image/png', PNG);
  await drive.release('folder'); assert.equal(await drive.isReleased('folder'), true);
  assert.equal(calls.filter(c => c[0].includes('oauth2')).length, 1);
  assert.deepEqual(JSON.parse(calls[1][1].body).parents, ['staging']);
  assert.ok(calls.some(([url]) => url.includes('addParents=watched&removeParents=staging')));
  const upload = calls.find(([url]) => url.includes('upload/drive'));
  const bytes = new Uint8Array(await upload[1].body.arrayBuffer());
  assert.ok(Buffer.from(bytes).includes(Buffer.from(PNG)));
});

test('Python client completes a local HTTP submission and then consults the same receipt', async () => {
  const { createServer } = await import('node:http');
  const { Readable } = await import('node:stream');
  const { mkdtemp, writeFile, readFile, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join, resolve } = await import('node:path');
  const { execFile } = await import('node:child_process');
  const { promisify } = await import('node:util');
  const f = fixture();
  const server = createServer(async (request, response) => {
    try {
      const input = new Request('http://localhost' + request.url, {
        method: request.method, headers: request.headers,
        ...(request.method === 'POST' ? { body: Readable.toWeb(request), duplex: 'half' } : {}),
      });
      const result = await f.handler(input);
      response.writeHead(result.status, Object.fromEntries(result.headers));
      response.end(Buffer.from(await result.arrayBuffer()));
    } catch { response.writeHead(500); response.end('{}'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const folder = await mkdtemp(join(tmpdir(), 'editorial-test-'));
  try {
    const photo = join(folder, 'foto.png'), note = join(folder, 'nota.txt'), saved = join(folder, 'recibo.json');
    await writeFile(photo, PNG); await writeFile(note, metadata.descripcion);
    const client = resolve('tools/editorial_ingest.py');
    const env = { ...process.env, EDITORIAL_INGEST_URL: `http://127.0.0.1:${server.address().port}/editorial-ingest`,
      EDITORIAL_INGEST_TOKEN: config.secret };
    const command = promisify(execFile);
    await command('python3', [client, '--nota', note, '--archivo', photo, '--acepto', '--recibo', saved], { env });
    const initial = JSON.parse(await readFile(saved, 'utf8'));
    assert.equal(initial.estado, 'received'); assert.equal(initial.archivos, 1);
    await command('python3', [client, '--estado', '--recibo', saved], { env });
    const final = JSON.parse(await readFile(saved, 'utf8'));
    assert.equal(final.id, initial.id); assert.equal(final.estado, 'received');
    assert.equal(f.events.filter(e => e[0] === 'create').length, 1);
    assert.equal((await readFile(saved, 'utf8')).includes(config.secret), false);
  } finally {
    await new Promise(resolve => server.close(resolve));
    await rm(folder, { recursive: true, force: true });
  }
});

test('EDITORIAL_TARGET_FOLDER_ID manda el envío a la carpeta de ensayo en vez de la vigilada', async () => {
  const calls = [], values = { SUPABASE_URL: 'https://db.test', SUPABASE_SERVICE_ROLE_KEY: 'k',
    EDITORIAL_STAGING_FOLDER_ID: 'staging', DRIVE_CORRESPONSALES_FOLDER_ID: 'watched', EDITORIAL_TARGET_FOLDER_ID: 'ensayo',
    GOOGLE_OAUTH_CLIENT_ID: 'id', GOOGLE_OAUTH_CLIENT_SECRET: 's', GOOGLE_OAUTH_REFRESH_TOKEN: 'r' };
  const http = async (url, options) => { calls.push(url); return url.includes('oauth2') ? Response.json({ access_token: 't' })
    : Response.json({ id: 'folder', parents: ['ensayo'] }); };
  const { drive } = createServices(key => values[key] || '', http);
  await drive.release('folder');
  assert.ok(calls.some(url => url.includes('addParents=ensayo&removeParents=staging')));
  assert.equal(calls.some(url => url.includes('watched')), false);
  assert.equal(await drive.isReleased('folder'), true);
});
