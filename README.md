# AL PRO — Finance Operating System

AI-first, multi-asset analysis platform. **Sprint 1 skeleton** — the walking
end-to-end slice of the roadmap ("Yol Haritası ve Teknik Mimari" v1.0):
canonical data model → connectors → portfolio engine → tool registry →
guarded morning briefing.

> Product boundary (SPK): AL PRO analyzes and informs. It never gives
> personalized buy/sell advice and never routes orders. The guardrails
> module enforces this in code, not just in policy.

## Architecture (modular monolith)

```
src/alpro/
  config.py          # env-driven settings (.env supported)
  core/              # db bootstrap, canonical models, formatting
  data/              # connectors: fetch→validate→normalize→store→report
    coingecko.py     #   crypto, live (free tier), fixture fallback
    evds.py          #   TCMB FX — live with EVDS_API_KEY, else fixture
    tefas.py         #   fund NAVs — live when ALPRO_TEFAS_FUNDS set
    bist_demo.py     #   labeled DEMO prices until the vendor contract (Faz 0)
  pricing/           # latest quotes + base-currency conversion
  portfolio/         # ledger-first engine: avg cost, realized/unrealized P/L
  ai/                # the "AI + Program" layer
    tools.py         #   typed tool registry (the ONLY source of numbers)
    briefing.py      #   "Günaydın Ali" pipeline (template; optional LLM polish)
    guardrails.py    #   advice filter + disclaimer + number-grounding audit
    llm.py           #   provider-agnostic adapter (Anthropic/OpenAI, optional)
  api/app.py         # FastAPI — same tools over HTTP
  __main__.py        # CLI
```

Two invariants the tests enforce:

1. **Numbers only from tools.** The LLM may rephrase, never compute. Any
   polished output containing numbers absent from the tool payload is
   rejected and the deterministic template ships instead.
2. **Demo data is always labeled.** Fixture prices carry
   `source="demo-fixture"` and the briefing must disclose DEMO sources.

## Quick start

```bash
pip install -e ".[dev]"      # or: pip install fastapi uvicorn sqlalchemy httpx python-dotenv pytest
python -m alpro init-db
python -m alpro demo         # seed the example portfolio
python -m alpro refresh      # run connectors (live where possible)
python -m alpro briefing     # print the morning briefing
python -m alpro serve        # API at http://127.0.0.1:8000/docs
pytest                       # 12 tests, fully offline
```

## Hızlı başlangıç (TR)

Sırasıyla: `init-db` (şema), `demo` (örnek portföy), `refresh` (veri çek),
`briefing` (sabah brifingi). Ağ yoksa veya anahtar tanımlı değilse sistem
**DEMO etiketli** veriyle çalışır; `.env` dosyasına anahtar eklendiğinde
aynı komutlar canlı veriye geçer. `alpro serve` ile API (Swagger arayüzü
`/docs`) açılır.

## Configuration (.env)

| Variable | Purpose | Default |
|---|---|---|
| `ALPRO_USER_NAME` | Briefing salutation | `Ali` |
| `ALPRO_BASE_CURRENCY` | Valuation currency | `TRY` |
| `DATABASE_URL` | SQLAlchemy URL (Postgres-ready) | local SQLite |
| `EVDS_API_KEY` | TCMB EVDS key → live FX | unset (fixture) |
| `ALPRO_TEFAS_FUNDS` | e.g. `AAK,TI2` → live TEFAS NAVs | unset (demo fund) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | enables LLM polish of briefing | unset (template) |
| `ALPRO_LLM_MODEL` | model override | provider default |
| `ALPRO_ALLOW_NETWORK` | `0` forces offline/fixture mode | `1` |

## Roadmap pointers (next in Sprint 1–2)

- Valuation history table → TWR/MWR returns (engine TODO)
- KAP disclosures connector + RAG store (Faz 2 prep)
- Vendor quotes for BIST delayed data (Faz 0 action — offers requested)
- Web UI (Next.js) consuming `/briefing` and `/portfolio/summary`
