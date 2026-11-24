// Função ingest: recebe a leitura do EMQX, valida e grava em public.readings.
// Supabase Edge Runtime (Deno). Sem dependências externas.
const reply = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {status, headers: {...CORS, "Content-Type": "application/json"}});
const CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "content-type, x-ingest-key"};

const PLANTAS = ["alface", "tomate", "morango", "manjericao"];
const num = (v: unknown, min: number, max: number) =>
  typeof v === "number" && Number.isFinite(v) && v >= min && v <= max;

export async function handler(req: Request): Promise<Response> {
  if (req.method === "OPTIONS") return new Response(null, {headers: CORS});
  if (req.method !== "POST") return reply({error: "method_not_allowed"}, 405);

  // Chaves do servidor (fornecidas pelo Supabase) e segredo compartilhado com o EMQX.
  const secret = Deno.env.get("INGEST_KEY");
  const base = Deno.env.get("SUPABASE_URL");
  let admin: string | undefined;
  try { admin = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}").default; }
  catch { return reply({error: "invalid_server_keys"}, 500); }
  admin ||= Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!secret || secret.length < 32 || !base || !admin) return reply({error: "server_not_configured"}, 500);

  // Só aceita quem conhece o segredo.
  if (req.headers.get("x-ingest-key") !== secret) return reply({error: "unauthorized"}, 401);

  const text = await req.text();
  if (text.length > 4096) return reply({error: "payload_too_large"}, 413);
  let body: any;
  try { body = JSON.parse(text); } catch { return reply({error: "invalid_json"}, 400); }

  // O EMQX envia {topic, reading}. Conferimos cada campo.
  const r = body?.reading;
  if (!r || typeof r !== "object" || Array.isArray(r)) return reply({error: "invalid_reading"}, 400);
  const ok =
    typeof r.device_id === "string" && /^[a-z0-9_-]{1,40}$/.test(r.device_id) &&
    PLANTAS.includes(r.plant_id) &&
    body.topic === `irrigacao/${r.plant_id}/telemetria` &&
    typeof r.message_id === "string" && r.message_id.trim().length > 0 && r.message_id.length <= 120 &&
    num(r.soil_moisture, 0, 100) && num(r.temperature, -40, 80) && num(r.light_lux, 0, 200000) &&
    typeof r.pump_on === "boolean" &&
    (r.pump_reason === null || r.pump_reason === undefined || ["limite", "previsao"].includes(r.pump_reason)) &&
    num(r.water_ml ?? 0, 0, 100000) &&
    (r.predicted_dry_s === null || r.predicted_dry_s === undefined || num(r.predicted_dry_s, 0, 1e7));
  if (!ok) {
    console.error("invalid_contract", text.slice(0, 600));
    return reply({error: "invalid_contract", recebido: body}, 400);
  }

  // Monta só as colunas conhecidas (received_at vem do banco).
  const row = {
    device_id: r.device_id, plant_id: r.plant_id, message_id: r.message_id,
    soil_moisture: r.soil_moisture, temperature: r.temperature, light_lux: r.light_lux,
    pump_on: r.pump_on, pump_reason: r.pump_reason ?? null,
    water_ml: r.water_ml ?? 0, predicted_dry_s: r.predicted_dry_s ?? null,
  };
  try {
    const res = await fetch(base + "/rest/v1/readings?on_conflict=plant_id,message_id", {
      method: "POST",
      headers: {
        "Content-Type": "application/json", apikey: admin,
        ...(admin.startsWith("sb_secret_") ? {} : {Authorization: "Bearer " + admin}),
        Prefer: "resolution=ignore-duplicates,return=representation",
      },
      body: JSON.stringify(row), signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) {
      console.error("database_status", res.status, await res.text());
      return reply({error: "database_error"}, 500);
    }
    const inserted = await res.json();
    return reply({ok: true, inserted: inserted.length > 0, message_id: r.message_id}, inserted.length ? 201 : 200);
  } catch (e) {
    console.error("ingest_failure", e instanceof Error ? e.name : "error");
    return reply({error: "ingest_failure"}, 500);
  }
}
Deno.serve(handler);
