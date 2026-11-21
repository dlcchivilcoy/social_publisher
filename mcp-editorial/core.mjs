// Conector MCP del ingreso editorial para ChatGPT (y Claude) — Diario La Campaña / Radio del Centro.
//
// Un solo manejador `fetch(Request) → Response`, sin dependencias, que expone:
//   - OAuth 2.1 con PKCE (S256) y registro dinámico de clientes (lo que pide ChatGPT para un conector):
//     /.well-known/oauth-protected-resource, /.well-known/oauth-authorization-server (y openid-configuration),
//     /register, /authorize (página donde el editor escribe SU clave de acceso), /token.
//   - MCP por HTTP (Streamable HTTP sin sesión, respuestas JSON) en /mcp, con dos herramientas:
//     `enviar_material` (texto + fotos/videos/audios adjuntos en el chat → API de ingreso editorial) y
//     `consultar_envio` (el recibo de un envío).
//
// Las credenciales quedan en el servidor (variables de entorno): ChatGPT solo recibe tokens firmados que valen
// para este conector. Todo es SIN ESTADO: los códigos, tokens y clientes registrados van firmados con HMAC
// (MCP_SIGNING_SECRET). Rotar ese secreto corta todos los accesos; cambiar MCP_ACCESS_KEY corta los refresh.
// Sin efectos al importar.

export const SCOPE = 'editorial';
const ACCESS_TTL = 3600;                  // 1 hora
const REFRESH_TTL = 30 * 24 * 3600;       // 30 días
const CODE_TTL = 120;                     // el código de autorización vale 2 minutos (y exige PKCE)
const MAX_TOTAL = 30 * 1024 * 1024;       // la API de ingreso acepta 32 MiB por pedido (con el multipart)
const PROTOCOLOS = ['2025-11-25', '2025-06-18', '2025-03-26'];
const REDIRECTS_FIJOS = ['https://chatgpt.com/connector_platform_oauth_redirect', 'https://claude.ai/api/mcp/auth_callback'];
const REDIRECTS_PREFIJO = ['https://chatgpt.com/connector/oauth/'];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const enc = new TextEncoder();

// ── utilidades ─────────────────────────────────────────────────────────────────
const b64url = bytes => Buffer.from(bytes).toString('base64url');
const desde64 = texto => Buffer.from(texto, 'base64url');
async function sha256(data) {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', typeof data === 'string' ? enc.encode(data) : data));
}
const hex = bytes => [...bytes].map(x => x.toString(16).padStart(2, '0')).join('');
function igual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  const x = enc.encode(a), y = enc.encode(b);
  let d = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) d |= (x[i] || 0) ^ (y[i] || 0);
  return d === 0;
}
async function hmac(secreto, texto) {
  const clave = await crypto.subtle.importKey('raw', enc.encode(secreto), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', clave, enc.encode(texto)));
}
async function firmar(secreto, datos) {
  const cuerpo = b64url(enc.encode(JSON.stringify(datos)));
  return `${cuerpo}.${b64url(await hmac(secreto, 'v1.' + cuerpo))}`;
}
async function verificar(secreto, token, tipo) {
  if (typeof token !== 'string' || token.length > 8192) return null;
  const [cuerpo, firma, sobra] = token.split('.');
  if (!cuerpo || !firma || sobra !== undefined) return null;
  if (!igual(firma, b64url(await hmac(secreto, 'v1.' + cuerpo)))) return null;
  let datos;
  try { datos = JSON.parse(desde64(cuerpo).toString('utf8')); } catch { return null; }
  if (!datos || datos.t !== tipo) return null;
  if (typeof datos.exp === 'number' && datos.exp < Math.floor(Date.now() / 1000)) return null;
  return datos;
}
const ahora = () => Math.floor(Date.now() / 1000);
function json(status, datos, extra = {}) {
  return new Response(JSON.stringify(datos), { status, headers: {
    'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...extra } });
}
function html(status, cuerpo) {
  return new Response(`<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Ingreso editorial</title>
<style>body{font-family:system-ui,sans-serif;background:#1b1d20;color:#f2f2f2;display:flex;justify-content:center;padding:40px 16px}
main{max-width:420px;width:100%}h1{color:#fd8511;font-size:22px}p{color:#c4c8ce;line-height:1.45}
input{width:100%;padding:12px;border-radius:8px;border:1px solid #555;background:#26292d;color:#fff;font-size:16px;box-sizing:border-box}
button{margin-top:14px;padding:12px 18px;border:0;border-radius:8px;font-size:16px;cursor:pointer}
.si{background:#fd8511;color:#1b1d20;font-weight:700}.no{background:#3a3d42;color:#ddd;margin-left:8px}.error{color:#ff8a80}</style>
</head><body><main>${cuerpo}</main></body></html>`, { status, headers: {
    'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY',
    'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://chatgpt.com https://claude.ai http://127.0.0.1:* http://localhost:*; frame-ancestors 'none'",
    'Referrer-Policy': 'no-referrer' } });
}
const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

// ── configuración ──────────────────────────────────────────────────────────────
export function leerConfig(env) {
  const base = (env('MCP_PUBLIC_URL') || '').replace(/\/+$/, '');
  const config = {
    base, recurso: base + '/mcp',
    secreto: env('MCP_SIGNING_SECRET') || '', clave: env('MCP_ACCESS_KEY') || '',
    // Clave fija OPCIONAL (21/11/2026): la app de escritorio de ChatGPT conecta el chat con otro cliente MCP que no
    // usa el login OAuth y solo manda «Bearer» desde una variable de entorno de la compu del editor.
    tokenFijo: env('MCP_STATIC_TOKEN') || '',
    ingestUrl: (env('EDITORIAL_INGEST_URL') || '').replace(/\/+$/, ''), ingestToken: env('EDITORIAL_INGEST_TOKEN') || '',
    redirectsExtra: (env('MCP_REDIRECTS_EXTRA') || '').split(',').map(s => s.trim()).filter(Boolean),
    hostsArchivos: (env('MCP_FILE_HOSTS') || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean),
  };
  config.listo = /^https:\/\/[^/?#]+$/.test(base) && config.secreto.length >= 32 && config.clave.length >= 12 &&
    /^https:\/\//.test(config.ingestUrl) && config.ingestToken.length >= 32;
  return config;
}

function redirectPermitido(config, uri) {
  if (typeof uri !== 'string' || uri.length > 500) return false;
  let u;
  try { u = new URL(uri); } catch { return false; }
  if (u.hash || u.username || u.password) return false;
  // Apps de escritorio (ChatGPT de escritorio / Codex, Claude Desktop): terminan el login en la PROPIA compu, en un
  // puerto local (RFC 8252, «loopback»). Solo llega a la máquina del usuario y el canje igual exige PKCE.
  if (u.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname)) return true;
  if (u.protocol !== 'https:') return false;
  return REDIRECTS_FIJOS.includes(uri) || config.redirectsExtra.includes(uri) ||
    REDIRECTS_PREFIJO.some(p => uri.startsWith(p) && /^[A-Za-z0-9_-]+$/.test(uri.slice(p.length)));
}

// ── OAuth ──────────────────────────────────────────────────────────────────────
function metadatosAS(config) {
  return {
    issuer: config.base, authorization_endpoint: config.base + '/authorize', token_endpoint: config.base + '/token',
    registration_endpoint: config.base + '/register', response_types_supported: ['code'],
    grant_types_supported: ['authorization_code', 'refresh_token'], code_challenge_methods_supported: ['S256'],
    token_endpoint_auth_methods_supported: ['none'], scopes_supported: [SCOPE],
    authorization_response_iss_parameter_supported: true, client_id_metadata_document_supported: false,
  };
}
function metadatosRecurso(config) {
  return { resource: config.recurso, authorization_servers: [config.base], scopes_supported: [SCOPE],
    bearer_methods_supported: ['header'], resource_name: 'Ingreso editorial — Diario La Campaña' };
}
function desafio(config, error = '', descripcion = '') {
  return `Bearer resource_metadata="${config.base}/.well-known/oauth-protected-resource", scope="${SCOPE}"` +
    (error ? `, error="${error}", error_description="${descripcion}"` : '');
}
const huellaClave = async config => hex(await sha256('clave:' + config.clave)).slice(0, 16);

async function registrar(config, req) {
  let datos;
  try { datos = await req.json(); } catch { return json(400, { error: 'invalid_client_metadata' }); }
  const uris = Array.isArray(datos?.redirect_uris) ? datos.redirect_uris : [];
  if (!uris.length || uris.length > 5 || !uris.every(u => redirectPermitido(config, u))) {
    return json(400, { error: 'invalid_redirect_uri', error_description: 'Solo se aceptan los retornos de ChatGPT, Claude o de la propia computadora.' });
  }
  if (datos.token_endpoint_auth_method && datos.token_endpoint_auth_method !== 'none') {
    return json(400, { error: 'invalid_client_metadata', error_description: 'Cliente público con PKCE (token_endpoint_auth_method=none).' });
  }
  const nombre = String(datos.client_name || 'Cliente MCP').slice(0, 80);
  const client_id = await firmar(config.secreto, { t: 'cliente', r: uris, n: nombre, iat: ahora() });
  return json(201, { client_id, client_id_issued_at: ahora(), client_name: nombre, redirect_uris: uris,
    token_endpoint_auth_method: 'none', grant_types: ['authorization_code', 'refresh_token'], response_types: ['code'] });
}

async function validarPedidoAutorizacion(config, p) {
  const cliente = await verificar(config.secreto, p.get('client_id'), 'cliente');
  const redirect = p.get('redirect_uri') || '';
  if (!cliente || !cliente.r.includes(redirect) || !redirectPermitido(config, redirect)) {
    return { fatal: 'El conector que pide acceso no está registrado o su dirección de retorno no es válida.' };
  }
  const volver = (error, descripcion) => {
    const u = new URL(redirect);
    u.searchParams.set('error', error);
    if (descripcion) u.searchParams.set('error_description', descripcion);
    if (p.get('state')) u.searchParams.set('state', p.get('state'));
    u.searchParams.set('iss', config.base);
    return Response.redirect(u.toString(), 302);
  };
  if (p.get('response_type') !== 'code') return { error: volver('unsupported_response_type') };
  if (p.get('code_challenge_method') !== 'S256' || !/^[A-Za-z0-9_-]{43,128}$/.test(p.get('code_challenge') || '')) {
    return { error: volver('invalid_request', 'Hace falta PKCE con S256.') };
  }
  const recurso = p.get('resource');
  if (recurso && ![config.recurso, config.base, config.base + '/'].includes(recurso)) {
    return { error: volver('invalid_target', 'Recurso desconocido.') };
  }
  // Otros alcances que pida el cliente (openid, offline_access…) se ignoran: el token solo sirve para `editorial`.
  return { cliente, redirect, volver };
}

function formulario(p, cliente, mensaje = '') {
  const ocultos = ['response_type', 'client_id', 'redirect_uri', 'code_challenge', 'code_challenge_method', 'state', 'resource', 'scope']
    .map(k => p.get(k) ? `<input type="hidden" name="${k}" value="${esc(p.get(k))}">` : '').join('');
  return `<h1>Ingreso editorial</h1>
<p><b>${esc(cliente.n)}</b> pide permiso para enviar material al Diario La Campaña y consultar sus recibos.</p>
<p>El material entra al circuito de corresponsales del diario (no se publica solo con esto).</p>
${mensaje ? `<p class="error">${esc(mensaje)}</p>` : ''}
<form method="post" action="authorize" autocomplete="off">${ocultos}
<label for="clave">Clave de acceso del editor</label>
<input id="clave" name="clave" type="password" required autofocus>
<button class="si" name="decision" value="si">Autorizar</button><button class="no" name="decision" value="no" formnovalidate>Cancelar</button>
</form>`;
}

async function autorizar(config, req) {
  if (req.method === 'GET') {
    const p = new URL(req.url).searchParams;
    const v = await validarPedidoAutorizacion(config, p);
    if (v.fatal) return html(400, `<h1>No se puede autorizar</h1><p class="error">${esc(v.fatal)}</p>`);
    if (v.error) return v.error;
    return html(200, formulario(p, v.cliente));
  }
  if (req.method !== 'POST') return json(405, { error: 'method_not_allowed' });
  let p;
  try { p = new URLSearchParams(await req.text()); } catch { return html(400, '<h1>Pedido inválido</h1>'); }
  const v = await validarPedidoAutorizacion(config, p);
  if (v.fatal) return html(400, `<h1>No se puede autorizar</h1><p class="error">${esc(v.fatal)}</p>`);
  if (v.error) return v.error;
  if (p.get('decision') !== 'si') return v.volver('access_denied', 'El editor canceló.');
  if (!igual(p.get('clave') || '', config.clave)) {
    await new Promise(r => setTimeout(r, 1200));           // frena a quien pruebe claves
    return html(401, formulario(p, v.cliente, 'Clave incorrecta.'));
  }
  const code = await firmar(config.secreto, { t: 'codigo', c: hex(await sha256(p.get('client_id'))).slice(0, 32),
    r: v.redirect, ch: p.get('code_challenge'), aud: config.recurso, exp: ahora() + CODE_TTL, n: crypto.randomUUID() });
  const u = new URL(v.redirect);
  u.searchParams.set('code', code);
  if (p.get('state')) u.searchParams.set('state', p.get('state'));
  u.searchParams.set('iss', config.base);
  return Response.redirect(u.toString(), 302);
}

async function emitir(config, clienteHash) {
  const access_token = await firmar(config.secreto, { t: 'acceso', aud: config.recurso, scope: SCOPE, exp: ahora() + ACCESS_TTL });
  const refresh_token = await firmar(config.secreto, { t: 'refresco', aud: config.recurso, c: clienteHash,
    k: await huellaClave(config), exp: ahora() + REFRESH_TTL, n: crypto.randomUUID() });
  return json(200, { access_token, token_type: 'Bearer', expires_in: ACCESS_TTL, refresh_token, scope: SCOPE });
}

async function token(config, req) {
  if (req.method !== 'POST') return json(405, { error: 'method_not_allowed' });
  let p;
  try { p = new URLSearchParams(await req.text()); } catch { return json(400, { error: 'invalid_request' }); }
  const cliente = await verificar(config.secreto, p.get('client_id'), 'cliente');
  if (!cliente) return json(401, { error: 'invalid_client' });
  const clienteHash = hex(await sha256(p.get('client_id'))).slice(0, 32);
  const recurso = p.get('resource');
  if (recurso && ![config.recurso, config.base, config.base + '/'].includes(recurso)) return json(400, { error: 'invalid_target' });
  if (p.get('grant_type') === 'authorization_code') {
    const c = await verificar(config.secreto, p.get('code'), 'codigo');
    const verifier = p.get('code_verifier') || '';
    if (!c || c.c !== clienteHash || c.r !== p.get('redirect_uri') || !/^[A-Za-z0-9._~-]{43,128}$/.test(verifier) ||
        !igual(b64url(await sha256(verifier)), c.ch)) return json(400, { error: 'invalid_grant' });
    return emitir(config, clienteHash);
  }
  if (p.get('grant_type') === 'refresh_token') {
    const r = await verificar(config.secreto, p.get('refresh_token'), 'refresco');
    if (!r || r.c !== clienteHash || r.aud !== config.recurso || r.k !== await huellaClave(config)) {
      return json(400, { error: 'invalid_grant' });
    }
    return emitir(config, clienteHash);
  }
  return json(400, { error: 'unsupported_grant_type' });
}

// ── herramientas ───────────────────────────────────────────────────────────────
const ARCHIVO_OPENAI = {
  type: 'object',
  properties: { download_url: { type: 'string' }, file_id: { type: 'string' }, mime_type: { type: 'string' }, file_name: { type: 'string' } },
  required: ['download_url', 'file_id'], additionalProperties: false,
};
const SEGURIDAD = [{ type: 'oauth2', scopes: [SCOPE] }];
export const HERRAMIENTAS = [
  {
    name: 'enviar_material',
    title: 'Enviar material al diario',
    description: 'Envía una nota con sus fotos, videos o audios al circuito editorial del Diario La Campaña y Radio del ' +
      'Centro (Chivilcoy). Usala SOLO cuando el editor lo pida y haya confirmado que autoriza la publicación del material. ' +
      'Adjuntá los archivos que el editor subió al chat en el orden en que deben salir (el primer visual es la portada). ' +
      'Devuelve un recibo con un id: guardalo para consultar el estado. El recibo confirma el depósito, no la publicación.',
    inputSchema: {
      type: 'object',
      properties: {
        texto: { type: 'string', description: 'El texto completo de la nota o el relato del hecho, tal como lo dio el editor.', maxLength: 30000 },
        archivos: { type: 'array', minItems: 1, maxItems: 8, items: ARCHIVO_OPENAI,
          description: 'De 1 a 8 fotos, videos o audios adjuntos en el chat, en orden. Al menos uno tiene que ser foto o video.' },
        autorizacion: { type: 'boolean', description: 'true solo si el editor confirmó en el chat que autoriza publicar el material.' },
        lugar: { type: 'string', description: 'Dónde pasó (una línea), por ejemplo «Chivilcoy».', maxLength: 200 },
        habla: { type: 'string', description: 'Si hay audios: nombre y cargo de quien habla en cada uno.', maxLength: 2000 },
        fuente_url: { type: 'string', description: 'Opcional: enlace de referencia interna (no se descarga).', maxLength: 2000 },
      },
      required: ['texto', 'archivos', 'autorizacion'],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: true },
    securitySchemes: SEGURIDAD,
    _meta: { 'openai/fileParams': ['archivos'], securitySchemes: SEGURIDAD,
      'openai/toolInvocation/invoking': 'Enviando el material al diario…', 'openai/toolInvocation/invoked': 'Material enviado' },
  },
  {
    name: 'consultar_envio',
    title: 'Consultar un envío',
    description: 'Consulta el recibo de un envío hecho con enviar_material (por su id): si el material quedó depositado, ' +
      'sigue en curso o falló. No confirma si ya se publicó.',
    inputSchema: { type: 'object', properties: { id: { type: 'string', description: 'El id (UUID) del recibo.' } },
      required: ['id'], additionalProperties: false },
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false, idempotentHint: true },
    securitySchemes: SEGURIDAD,
    _meta: { securitySchemes: SEGURIDAD, 'openai/toolInvocation/invoking': 'Consultando el recibo…',
      'openai/toolInvocation/invoked': 'Recibo consultado' },
  },
];

class ErrorHerramienta extends Error {}

export function tipoPorBytes(b) {
  const ascii = (i, j) => String.fromCharCode(...b.slice(i, j));
  if (b.length < 12) return '';
  if (b[0] === 0x89 && ascii(1, 4) === 'PNG') return 'image/png';
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return 'image/jpeg';
  if (ascii(0, 4) === 'RIFF' && ascii(8, 12) === 'WEBP') return 'image/webp';
  if (ascii(0, 4) === 'RIFF' && ascii(8, 12) === 'WAVE') return 'audio/wav';
  if (b[0] === 0x1a && b[1] === 0x45 && b[2] === 0xdf && b[3] === 0xa3) return 'video/webm';
  if (ascii(0, 4) === 'OggS') return 'audio/ogg';
  if (ascii(4, 8) === 'ftyp') {
    const marca = ascii(8, 12);
    if (marca === 'qt  ') return 'video/quicktime';
    if (marca.startsWith('M4A') || marca.startsWith('M4B')) return 'audio/mp4';
    return 'video/mp4';
  }
  if (ascii(0, 3) === 'ID3' || (b[0] === 0xff && (b[1] & 0xe0) === 0xe0)) return 'audio/mpeg';
  return '';
}

async function bajar(config, archivo, restante, http) {
  let u;
  try { u = new URL(archivo.download_url); } catch { throw new ErrorHerramienta('Un archivo adjunto no trae un enlace válido.'); }
  if (u.protocol !== 'https:' || u.username || u.password) throw new ErrorHerramienta('Un archivo adjunto no trae un enlace seguro.');
  if (config.hostsArchivos.length && !config.hostsArchivos.some(h => u.hostname === h || u.hostname.endsWith('.' + h))) {
    throw new ErrorHerramienta('Un archivo adjunto viene de un sitio no permitido.');
  }
  const r = await http(u.toString(), { redirect: 'follow', signal: AbortSignal.timeout(45000) });
  if (!r.ok || !r.body) throw new ErrorHerramienta('No se pudo bajar un archivo adjunto (probá adjuntarlo de nuevo).');
  const partes = [];
  let total = 0;
  for await (const parte of r.body) {
    total += parte.byteLength;
    if (total > restante) throw new ErrorHerramienta('Los archivos superan los 30 MB que acepta el ingreso por envío.');
    partes.push(parte);
  }
  const bytes = new Uint8Array(total);
  let i = 0;
  for (const parte of partes) { bytes.set(parte, i); i += parte.byteLength; }
  const mime = tipoPorBytes(bytes);
  if (!mime) throw new ErrorHerramienta(`«${String(archivo.file_name || 'un archivo').slice(0, 60)}» no es una foto, video o audio aceptado.`);
  return { bytes, mime };
}

async function idDeEnvio(args) {
  // El mismo envío (mismo texto y mismos archivos) da siempre el mismo id: si ChatGPT reintenta, la API lo reconoce
  // como repetido y no deposita dos veces.
  const h = await sha256(JSON.stringify([args.texto, args.lugar || '', args.habla || '', args.fuente_url || '',
    args.archivos.map(a => a.file_id)]));
  h[6] = (h[6] & 0x0f) | 0x40;
  h[8] = (h[8] & 0x3f) | 0x80;
  const x = hex(h.slice(0, 16));
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20, 32)}`;
}

function textoRecibo(r) {
  const estados = { received: 'DEPOSITADO', processing: 'EN CURSO', failed: 'FALLÓ' };
  return [`Estado: ${estados[r.estado] || r.estado || 'desconocido'}`, r.id ? `id: ${r.id}` : '',
    r.archivos ? `Archivos: ${r.archivos}` : '', r.mensaje || ''].filter(Boolean).join('\n');
}

async function llamarIngreso(config, http, req) {
  let r;
  try { r = await http(req.url, { ...req, redirect: 'manual', signal: AbortSignal.timeout(130000) }); } catch {
    return { estado: 'processing', mensaje: 'No hubo respuesta a tiempo del ingreso. Consultá el recibo con este mismo id antes de reenviar.' };
  }
  let datos = {};
  try { datos = await r.json(); } catch { /* sin cuerpo */ }
  if (r.status === 401 || r.status === 503) throw new ErrorHerramienta('El ingreso editorial no está disponible o mal configurado. Avisale al administrador.');
  if (r.status === 404) throw new ErrorHerramienta('No hay ningún envío con ese id.');
  if (r.status === 409) throw new ErrorHerramienta('Ese id ya se usó con otro contenido.');
  if (r.status === 413) throw new ErrorHerramienta('El envío supera el tamaño que acepta el ingreso (32 MB).');
  if (r.status >= 400 && r.status < 500) throw new ErrorHerramienta(`El ingreso rechazó el envío (${String(datos.error || r.status)}).`);
  return datos;
}

async function enviarMaterial(config, args, http) {
  if (args?.autorizacion !== true) throw new ErrorHerramienta('Falta la autorización expresa del editor para publicar el material.');
  const texto = typeof args.texto === 'string' ? args.texto.trim() : '';
  if (!texto) throw new ErrorHerramienta('Falta el texto de la nota.');
  const archivos = Array.isArray(args.archivos) ? args.archivos : [];
  if (!archivos.length || archivos.length > 8 || !archivos.every(a => a && typeof a.download_url === 'string' && typeof a.file_id === 'string')) {
    throw new ErrorHerramienta('Adjuntá de 1 a 8 fotos, videos o audios.');
  }
  const id = await idDeEnvio({ ...args, texto, archivos });
  const form = new FormData();
  form.set('metadata', JSON.stringify({ descripcion: texto, lugar: (args.lugar || '').trim(), habla: (args.habla || '').trim(),
    fuente_url: (args.fuente_url || '').trim(), autorizacion: 'ACEPTO' }));
  let restante = MAX_TOTAL;
  for (let i = 0; i < archivos.length; i++) {
    const { bytes, mime } = await bajar(config, archivos[i], restante, http);
    restante -= bytes.length;
    form.append('file', new File([bytes], `archivo_${String(i + 1).padStart(2, '0')}`, { type: mime }));
  }
  const datos = await llamarIngreso(config, http, { url: config.ingestUrl, method: 'POST', body: form,
    headers: { Authorization: 'Bearer ' + config.ingestToken, 'Idempotency-Key': id } });
  return { id, ...datos, id_envio: id };
}

async function consultarEnvio(config, args, http) {
  const id = String(args?.id || '');
  if (!UUID.test(id)) throw new ErrorHerramienta('El id tiene que ser el UUID del recibo.');
  const datos = await llamarIngreso(config, http, { url: `${config.ingestUrl}?id=${encodeURIComponent(id)}`, method: 'GET',
    headers: { Authorization: 'Bearer ' + config.ingestToken } });
  return { id, ...datos };
}

// ── MCP (JSON-RPC por HTTP) ────────────────────────────────────────────────────
async function rpc(config, msg, http) {
  const responder = result => ({ jsonrpc: '2.0', id: msg.id, result });
  const fallar = (code, message) => ({ jsonrpc: '2.0', id: msg.id ?? null, error: { code, message } });
  if (!msg || msg.jsonrpc !== '2.0' || typeof msg.method !== 'string') return fallar(-32600, 'Invalid Request');
  if (msg.id === undefined) return null;                                  // notificación
  switch (msg.method) {
    case 'initialize': {
      const pedido = msg.params?.protocolVersion;
      return responder({ protocolVersion: PROTOCOLOS.includes(pedido) ? pedido : PROTOCOLOS[0],
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: 'ingreso-editorial-diario-la-campana', title: 'Ingreso editorial — Diario La Campaña', version: '1.0.0' },
        instructions: 'Enviá material al diario solo cuando el editor lo pida y confirme la autorización. Guardá el id del recibo.' });
    }
    case 'ping': return responder({});
    case 'tools/list': return responder({ tools: HERRAMIENTAS });
    case 'tools/call': {
      const nombre = msg.params?.name, args = msg.params?.arguments || {};
      try {
        const r = nombre === 'enviar_material' ? await enviarMaterial(config, args, http)
          : nombre === 'consultar_envio' ? await consultarEnvio(config, args, http) : null;
        if (!r) return fallar(-32602, 'Herramienta desconocida');
        return responder({ content: [{ type: 'text', text: textoRecibo(r) }], structuredContent: r,
          isError: r.estado === 'failed' });
      } catch (e) {
        const texto = e instanceof ErrorHerramienta ? e.message : 'Falló el envío por un error interno. Probá de nuevo en unos minutos.';
        return responder({ content: [{ type: 'text', text: texto }], isError: true });
      }
    }
    default: return fallar(-32601, 'Method not found');
  }
}

async function mcp(config, req, http) {
  if (req.method === 'GET' || req.method === 'DELETE') return json(405, { error: 'method_not_allowed' }, { Allow: 'POST' });
  if (req.method !== 'POST') return json(405, { error: 'method_not_allowed' });
  const auth = req.headers.get('authorization') || '';
  const fijo = config.tokenFijo.length >= 32 && auth.startsWith('Bearer ') && igual(auth.slice(7), config.tokenFijo);
  const datos = fijo ? { aud: config.recurso, scope: SCOPE }
    : auth.startsWith('Bearer ') ? await verificar(config.secreto, auth.slice(7), 'acceso') : null;
  if (!datos || datos.aud !== config.recurso || datos.scope !== SCOPE) {
    return json(401, { error: 'invalid_token' }, { 'WWW-Authenticate': desafio(config, auth ? 'invalid_token' : '', auth ? 'Token vencido o inválido' : '') });
  }
  let cuerpo;
  try { cuerpo = await req.json(); } catch { return json(400, { jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } }); }
  const lote = Array.isArray(cuerpo);
  const salidas = [];
  for (const m of lote ? cuerpo : [cuerpo]) {
    const r = await rpc(config, m, http);
    if (r) salidas.push(r);
  }
  if (!salidas.length) return new Response(null, { status: 202 });
  return json(200, lote ? salidas : salidas[0]);
}

// ── el manejador ───────────────────────────────────────────────────────────────
export function crearApp(env, http = fetch) {
  return async (req, ruta) => {
    const config = leerConfig(env);
    const path = ruta ?? new URL(req.url).pathname;
    try {
      if (!config.listo) return json(503, { error: 'not_configured' });
      if (path === '/.well-known/oauth-protected-resource' || path === '/.well-known/oauth-protected-resource/mcp') {
        return json(200, metadatosRecurso(config));
      }
      if (path === '/.well-known/oauth-authorization-server' || path === '/.well-known/openid-configuration') {
        return json(200, metadatosAS(config));
      }
      if (path === '/register') return req.method === 'POST' ? await registrar(config, req) : json(405, { error: 'method_not_allowed' });
      if (path === '/authorize') return await autorizar(config, req);
      if (path === '/token') return await token(config, req);
      if (path === '/mcp' || path === '/mcp/') return await mcp(config, req, http);
      if (path === '/' || path === '') {
        return json(200, { servicio: 'Ingreso editorial — Diario La Campaña', mcp: config.recurso });
      }
      return json(404, { error: 'not_found' });
    } catch {
      return json(500, { error: 'server_error' });
    }
  };
}
