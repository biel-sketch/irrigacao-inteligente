-- Migração aplicada depois da criação: identifica o ESP32 que enviou cada leitura.
alter table public.readings
  add column if not exists device_id text not null default 'irrig01'
  check (device_id ~ '^[a-z0-9_-]{1,40}$');
alter table public.readings alter column device_id drop default;

-- Consulta pela API (função leituras) usa a chave de servidor.
grant select on public.resumo_agua to service_role;
