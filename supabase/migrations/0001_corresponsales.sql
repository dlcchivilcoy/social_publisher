-- Programa de Corresponsales "Chivilcoy en Acción" — tablas del webhook de WhatsApp.
-- Se aplican con:  supabase db push   (o pegando este SQL en el editor de Supabase).

-- Sesión conversacional por número (máquina de estados del formulario).
create table if not exists public.corresponsales_sesiones (
  wa_id        text primary key,                       -- número de WhatsApp del remitente
  paso         text not null default 'nombre',         -- nombre|celular|lugar|descripcion|autorizacion
  nombre       text,
  celular      text,
  lugar        text,
  descripcion  text,
  media_id     text,                                   -- id del video en la API de WhatsApp
  perfil       text,                                   -- nombre de perfil de WhatsApp (referencia)
  creado       timestamptz not null default now(),
  actualizado  timestamptz not null default now()
);

-- Base de datos de colaboradores (un registro por número; el spec pide guardarlos).
create table if not exists public.corresponsales_colaboradores (
  wa_id         text primary key,
  nombre        text,
  celular       text,
  primera_vez   timestamptz not null default now(),
  ultima_vez    timestamptz not null default now(),
  cant_notas    integer not null default 0,            -- materiales enviados (con autorización)
  autorizacion  text                                   -- última autorización registrada (texto + fecha)
);

-- Las dos tablas solo las toca el webhook con la SERVICE ROLE KEY (saltea RLS).
-- Igual dejamos RLS activado y sin policies públicas: nadie con la anon key puede leerlas.
alter table public.corresponsales_sesiones      enable row level security;
alter table public.corresponsales_colaboradores enable row level security;

-- Permisos EXPLÍCITOS para la Data API (PostgREST). Desde el 30/10/2026 Supabase deja de
-- darlos solo a las tablas NUEVAS de `public` (las que ya existen los conservan): sin esto,
-- rearmar el proyecto desde estas migraciones dejaría la tabla respondiendo «permission
-- denied». Solo service_role, que es con la que la usan el bot, el publicador y la web; RLS
-- está activo y sin políticas, así que anon y authenticated no tienen nada que hacer acá.
grant select, insert, update, delete on table public.corresponsales_sesiones to service_role;
grant select, insert, update, delete on table public.corresponsales_colaboradores to service_role;
