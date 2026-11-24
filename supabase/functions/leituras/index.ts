// Função leituras: API REST de consulta (GET), usada pelo painel.
//   GET /functions/v1/leituras                  -> todas as plantações, última hora
//   GET /functions/v1/leituras?planta=tomate     -> só uma plantação
//   GET /functions/v1/leituras?minutos=15        -> janela do histórico (1 a 1440)
//   GET /functions/v1/leituras?limite=100        -> máximo de leituras por plantação
// Resposta: para cada plantação, a faixa ideal, a leitura mais recente,
// o histórico da janela pedida e o total de água gasta.
// Somente leitura. A chave de servidor fica no Supabase, nunca no navegador.
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
};
const reply = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status, headers: {...CORS, "Content-Type": "application/json", "Cache-Control": "no-store"},
  });
const entre = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

export async function handler(req: Request): Promise<Response> {
  if (req.method === "OPTIONS") return new Response(null, {headers: CORS});
  if (req.method !== "GET") return reply({error: "method_not_allowed"}, 405);

  const base = Deno.env.get("SUPABASE_URL");
  let key: string | undefined;
  try { key = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}").default; } catch { /* usa a legada */ }
  key ||= Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!base || !key) return reply({error: "server_not_configured"}, 500);
  const headers = {apikey: key, ...(key.startsWith("sb_secret_") ? {} : {Authorization: "Bearer " + key})};

  // Parâmetros da consulta (validados).
  const url = new URL(req.url);
  const planta = url.searchParams.get("planta");
  if (planta && !/^[a-z0-9_-]{1,40}$/.test(planta)) return reply({error: "invalid_planta"}, 400);
  const minutos = entre(Number(url.searchParams.get("minutos") || 60) || 60, 1, 1440);
  const limite = entre(Number(url.searchParams.get("limite") || 720) || 720, 1, 1000);
  const desde = new Date(Date.now() - minutos * 60_000).toISOString();

  const get = async (path: string) => {
    const res = await fetch(base + "/rest/v1/" + path, {headers, signal: AbortSignal.timeout(8000)});
    if (!res.ok) throw new Error("db " + res.status);
    return res.json();
  };

  try {
    let plants = await get("plants?select=*&order=ordem");
    if (planta) plants = plants.filter((p: any) => p.id === planta);
    if (!plants.length) return reply({error: "planta_not_found"}, 404);
    const totais = await get("resumo_agua?select=*");

    const resultado = await Promise.all(plants.map(async (p: any) => {
      const [ultima] = await get(`readings?select=*&plant_id=eq.${p.id}&order=received_at.desc&limit=1`);
      const hist = await get(
        `readings?select=*&plant_id=eq.${p.id}&received_at=gte.${desde}&order=received_at.desc&limit=${limite}`);
      const t = totais.find((x: any) => x.plant_id === p.id);
      return {
        ...p,
        unidades: {umidade_solo: "%", temperatura: "°C", luminosidade: "lux", agua: "ml"},
        ultima: ultima ?? null,
        historico: hist.reverse(),
        agua_total_ml: Number(t?.agua_total_ml ?? 0),
      };
    }));
    return reply({gerado_em: new Date().toISOString(), minutos, plantas: resultado});
  } catch (e) {
    console.error("leituras_failure", e instanceof Error ? e.message : "error");
    return reply({error: "consulta_falhou"}, 502);
  }
}
Deno.serve(handler);
