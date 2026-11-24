-- Irrigação Inteligente com Previsão: banco de dados.
-- Executar UMA VEZ em projeto novo. Transação: se der erro, nada fica pela metade.
begin;

-- 1) Cadastro das plantações e das faixas ideais de cada uma.
create table public.plants (
  id          text primary key check (id ~ '^[a-z0-9_-]{1,40}$'),
  nome        text not null,
  cor         text not null,                       -- cor usada no dashboard
  umidade_min double precision not null,           -- % de umidade do solo
  umidade_max double precision not null,
  temp_min    double precision not null,           -- °C
  temp_max    double precision not null,
  luz_min     double precision not null,           -- lux
  luz_max     double precision not null,
  vazao_ml_s  double precision not null,           -- vazão da bomba (ml/s)
  ordem       int not null
);

insert into public.plants values
  ('alface',     'Alface',     '#5F9A2E', 60, 80, 15, 24, 10000, 30000, 15, 1),
  ('morango',    'Morango',    '#8E3FA0', 65, 80, 15, 26, 20000, 50000, 20, 2),
  ('tomate',     'Tomate',     '#D2552E', 55, 75, 20, 30, 30000, 70000, 30, 3),
  ('manjericao', 'Manjericão', '#0A7EA4', 50, 70, 20, 30, 25000, 60000, 12, 4);

-- 2) Leituras enviadas pelo ESP32 (uma linha por plantação a cada envio).
create table public.readings (
  device_id       text not null check (device_id ~ '^[a-z0-9_-]{1,40}$'),  -- ESP32 que enviou
  plant_id        text not null references public.plants(id),
  message_id      text not null check (length(trim(message_id)) between 1 and 120),
  soil_moisture   double precision not null check (soil_moisture between 0 and 100),
  temperature     double precision not null check (temperature between -40 and 80),
  light_lux       double precision not null check (light_lux between 0 and 200000),
  pump_on         boolean not null,                -- bomba ligou durante o intervalo?
  pump_reason     text check (pump_reason in ('limite', 'previsao')),
  water_ml        double precision not null default 0 check (water_ml between 0 and 100000),
  predicted_dry_s double precision,                -- previsão do ESP32: segundos até o mínimo
  received_at     timestamptz not null default now(),
  primary key (plant_id, message_id)
);
create index readings_plant_time on public.readings (plant_id, received_at desc);

-- 3) Segurança: leitura pública (dados de laboratório), escrita só pela função ingest.
alter table public.plants   enable row level security;
alter table public.readings enable row level security;
grant usage on schema public to anon, authenticated, service_role;
revoke all on public.plants, public.readings from anon, authenticated;
grant select on public.plants, public.readings to anon, authenticated;
grant all on public.plants, public.readings to service_role;
create policy "leitura_publica_plants"   on public.plants   for select to anon, authenticated using (true);
create policy "leitura_publica_readings" on public.readings for select to anon, authenticated using (true);

-- 4) Resumo de água gasta desde o início, por plantação (usado no dashboard).
create view public.resumo_agua with (security_invoker = true) as
  select plant_id, sum(water_ml) as agua_total_ml, count(*) as leituras,
         count(*) filter (where pump_on) as leituras_com_bomba
  from public.readings group by plant_id;
grant select on public.resumo_agua to anon, authenticated, service_role;

commit;
