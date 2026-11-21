// Vercel: todas las rutas del conector llegan acá (vercel.json las reescribe con ?ruta=…).
import { crearApp } from '../core.mjs';

const app = crearApp(clave => process.env[clave] || '');

async function manejar(request) {
  const url = new URL(request.url);
  const ruta = url.searchParams.has('ruta') ? '/' + (url.searchParams.get('ruta') || '').replace(/^\/+/, '') : url.pathname;
  const r = ruta === '/index' ? '/' : ruta;
  let rpc = '';
  if (r.startsWith('/mcp') && request.method === 'POST') {
    try { const c = await request.clone().json(); rpc = [].concat(c).map(m => m.method + (m.params?.name ? ':' + m.params.name : '') +
      (m.params?.arguments ? ' args=' + Object.keys(m.params.arguments).join('|') + ' archivos=' +
        [].concat(m.params.arguments.archivos || []).map(x => typeof x === 'string' ? 'texto' :
          (x && x.download_url ? String(x.download_url).split(':')[0] : Object.keys(x || {}).join('+'))).join(';') : '')).join(','); } catch { rpc = '?'; }
  }
  const resp = await app(request, r);
  // Diagnóstico (sin claves ni tokens): ruta, método, resultado y qué pidió el cliente MCP.
  console.log(JSON.stringify({ ruta: r, metodo: request.method, estado: resp.status, rpc,
    auth: (request.headers.get('authorization') || '').startsWith('Bearer ') ? 'bearer' : 'no',
    ua: (request.headers.get('user-agent') || '').slice(0, 60) }));
  return resp;
}

export const GET = manejar;
export const POST = manejar;
export const DELETE = manejar;
