# How to run

Two ways: **six terminals** (best for development — you see each service's logs) or **Docker** (one command).

---

## Option A — separate terminals

### Step 1. Install

Once, from the project root (`D:\modular_multi_agent`):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-ui.txt
```

`requirements-ui.txt` pulls in everything: the services, the test tools and the dashboard.

### Step 2. Set your API key

Copy the template and edit it:

```powershell
copy .env.example .env
notepad .env
```

Set two lines. Any provider [litellm](https://docs.litellm.ai/docs/providers) supports works:

```ini
MODEL=groq/openai/gpt-oss-120b
LLM_API_KEY=your_key_here
```

Other providers:

| Provider | `MODEL` |
|---|---|
| Groq | `groq/openai/gpt-oss-120b` |
| Google Gemini | `gemini/gemini-2.0-flash` |
| OpenAI | `gpt-4o-mini` |
| Anthropic | `anthropic/claude-sonnet-4-5` |
| Local Ollama | `ollama/llama3` + `LLM_API_BASE=http://127.0.0.1:11434` |

`.env` is gitignored — your key never gets committed.

### Step 3. Start the four modules

Each in **its own terminal**, from the project root. This is the point of the design: four independently deployable services.

```powershell
# Terminal 1
uvicorn modules.research.server:app --host 0.0.0.0 --port 8001

# Terminal 2
uvicorn modules.fact_checker.server:app --host 0.0.0.0 --port 8002

# Terminal 3
uvicorn modules.marketing.server:app --host 0.0.0.0 --port 8003

# Terminal 4
uvicorn modules.travel.server:app --host 0.0.0.0 --port 8004
```

Each prints JSON logs and serves `/docs`.

### Step 4. Start the gateway

```powershell
# Terminal 5
uvicorn gateway.app:app --host 0.0.0.0 --port 8000
```

### Step 5. Start the dashboard

```powershell
# Terminal 6
streamlit run ui/app.py
```

Opens at **http://localhost:8501**.

### Step 6. Check it worked

```powershell
curl http://127.0.0.1:8000/health
```

You want `"modules_reachable": 4`.

---

## Option B — Docker

```powershell
copy .env.example .env    # set MODEL and LLM_API_KEY
docker compose up --build
```

Six containers: four modules, the gateway, and the dashboard. Same ports as above.

```powershell
docker compose down       # stop
```

---

## Using it

### The dashboard (easiest)

Go to **http://localhost:8501**:

1. **Topology** — the module map, agent pipelines and dependency edges. Green rings = reachable.
2. **Run** — pick a module, type an input, press **Run**. You see each agent's output, tokens and timing.
3. **Traces** — pick a trace to see its span waterfall. A Fact Checker or Marketing run spans two services.
4. **Tokens & Cost** — per-module and per-agent token totals.
5. **Failure modes** — press a button to inject a failure and watch the telemetry survive it.

### Or call the API directly

```powershell
# Independent module
curl -X POST http://127.0.0.1:8004/run -H "Content-Type: application/json" -d "{\"input\":\"3 days in Lisbon\"}"

# Cross-module: this calls Research over HTTP first
curl -X POST http://127.0.0.1:8002/run -H "Content-Type: application/json" -d "{\"input\":\"Solid-state batteries are mass produced.\"}"

# Discovery
curl http://127.0.0.1:8000/topology
curl http://127.0.0.1:8001/metadata

# Inject a failure
curl -X POST http://127.0.0.1:8001/run -H "Content-Type: application/json" -d "{\"input\":\"test\",\"failure_mode\":\"error\"}"
```

Interactive API docs: http://127.0.0.1:8001/docs (same for 8002–8004 and 8000).

---

## Tests

No key, no network and no running services needed:

```powershell
pytest
```

202 tests, about 15 seconds.

---

## Ports

| Port | Service |
|---|---|
| 8000 | Gateway |
| 8001 | Research (researcher → analyst → reviewer) |
| 8002 | Fact Checker (fact_researcher → verification) |
| 8003 | Marketing (researcher → strategist → writer) |
| 8004 | Travel (planner → search → booking) |
| 8501 | Dashboard |

---

## Troubleshooting

**`ModuleNotFoundError`** — run commands from the project root (`D:\modular_multi_agent`), not from inside `modules/`.

**Health says `"degraded"`, `llm_configured: false`** — no key found. Check `.env` exists in the project root and has `LLM_API_KEY`.

**`RateLimitError` / HTTP 500 mid-run** — provider quota, not a bug. Groq's free tier is ~8k tokens/minute and a full four-module sweep uses ~8.5k. Wait a minute between sweeps, lower `LLM_MAX_TOKENS`, or use a paid key.

**Port already in use** — something is still running. Free them:

```powershell
Get-NetTCPConnection -LocalPort 8000,8001,8002,8003,8004,8501 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

**A module shows red in the dashboard** — that service isn't running. Check its terminal. The rest keep working; Travel never needs the others, and Fact Checker and Marketing degrade gracefully when Research is down.

**Dashboard shows "Gateway unreachable"** — start Terminal 5. The other tabs still work if the modules are up.
