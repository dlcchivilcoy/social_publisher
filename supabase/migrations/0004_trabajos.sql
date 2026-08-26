-- Registro de TRABAJOS del desgrabador (la "cola" de la Fase 1 · Bloque B).
--
-- QUÉ RESUELVE: hasta ahora, si una corrida se cortaba (timeout, cuota, un hipo de red), el video
-- podía quedarse sin procesar y NADIE se enteraba: el ledger vive en el repo y no hay dónde mirar
-- qué entró, qué salió y qué falló. Esta tabla es ese lugar único.
--
-- CÓMO SE USA: `utils/trabajos.py` la escribe best-effort desde el desgrabador (empieza → 'en_proceso',
-- termina → 'hecho', se rompe → 'error', con el detalle). El vigía (`main.py --watchdog`) la lee para
-- avisar por mail lo que quedó trabado o falló varias veces. NO reemplaza al disparador rápido
-- (Apps Script cada 1 min): es la red de seguridad y el panel.
create table if not exists public.trabajos (
  clave text primary key,            -- nombre del archivo/carpeta: identifica el trabajo y DEDUPLICA
  tipo text not null,                -- 'video-diario' | 'video-radio' | 'placa' | …
  args text,                         -- el comando que lo produjo (para poder reintentarlo a mano)
  estado text not null,              -- 'en_proceso' | 'hecho' | 'error'
  intentos integer not null default 0,
  detalle text,                      -- último error (recortado)
  creado timestamptz not null default now(),
  actualizado timestamptz not null default now()
);

-- Para que el vigía encuentre rápido lo trabado/fallado.
create index if not exists trabajos_estado_idx on public.trabajos (estado, actualizado desc);

-- Solo el backend (service key) la toca: RLS prendida y sin políticas para anon/authenticated.
alter table public.trabajos enable row level security;

create or replace function public.trabajos_touch() returns trigger
language plpgsql as $$
begin
  new.actualizado := now();
  return new;
end;
$$;

drop trigger if exists trabajos_touch on public.trabajos;
create trigger trabajos_touch before insert or update on public.trabajos
  for each row execute function public.trabajos_touch();
