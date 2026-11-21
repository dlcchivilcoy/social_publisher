// Vercel: todas las rutas del conector llegan acá (vercel.json las reescribe con ?ruta=…).
import { crearApp } from '../core.mjs';

const app = crearApp(clave => process.env[clave] || '');

async function manejar(request) {
  const url = new URL(request.url);
  const ruta = url.searchParams.has('ruta') ? '/' + (url.searchParams.get('ruta') || '').replace(/^\/+/, '') : url.pathname;
  return app(request, ruta === '/index' ? '/' : ruta);
}

export const GET = manejar;
export const POST = manejar;
export const DELETE = manejar;
