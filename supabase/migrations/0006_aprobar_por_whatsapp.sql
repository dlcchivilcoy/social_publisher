-- APROBAR POR WHATSAPP (2026-11-07): cada envío de corresponsal que se le avisa al editor por WhatsApp
-- (utils/editor_wa.py) y lo que tocó (corresponsales-webhook, atenderEditor).
--
-- estado: pendiente → publicar → publicada   (revisión: Publicar)
--         pendiente → descartada              (revisión: Descartar)
--         esperando → publicar | frenada      (publicación directa con freno de 10 minutos)
create table if not exists public.corresponsales_aprobaciones (
  id           text primary key,
  nombre       text not null,                 -- el video o la carpeta (lo que va en --file)
  tipo         text not null default 'video', -- 'video' o 'carpeta'
  titulo       text not null default '',
  estado       text not null default 'pendiente',
  url_aprobar  text not null default '',      -- el mismo link firmado del botón del mail
  url_corregir text not null default '',
  publicar_a   timestamptz,
  creado       timestamptz not null default now(),
  actualizado  timestamptz not null default now()
);
create index if not exists corresponsales_aprobaciones_nombre on public.corresponsales_aprobaciones (nombre, creado desc);

-- Lo que no va en el código porque el repositorio es público (p. ej. `editor_wa`, el WhatsApp del editor).
create table if not exists public.corresponsales_config (
  clave text primary key,
  valor text not null default ''
);

alter table public.corresponsales_aprobaciones enable row level security;
alter table public.corresponsales_config enable row level security;
grant all on public.corresponsales_aprobaciones to service_role;
grant all on public.corresponsales_config to service_role;

-- El número del editor se carga aparte, en el SQL Editor (no acá):
--   insert into public.corresponsales_config (clave, valor) values ('editor_wa', '549…')
--   on conflict (clave) do update set valor = excluded.valor;

-- EL RELOJ DEL FRENO: cada minuto, lo que está «esperando» y ya cumplió sus 10 minutos pasa a «publicar»
-- y se llama a su link de aprobar (el mismo del botón del mail). Así ninguna corrida de GitHub espera
-- 10 minutos trancando la fila de videos. Necesita las extensiones pg_cron y pg_net.
create extension if not exists pg_cron;
create extension if not exists pg_net;

create or replace function public.corresponsales_frenos_vencidos() returns integer
language plpgsql security definer set search_path = public as $$
declare
  f record;
  n integer := 0;
begin
  for f in
    update public.corresponsales_aprobaciones
       set estado = 'publicar', actualizado = now()
     where estado = 'esperando' and publicar_a <= now() and url_aprobar <> ''
    returning url_aprobar
  loop
    perform net.http_get(f.url_aprobar);
    n := n + 1;
  end loop;
  return n;
end $$;

select cron.unschedule('corresponsales-frenos') where exists (select 1 from cron.job where jobname = 'corresponsales-frenos');
select cron.schedule('corresponsales-frenos', '* * * * *', 'select public.corresponsales_frenos_vencidos()');
