-- Ingreso editorial directo: recibo persistente e idempotencia por UUID del cliente.
create table if not exists public.editorial_ingresos (
  id uuid primary key,
  payload_hash text not null check (payload_hash ~ '^[a-f0-9]{64}$'),
  estado text not null default 'processing' check (estado in ('processing', 'received', 'failed')),
  folder_id text,
  folder_name text not null,
  file_count integer not null check (file_count between 1 and 8),
  total_bytes bigint not null check (total_bytes > 0),
  created_at timestamptz not null default now(),
  received_at timestamptz
);
alter table public.editorial_ingresos enable row level security;
revoke all on public.editorial_ingresos from anon, authenticated;
grant select, insert, update on public.editorial_ingresos to service_role;
