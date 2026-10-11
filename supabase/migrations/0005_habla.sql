-- Declaraciones (2026-10-10): quién habla en el audio que manda el corresponsal (nombre y cargo).
-- El webhook lo pide después del relato cuando el envío trae un audio y lo escribe en el
-- contexto.txt como «HABLA:». Correr ANTES de deployar el webhook nuevo: sin la columna, el
-- PATCH de ese paso falla y el formulario no avanza.
alter table public.corresponsales_sesiones add column if not exists habla text;
