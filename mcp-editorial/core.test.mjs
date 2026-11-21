import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { crearApp, HERRAMIENTAS, tipoPorBytes } from './core.mjs';
import { createHandler } from '../supabase/functions/editorial-ingest/core.mjs';

const BASE = 'https://editorial-mcp.example.test';
const INGEST = 'https://ingreso.example.test/functions/v1/editorial-ingest';
const ENV = { MCP_PUBLIC_URL: BASE, MCP_SIGNING_SECRET: 'firma-'.repeat(8), MCP_ACCESS_KEY: 'clave-del-editor-123',
  EDITORIAL_INGEST_URL: INGEST, EDITORIAL_INGEST_TOKEN: 'ingreso-'.repeat(6) };
const REDIRECT = 'https://chatgpt.com/connector_platform_oauth_redirect';
const PNG = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82]);

// La API de ingreso REAL (core del parche) con Drive y base en memoria: la prueba recorre todo el camino.
function ingresoEnMemoria() {
  const filas = new Map(), subidas = [];
  const handler = createHandler({ secret: ENV.EDITORIAL_INGEST_TOKEN, authorName: 'Editor de prueba', authorPhone: '5492346000000' }, {
    store: {
      reserve: async row => filas.has(row.id) ? { created: false, row: { ...filas.get(row.id) } } : (filas.set(row.id, { ...row }), { created: true, row }),
      get: async id => filas.get(id) ? { ...filas.get(id) } : null,
      attachFolder: async (id, folder_id) => Object.assign(filas.get(id), { folder_id }),
      received: async id => (Object.assign(filas.get(id), { estado: 'received', received_at: '2026-11-21T12:00:00Z' }), { ...filas.get(id) }),
      failed: async id => { filas.get(id).estado = 'failed'; },
    },
    drive: {
      create: async () => 'carpeta', upload: async (f, nombre, mime, bytes) => subidas.push({ nombre, mime, bytes }),
      release: async () => {}, isReleased: async () => true, discard: async () => {},
    },
  });
  return { filas, subidas, handler };
}

function entorno() {
  const ingreso = ingresoEnMemoria(), pedidos = [];
  const http = async (url, opciones = {}) => {
    pedidos.push({ url, opciones });
    if (url.startsWith('https://files.example.test/')) {
      return url.includes('html') ? new Response('<html>no</html>') : new Response(PNG);
    }
    if (url.startsWith(INGEST)) return ingreso.handler(new Request(url, opciones));
    throw new Error('red no esperada: ' + url);
  };
  return { ingreso, pedidos, app: crearApp(k => ENV[k] || '', http) };
}
const pedido = (ruta, opciones = {}) => new Request(BASE + ruta, opciones);
const form = datos => ({ method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(datos).toString() });

async function conectar(app) {
  const reg = await app(pedido('/register', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ redirect_uris: [REDIRECT], client_name: 'ChatGPT', token_endpoint_auth_method: 'none' }) }), '/register');
  assert.equal(reg.status, 201);
  const { client_id } = await reg.json();
  const verifier = randomBytes(32).toString('base64url');
  const challenge = createHash('sha256').update(verifier).digest('base64url');
  const params = { response_type: 'code', client_id, redirect_uri: REDIRECT, code_challenge: challenge,
    code_challenge_method: 'S256', state: 'estado-123', resource: BASE + '/mcp', scope: 'editorial' };
  return { client_id, verifier, params };
}

test('metadatos de OAuth: recurso, emisor, PKCE S256, registro dinámico e iss en la respuesta', async () => {
  const { app } = entorno();
  const pr = await (await app(pedido('/.well-known/oauth-protected-resource'), '/.well-known/oauth-protected-resource')).json();
  assert.equal(pr.resource, BASE + '/mcp');
  assert.deepEqual(pr.authorization_servers, [BASE]);
  const as = await (await app(pedido('/.well-known/oauth-authorization-server'), '/.well-known/oauth-authorization-server')).json();
  assert.equal(as.issuer, BASE);
  assert.deepEqual(as.code_challenge_methods_supported, ['S256']);
  assert.equal(as.registration_endpoint, BASE + '/register');
  assert.equal(as.authorization_response_iss_parameter_supported, true);
});

test('sin token, /mcp contesta 401 con el desafío que dispara el login de ChatGPT', async () => {
  const { app } = entorno();
  const r = await app(pedido('/mcp', { method: 'POST', body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }) }), '/mcp');
  assert.equal(r.status, 401);
  assert.match(r.headers.get('www-authenticate'), /resource_metadata="https:\/\/editorial-mcp\.example\.test\/\.well-known\/oauth-protected-resource"/);
});

test('el registro solo acepta los retornos de ChatGPT y Claude', async () => {
  const { app } = entorno();
  for (const malo of ['https://evil.example/cb', 'http://chatgpt.com/connector_platform_oauth_redirect', 'https://chatgpt.com/connector/oauth/../x', 'http://192.168.0.5:8080/cb', 'http://evil.example/cb']) {
    const r = await app(pedido('/register', { method: 'POST', body: JSON.stringify({ redirect_uris: [malo] }) }), '/register');
    assert.equal(r.status, 400, malo);
  }
  const ok = await app(pedido('/register', { method: 'POST', body: JSON.stringify({ redirect_uris: ['https://chatgpt.com/connector/oauth/abc_123'] }) }), '/register');
  assert.equal(ok.status, 201);
});

test('autorización completa: clave incorrecta, clave correcta, código con iss y state, canje con PKCE, refresh', async () => {
  const { app } = entorno();
  const { client_id, verifier, params } = await conectar(app);
  const pagina = await app(pedido('/authorize?' + new URLSearchParams(params)), '/authorize');
  assert.equal(pagina.status, 200);
  assert.match(pagina.headers.get('content-type'), /text\/html/);
  const html = await pagina.text();
  assert.match(html, /Clave de acceso del editor/);
  assert.doesNotMatch(html, /clave-del-editor-123/);
  const mal = await app(pedido('/authorize', form({ ...params, clave: 'otra', decision: 'si' })), '/authorize');
  assert.equal(mal.status, 401);
  const bien = await app(pedido('/authorize', form({ ...params, clave: ENV.MCP_ACCESS_KEY, decision: 'si' })), '/authorize');
  assert.equal(bien.status, 302);
  const vuelta = new URL(bien.headers.get('location'));
  assert.equal(vuelta.origin + vuelta.pathname, REDIRECT);
  assert.equal(vuelta.searchParams.get('state'), 'estado-123');
  assert.equal(vuelta.searchParams.get('iss'), BASE);
  const code = vuelta.searchParams.get('code');
  const sinPkce = await app(pedido('/token', form({ grant_type: 'authorization_code', code, client_id, redirect_uri: REDIRECT,
    code_verifier: randomBytes(32).toString('base64url'), resource: BASE + '/mcp' })), '/token');
  assert.equal(sinPkce.status, 400);
  const tok = await app(pedido('/token', form({ grant_type: 'authorization_code', code, client_id, redirect_uri: REDIRECT,
    code_verifier: verifier, resource: BASE + '/mcp' })), '/token');
  assert.equal(tok.status, 200);
  const t = await tok.json();
  assert.equal(t.token_type, 'Bearer');
  assert.ok(t.access_token && t.refresh_token);
  const ref = await app(pedido('/token', form({ grant_type: 'refresh_token', refresh_token: t.refresh_token, client_id })), '/token');
  assert.equal(ref.status, 200);
  // Otro cliente no puede usar ese refresh.
  const otro = await (await app(pedido('/register', { method: 'POST', body: JSON.stringify({ redirect_uris: [REDIRECT] }) }), '/register')).json();
  const robado = await app(pedido('/token', form({ grant_type: 'refresh_token', refresh_token: t.refresh_token, client_id: otro.client_id })), '/token');
  assert.equal(robado.status, 400);
  // Si se cambia la clave del editor, los refresh viejos dejan de valer.
  const app2 = crearApp(k => ({ ...ENV, MCP_ACCESS_KEY: 'clave-nueva-456789' })[k] || '');
  assert.equal((await app2(pedido('/token', form({ grant_type: 'refresh_token', refresh_token: t.refresh_token, client_id })), '/token')).status, 400);
  // Cancelar vuelve con access_denied.
  const cancelar = await app(pedido('/authorize', form({ ...params, decision: 'no' })), '/authorize');
  assert.equal(new URL(cancelar.headers.get('location')).searchParams.get('error'), 'access_denied');
});

async function tokenDeAcceso(app) {
  const { client_id, verifier, params } = await conectar(app);
  const r = await app(pedido('/authorize', form({ ...params, clave: ENV.MCP_ACCESS_KEY, decision: 'si' })), '/authorize');
  const code = new URL(r.headers.get('location')).searchParams.get('code');
  const t = await (await app(pedido('/token', form({ grant_type: 'authorization_code', code, client_id, redirect_uri: REDIRECT, code_verifier: verifier })), '/token')).json();
  return t.access_token;
}
const llamar = (app, token, cuerpo) => app(pedido('/mcp', { method: 'POST',
  headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
  body: JSON.stringify(cuerpo) }), '/mcp');

test('MCP: initialize, tools/list con fileParams y esquema de archivo que exige ChatGPT', async () => {
  const { app } = entorno();
  const token = await tokenDeAcceso(app);
  const ini = await (await llamar(app, token, { jsonrpc: '2.0', id: 1, method: 'initialize',
    params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'prueba', version: '1' } } })).json();
  assert.equal(ini.result.protocolVersion, '2025-06-18');
  assert.equal((await llamar(app, token, { jsonrpc: '2.0', method: 'notifications/initialized' })).status, 202);
  const lista = await (await llamar(app, token, { jsonrpc: '2.0', id: 2, method: 'tools/list' })).json();
  const enviar = lista.result.tools.find(t => t.name === 'enviar_material');
  assert.deepEqual(enviar._meta['openai/fileParams'], ['archivos']);
  const f = enviar.inputSchema.$defs.OpenAIFile;
  assert.deepEqual(Object.keys(f.properties).sort(), ['download_url', 'file_id', 'file_name', 'mime_type']);
  assert.deepEqual(f.required, ['download_url', 'file_id']);
  assert.equal(HERRAMIENTAS.find(t => t.name === 'consultar_envio').annotations.readOnlyHint, true);
});

test('enviar_material deposita en la API de ingreso real, sin exponer su token, y consultar_envio lee el recibo', async () => {
  const { app, ingreso, pedidos } = entorno();
  const token = await tokenDeAcceso(app);
  const args = { texto: 'Choque en Av. Soarez y Rivadavia, sin heridos.', lugar: 'Chivilcoy', autorizacion: true,
    archivos: [{ download_url: 'https://files.example.test/a.png', file_id: 'file_1', mime_type: 'application/octet-stream', file_name: 'a.png' }] };
  const r = await (await llamar(app, token, { jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'enviar_material', arguments: args } })).json();
  assert.equal(r.result.isError, false);
  assert.equal(r.result.structuredContent.estado, 'received');
  const id = r.result.structuredContent.id;
  assert.match(r.result.content[0].text, /DEPOSITADO/);
  assert.equal(JSON.stringify(r).includes(ENV.EDITORIAL_INGEST_TOKEN), false);
  // Lo que llegó al Drive (en memoria): contexto con el autor del SERVIDOR y la foto con sus bytes originales.
  const contexto = new TextDecoder().decode(ingreso.subidas[0].bytes);
  assert.match(contexto, /ORIGEN: corresponsal-editorial-directo/);
  assert.match(contexto, /NOMBRE: Editor de prueba/);
  assert.match(contexto, /LUGAR: Chivilcoy/);
  assert.deepEqual(ingreso.subidas.at(-1).bytes, PNG);
  assert.equal(ingreso.subidas.at(-1).mime, 'image/png');
  // El reintento del mismo envío da el mismo id y no deposita dos veces.
  const otra = await (await llamar(app, token, { jsonrpc: '2.0', id: 4, method: 'tools/call', params: { name: 'enviar_material', arguments: args } })).json();
  assert.equal(otra.result.structuredContent.id, id);
  assert.equal(ingreso.filas.size, 1);
  const pedidoIngreso = pedidos.find(p => p.url === INGEST);
  assert.equal(pedidoIngreso.opciones.headers.Authorization, 'Bearer ' + ENV.EDITORIAL_INGEST_TOKEN);
  const c = await (await llamar(app, token, { jsonrpc: '2.0', id: 5, method: 'tools/call', params: { name: 'consultar_envio', arguments: { id } } })).json();
  assert.equal(c.result.structuredContent.estado, 'received');
});

test('enviar_material rechaza sin autorización, archivos que no son foto/video/audio y enlaces inseguros', async () => {
  const { app, ingreso } = entorno();
  const token = await tokenDeAcceso(app);
  const base = { texto: 'Hecho', archivos: [{ download_url: 'https://files.example.test/a.png', file_id: 'f' }] };
  const casos = [
    { ...base, autorizacion: false },
    { ...base, autorizacion: true, archivos: [{ download_url: 'https://files.example.test/html', file_id: 'f' }] },
    { ...base, autorizacion: true, archivos: [{ download_url: 'http://files.example.test/a.png', file_id: 'f' }] },
    { ...base, autorizacion: true, archivos: [] },
  ];
  for (const args of casos) {
    const r = await (await llamar(app, token, { jsonrpc: '2.0', id: 9, method: 'tools/call', params: { name: 'enviar_material', arguments: args } })).json();
    assert.equal(r.result.isError, true, JSON.stringify(args));
  }
  assert.equal(ingreso.filas.size, 0);
});

test('un token vencido o de otro tipo no entra; sin configuración completa contesta 503', async () => {
  const { app } = entorno();
  assert.equal((await llamar(app, 'basura.firma', { jsonrpc: '2.0', id: 1, method: 'tools/list' })).status, 401);
  const reg = await (await app(pedido('/register', { method: 'POST', body: JSON.stringify({ redirect_uris: [REDIRECT] }) }), '/register')).json();
  assert.equal((await llamar(app, reg.client_id, { jsonrpc: '2.0', id: 1, method: 'tools/list' })).status, 401);
  const incompleta = crearApp(k => ({ ...ENV, MCP_ACCESS_KEY: '' })[k] || '');
  assert.equal((await incompleta(pedido('/mcp', { method: 'POST' }), '/mcp')).status, 503);
});

test('reconoce los tipos por los bytes (no por lo que diga ChatGPT)', () => {
  assert.equal(tipoPorBytes(PNG), 'image/png');
  assert.equal(tipoPorBytes(new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 0, 0, 0, 0, 0, 0, 0])), 'image/jpeg');
  const mp4 = new Uint8Array([0, 0, 0, 24, ...Buffer.from('ftypisom')]);
  assert.equal(tipoPorBytes(mp4), 'video/mp4');
  assert.equal(tipoPorBytes(new TextEncoder().encode('<html><body></body>')), '');
});

test('apps de escritorio: retorno local (127.0.0.1 con puerto) y alcances extra ignorados', async () => {
  const { app } = entorno();
  const local = 'http://127.0.0.1:53682/callback';
  const reg = await app(pedido('/register', { method: 'POST', body: JSON.stringify({ redirect_uris: [local], client_name: 'Codex' }) }), '/register');
  assert.equal(reg.status, 201);
  const { client_id } = await reg.json();
  const verifier = randomBytes(32).toString('base64url');
  const params = { response_type: 'code', client_id, redirect_uri: local, state: 's',
    code_challenge: createHash('sha256').update(verifier).digest('base64url'), code_challenge_method: 'S256',
    scope: 'openid offline_access editorial', resource: BASE };
  assert.equal((await app(pedido('/authorize?' + new URLSearchParams(params)), '/authorize')).status, 200);
  const r = await app(pedido('/authorize', form({ ...params, clave: ENV.MCP_ACCESS_KEY, decision: 'si' })), '/authorize');
  const vuelta = new URL(r.headers.get('location'));
  assert.equal(vuelta.origin, 'http://127.0.0.1:53682');
  const tok = await app(pedido('/token', form({ grant_type: 'authorization_code', code: vuelta.searchParams.get('code'),
    client_id, redirect_uri: local, code_verifier: verifier, resource: BASE })), '/token');
  assert.equal(tok.status, 200);
});
