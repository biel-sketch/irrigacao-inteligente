-- Regra do EMQX (Data Integration > Rules): pega toda mensagem das 4 plantações
-- e entrega para a ação HTTP como {"topic": ..., "reading": {...}}.
SELECT topic, json_decode(payload) AS reading
FROM "irrigacao/+/telemetria"
