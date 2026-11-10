-- RELOJ DE LAS PUBLICIDADES (2026-11-10). El */15 nativo de GitHub sonaba cada 3-4 h y una historia
-- agendada a las 10:50 no salió. Ahora cada horario de la cola (state/.publicidades_programadas.json)
-- se anota acá —lo anota el editor al guardar y la nube después de cada corrida
-- (`publicidades_programadas.sincronizar_reloj`)— y este reloj, cada minuto, despierta UNA corrida
-- de GitHub cuando llega alguno, por el mismo botón de la web que usan los mails
-- (/api/aprobar-video?action=publicidades). Sin nada vencido no corre nada.
--
-- Una fila: (trabajo_id, cuando). `disparado_en` = cuándo se llamó a la web; `hecho_en` = cuándo la
-- corrida confirmó que la procesó. Si en 10 minutos no se confirmó (GitHub caído, por ejemplo), se
-- vuelve a llamar, hasta 3 veces. Repetir es inocuo: la cola sabe qué ya salió y no duplica.
create table if not exists public.publicidades_disparos (
  trabajo_id   text not null,
  cuando       timestamptz not null,
  motivo       text not null default 'alta',     -- 'alta', 'agenda' o 'baja'
  disparado_en timestamptz,
  hecho_en     timestamptz,
  intentos     integer not null default 0,
  creado       timestamptz not null default now(),
  primary key (trabajo_id, cuando)
);
create index if not exists publicidades_disparos_pendientes
  on public.publicidades_disparos (cuando) where hecho_en is null;

alter table public.publicidades_disparos enable row level security;
grant all on public.publicidades_disparos to service_role;

-- La dirección a llamar (lleva el token de los botones del mail: por eso va en la tabla de config,
-- no en el código, que es público). La carga la nube/el editor con la clave `publicidades_url`.
create extension if not exists pg_cron;
create extension if not exists pg_net;

create or replace function public.publicidades_disparos_vencidos() returns integer
language plpgsql security definer set search_path = public as $$
declare
  url text;
  n   integer;
begin
  select valor into url from public.corresponsales_config where clave = 'publicidades_url';
  if coalesce(url, '') = '' then
    return 0;
  end if;
  with vencidos as (
    update public.publicidades_disparos
       set disparado_en = now(), intentos = intentos + 1
     where hecho_en is null
       and cuando <= now()
       and intentos < 3
       and (disparado_en is null or disparado_en <= now() - interval '10 minutes')
    returning 1
  )
  select count(*) into n from vencidos;
  if n > 0 then
    perform net.http_get(url);                -- UNA corrida procesa todo lo vencido
  end if;
  -- Limpieza: lo confirmado o agotado hace más de 30 días.
  delete from public.publicidades_disparos
   where cuando < now() - interval '30 days' and (hecho_en is not null or intentos >= 3);
  return n;
end $$;

select cron.unschedule('publicidades-reloj') where exists (select 1 from cron.job where jobname = 'publicidades-reloj');
select cron.schedule('publicidades-reloj', '* * * * *', 'select public.publicidades_disparos_vencidos()');
