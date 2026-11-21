// Ingreso editorial independiente de la sesión de WhatsApp. Sin efectos al importar.
export const LEGAL = 'Autorizo al Diario La Campaña y Radio del Centro a publicar el material, sin obligación de ningún tipo de retribución.';
export const MAX_BODY = 32 * 1024 * 1024;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TYPES = {
  'image/jpeg': ['image', 'jpg'], 'image/png': ['image', 'png'],
  'image/webp': ['image', 'webp'], 'video/mp4': ['video', 'mp4'],
  'video/quicktime': ['video', 'mov'], 'video/webm': ['video', 'webm'],
  'audio/mpeg': ['audio', 'mp3'], 'audio/mp4': ['audio', 'm4a'],
  'audio/ogg': ['audio', 'ogg'], 'audio/wav': ['audio', 'wav'],
};
const CTX_KEYS = /^(ORIGEN|NOMBRE|CELULAR|LUGAR|DESCRIPCION|AUTORIZACION|HABLA|NUMERACION)\s*:/i;
const LINE_BREAK = /[\r\n\v\f\u0085\u2028\u2029]/;
const json = (status, data) => new Response(JSON.stringify(data), {
  status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
});
class InputError extends Error {
  constructor(status, code) { super(code); this.status = status; }
}
function text(value, max, required = false, singleLine = false) {
  if (typeof value !== 'string' || value.includes('\0')) throw new InputError(400, 'invalid_metadata');
  const result = value.trim();
  if (result.length > max || (required && !result) || (singleLine && LINE_BREAK.test(result))) {
    throw new InputError(400, 'invalid_metadata');
  }
  return result;
}
export function contextValue(value) {
  // Un relato puede contener rótulos como «NOMBRE:». No deben redefinir los datos del envío.
  return value.split(/\r\n|[\r\n\v\f\u0085\u2028\u2029]/)
    .map(line => CTX_KEYS.test(line.trim()) ? '> ' + line : line).join('\n');
}
async function hash(data) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', data))]
    .map(x => x.toString(16).padStart(2, '0')).join('');
}
async function authorized(header, secret) {
  if (!secret || secret.length < 32 || !header?.startsWith('Bearer ')) return false;
  const enc = new TextEncoder();
  const [a, b] = await Promise.all([hash(enc.encode(header.slice(7))), hash(enc.encode(secret))]);
  let different = 0;
  for (let i = 0; i < a.length; i++) different |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return different === 0;
}
async function boundedBody(req) {
  const length = req.headers.get('content-length');
  if (length && (!/^\d+$/.test(length) || Number(length) > MAX_BODY)) throw new InputError(413, 'request_too_large');
  if (!req.body) throw new InputError(400, 'empty_body');
  const reader = req.body.getReader(), chunks = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BODY) { await reader.cancel(); throw new InputError(413, 'request_too_large'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  return new Blob(chunks, { type: req.headers.get('content-type') || '' });
}
function matchesType(bytes, mime) {
  const ascii = (start, end) => String.fromCharCode(...bytes.slice(start, end));
  if (mime === 'image/png') return bytes.slice(0, 8).join(',') === '137,80,78,71,13,10,26,10';
  if (mime === 'image/jpeg') return bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
  if (mime === 'image/webp') return ascii(0, 4) === 'RIFF' && ascii(8, 12) === 'WEBP';
  if (mime === 'video/webm') return bytes.slice(0, 4).join(',') === '26,69,223,163';
  if (mime === 'video/mp4' || mime === 'video/quicktime' || mime === 'audio/mp4') return ascii(4, 8) === 'ftyp';
  if (mime === 'audio/ogg') return ascii(0, 4) === 'OggS';
  if (mime === 'audio/wav') return ascii(0, 4) === 'RIFF' && ascii(8, 12) === 'WAVE';
  if (mime === 'audio/mpeg') return ascii(0, 3) === 'ID3' || (bytes[0] === 255 && (bytes[1] & 224) === 224);
  return false;
}
async function parseSubmission(req) {
  if (!/^multipart\/form-data\s*;/i.test(req.headers.get('content-type') || '')) throw new InputError(415, 'multipart_required');
  const form = await new Response(await boundedBody(req)).formData().catch(() => { throw new InputError(400, 'invalid_multipart'); });
  if ([...form.keys()].some(key => key !== 'metadata' && key !== 'file') || form.getAll('metadata').length !== 1) {
    throw new InputError(400, 'invalid_fields');
  }
  let raw;
  try { raw = JSON.parse(form.get('metadata')); } catch { throw new InputError(400, 'invalid_metadata'); }
  if (!raw || Array.isArray(raw) || typeof raw !== 'object' || Object.keys(raw).some(k =>
    !['descripcion', 'lugar', 'habla', 'fuente_url', 'autorizacion'].includes(k))) throw new InputError(400, 'invalid_metadata');
  if (raw.autorizacion !== 'ACEPTO') throw new InputError(400, 'authorization_required');
  const metadata = {
    descripcion: text(raw.descripcion, 30000, true), lugar: text(raw.lugar ?? '', 200, false, true),
    habla: text(raw.habla ?? '', 2000), fuente_url: text(raw.fuente_url ?? '', 2000, false, true),
    autorizacion: 'ACEPTO',
  };
  if (metadata.fuente_url) {
    let source;
    try { source = new URL(metadata.fuente_url); } catch { throw new InputError(400, 'invalid_source_url'); }
    if (!['http:', 'https:'].includes(source.protocol) || source.username || source.password) throw new InputError(400, 'invalid_source_url');
  }
  const uploads = form.getAll('file');
  if (!uploads.length || uploads.length > 8) throw new InputError(400, 'invalid_file_count');
  const media = [];
  for (const file of uploads) {
    if (!(file instanceof File) || !file.size || !TYPES[file.type]) throw new InputError(415, 'unsupported_media');
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (!matchesType(bytes, file.type)) throw new InputError(415, 'media_type_mismatch');
    const [tipo, ext] = TYPES[file.type];
    media.push({ bytes, tipo, mime: file.type, ext, size: bytes.length, sha256: await hash(bytes) });
  }
  if (!media.some(m => m.tipo !== 'audio')) throw new InputError(400, 'visual_media_required');
  if (media.some(m => m.tipo === 'audio') && !metadata.habla) throw new InputError(400, 'speaker_required');
  const fingerprint = await hash(new TextEncoder().encode(JSON.stringify({ metadata,
    media: media.map(({ mime, size, sha256 }) => ({ mime, size, sha256 })) })));
  return { metadata, media, fingerprint };
}
function receipt(row) {
  return { id: row.id, estado: row.estado, carpeta: row.folder_name, archivos: row.file_count,
    bytes: row.total_bytes, recibido: row.received_at || null,
    mensaje: row.estado === 'received' ? 'Material depositado para el procesamiento editorial; no confirma publicación.'
      : row.estado === 'failed' ? 'No se completó el depósito. Revisar antes de iniciar otro envío.'
      : 'Depósito en curso o pendiente de verificación. Consultar este mismo identificador.' };
}
export function createHandler(config, services) {
  return async req => {
    try {
      if (!config.secret || config.secret.length < 32 || !config.authorName?.trim() ||
          LINE_BREAK.test(config.authorName) || config.authorName.trim().length > 60 ||
          config.authorName.trim().split(/\s+/).length > 7 || !/^\d{10,15}$/.test(config.authorPhone || '')) {
        return json(503, { error: 'ingest_not_configured' });
      }
      if (!await authorized(req.headers.get('authorization'), config.secret)) return json(401, { error: 'unauthorized' });
      const url = new URL(req.url);
      if (req.method !== 'GET' && req.method !== 'POST') return json(405, { error: 'method_not_allowed' });
      const id = req.method === 'GET' ? url.searchParams.get('id') : req.headers.get('idempotency-key');
      if (!UUID.test(id || '')) return json(400, { error: 'uuid_id_required' });
      const settle = async row => {
        if (row.estado === 'processing' && row.folder_id && await services.drive.isReleased(row.folder_id)) {
          row = await services.store.received(row.id);
        }
        return json(row.estado === 'processing' ? 202 : 200, receipt(row));
      };
      if (req.method === 'GET') {
        const row = await services.store.get(id);
        return row ? await settle(row) : json(404, { error: 'receipt_not_found' });
      }
      const { metadata, media, fingerprint } = await parseSubmission(req);
      const folderName = `corresponsal_${new Date().toISOString().slice(0, 10)}_editorial_${id}`;
      const reserved = await services.store.reserve({ id, payload_hash: fingerprint, estado: 'processing',
        folder_name: folderName, file_count: media.length, total_bytes: media.reduce((n, m) => n + m.size, 0) });
      if (!reserved.created) {
        if (reserved.row.payload_hash !== fingerprint) return json(409, { error: 'id_reused_with_different_content', id });
        return await settle(reserved.row);
      }
      let folder, releaseStarted = false;
      try {
        // STAGING está fuera del árbol vigilado. La carpeta se mueve recién con TODO completo.
        folder = await services.drive.create(folderName);
        await services.store.attachFolder(id, folder);
        const now = new Date().toISOString();
        const context = `ORIGEN: corresponsal-editorial-directo\nNOMBRE: ${config.authorName.trim()}\n` +
          `CELULAR: ${config.authorPhone}\nNUMERACION: llegada\nLUGAR: ${contextValue(metadata.lugar)}\n` +
          `DESCRIPCION: ${contextValue(metadata.descripcion)}\n` +
          (metadata.habla ? `HABLA: ${contextValue(metadata.habla)}\n` : '') +
          `AUTORIZACION: ACEPTADA — ${LEGAL} — ${now} — ingreso:${id}\n`;
        const enc = new TextEncoder();
        await services.drive.upload(folder, 'contexto.txt', 'text/plain; charset=utf-8', enc.encode(context));
        await services.drive.upload(folder, 'ingreso.json', 'application/json', enc.encode(JSON.stringify({
          id, fecha: now, autor: config.authorName.trim(), autorizacion: LEGAL,
          fuente_url: metadata.fuente_url, media: media.map(({ tipo, mime, size, sha256 }, i) =>
            ({ orden: i + 1, tipo, mime, size, sha256 })),
        })));
        // Los nombres conservan orden entre tipos y no incluyen nombres de archivo del cliente.
        for (let i = 0; i < media.length; i++) {
          const m = media[i];
          await services.drive.upload(folder, `${m.tipo === 'image' ? 'foto' : m.tipo}_${id}_${String(i + 1).padStart(2, '0')}.${m.ext}`, m.mime, m.bytes);
        }
        releaseStarted = true;
        await services.drive.release(folder);
        const row = await services.store.received(id);
        return json(201, receipt(row));
      } catch {
        // Si el movimiento pudo concretarse pero falló su respuesta, no borrar ni volver a subir:
        // GET reconcilia por el padre de Drive. Así un timeout nunca genera dos carpetas.
        if (releaseStarted) return json(202, { id, estado: 'processing', mensaje: 'Consultar el recibo para verificar el depósito.' });
        if (folder) { try { await services.drive.discard(folder); } catch { /* queda aislada en staging */ } }
        await services.store.failed(id);
        return json(502, { id, estado: 'failed', error: 'deposit_failed' });
      }
    } catch (error) {
      return json(error instanceof InputError ? error.status : 503,
        { error: error instanceof InputError ? error.message : 'service_unavailable' });
    }
  };
}
