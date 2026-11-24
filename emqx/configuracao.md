# Configuração do EMQX Cloud (sem segredos)

Broker: EMQX Serverless (TLS na porta 8883), host `sbe6bf6f.ala.us-east-1.emqxsl.com`.

## Autenticação (Access Control > Authentication)
| Usuário | Senha |
|---|---|
| `irrigacao_esp32` | definida no painel do EMQX, **não versionada** |

## Autorização (Access Control > Authorization)
| Tipo | Identificação | Tópico | Ação | Permissão |
|---|---|---|---|---|
| Username | `irrigacao_esp32` | `irrigacao/#` | Publish | Allow |
| All Users | — | `#` | Publish & Subscribe | Deny |

## Conector HTTP (Data Integration > Connectors)
| Campo | Valor |
|---|---|
| URL | `https://stfpruxggcextsaulvqi.supabase.co` |
| Enable TLS / TLS Verify | ligados |
| SNI | `stfpruxggcextsaulvqi.supabase.co` |
| CA Cert | GTS Root R4 (certificado público do Google Trust Services) |

## Regra e ação
- SQL: [`regra.sql`](regra.sql)
- Ação HTTP: método `POST`, caminho `/functions/v1/ingest`, body `${.}`
- Header `x-ingest-key`: o valor do segredo `INGEST_KEY` do Supabase (**não versionado**)
