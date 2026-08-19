-- Append ATÓMICO de media a la sesión del corresponsal.
--
-- Arregla el bug "se pierden fotos de un álbum": cuando un vecino manda varias fotos en un mismo
-- envío, Meta las entrega como webhooks CONCURRENTES. El patrón viejo (leer media_id → append en JS
-- → upsert) tenía una carrera read-modify-write: dos webhooks leían la MISMA lista y el último
-- pisaba al anterior → quedaba 1 sola foto. Este RPC hace el append en UNA sentencia SQL con lock de
-- fila (INSERT ... ON CONFLICT DO UPDATE), así los envíos concurrentes se SERIALIZAN y no se pierde
-- ninguno. Devuelve la cantidad total DESPUÉS de su propio append (1 = primer archivo → bienvenida).
create or replace function public.corresponsales_add_media(
  p_wa_id text, p_id text, p_tipo text, p_perfil text
) returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  nuevo jsonb := jsonb_build_object('id', p_id, 'tipo', coalesce(nullif(p_tipo, ''), 'video'));
  total integer;
begin
  insert into public.corresponsales_sesiones (wa_id, paso, media_id, perfil, actualizado)
    values (p_wa_id, 'hecho', jsonb_build_array(nuevo)::text, p_perfil, now())
  on conflict (wa_id) do update
    set media_id = (
          -- si la lista previa no es un array JSON válido (sesión vieja con id único), arranca de []
          (case when public.corresponsales_sesiones.media_id ~ '^\s*\['
                then public.corresponsales_sesiones.media_id::jsonb
                else '[]'::jsonb end) || nuevo
        )::text,
        actualizado = now()
  returning jsonb_array_length(nullif(media_id, '')::jsonb) into total;
  return coalesce(total, 1);
end;
$$;
