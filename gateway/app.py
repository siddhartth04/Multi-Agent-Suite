from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
from gateway.registry import SERVICES

app = FastAPI(title="Modular Multi-Agent Gateway", version="0.2.0")

class Request(BaseModel):
    input: str

async def call(service: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(SERVICES[service].rstrip("/") + "/run", json=payload)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        raise HTTPException(502, f"{service} unavailable: {exc}")

@app.get("/health")
async def health():
    out = {}
    async with httpx.AsyncClient(timeout=3) as client:
        for name, base in SERVICES.items():
            try:
                out[name] = (await client.get(base.rstrip("/") + "/health")).json()
            except Exception:
                out[name] = {"status": "unavailable", "url": base}
    return {"gateway": "ok", "services": out}

@app.get("/topology")
def topology():
    return {
        "modules": {
            "research": {"independent": True, "agents": ["researcher", "analyst", "reviewer"]},
            "fact_checker": {"independent": True, "agents": ["researcher", "verification"]},
            "marketing": {"independent": True, "agents": ["researcher", "strategist", "writer"]},
            "travel": {"independent": True, "agents": ["planner", "search", "booking"]},
        },
        "services": SERVICES,
    }

@app.post("/workflow/research")
async def research(req: Request):
    return await call("research", {"input": req.input})

@app.post("/workflow/fact-check")
async def fact_check(req: Request):
    return await call("fact_checker", {"input": req.input})

@app.post("/workflow/marketing")
async def marketing(req: Request):
    return await call("marketing", {"input": req.input})

@app.post("/workflow/travel")
async def travel(req: Request):
    return await call("travel", {"input": req.input})
