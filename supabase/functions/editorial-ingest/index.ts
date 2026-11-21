import { createHandler } from './core.mjs';
import { createServices } from './services.mjs';

Deno.serve(async (req: Request) => {
  const env = (key: string) => Deno.env.get(key) ?? '';
  try {
    // Servicios y token OAuth por petición: no se comparte estado entre usuarios/peticiones.
    return await createHandler({
      secret: env('EDITORIAL_INGEST_TOKEN'),
      authorName: env('EDITORIAL_AUTHOR_NAME'),
      authorPhone: env('EDITORIAL_AUTHOR_PHONE'),
    }, createServices(env))(req);
  } catch {
    return new Response(JSON.stringify({ error: 'ingest_not_configured' }), {
      status: 503, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
    });
  }
});
