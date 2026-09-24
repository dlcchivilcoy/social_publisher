-- Almacén del token de TikTok (una fila por cuenta).
--
-- POR QUÉ hace falta: el refresh token de TikTok **ROTA EN CADA USO**. Cada corrida de GitHub
-- Actions arranca en una máquina limpia, así que si el token nuevo no se guarda en un lugar
-- COMPARTIDO, la corrida siguiente usa uno viejo, TikTok lo rechaza y hay que re-autorizar a mano.
-- Guardarlo acá (y no en el repo) también evita exponerlo: `social_publisher` es un repo PÚBLICO.
--
-- Lo leen y escriben `platforms/tiktok.py::_load_token/_save_token` con SUPABASE_SERVICE_KEY.
create table if not exists public.tiktok_token (
  id text primary key,               -- 'diario' (una sola cuenta para todo el ecosistema)
  data jsonb not null,               -- access_token, refresh_token, access_expires_at, open_id, scope
  actualizado timestamptz not null default now()
);

-- Solo el service key (backend) puede tocarla: RLS prendida y SIN políticas para anon/authenticated.
alter table public.tiktok_token enable row level security;

-- Dejar constancia de cuándo se actualizó por última vez.
create or replace function public.tiktok_token_touch() returns trigger
language plpgsql as $$
begin
  new.actualizado := now();
  return new;
end;
$$;

drop trigger if exists tiktok_token_touch on public.tiktok_token;
create trigger tiktok_token_touch before insert or update on public.tiktok_token
  for each row execute function public.tiktok_token_touch();

-- Permisos EXPLÍCITOS para la Data API (PostgREST). Desde el 30/10/2026 Supabase deja de
-- darlos solo a las tablas NUEVAS de `public` (las que ya existen los conservan): sin esto,
-- rearmar el proyecto desde estas migraciones dejaría la tabla respondiendo «permission
-- denied». Solo service_role, que es con la que la usan el bot, el publicador y la web; RLS
-- está activo y sin políticas, así que anon y authenticated no tienen nada que hacer acá.
grant select, insert, update, delete on table public.tiktok_token to service_role;
