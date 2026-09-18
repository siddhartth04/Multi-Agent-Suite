from fastapi import FastAPI
from pydantic import BaseModel
from typing import Callable, Any

def create_service(name: str, description: str, run_fn: Callable[[str], Any], input_name: str) -> FastAPI:
    app = FastAPI(title=name, description=description, version="0.1.0")
    class RunRequest(BaseModel):
        input: str
        context: str | None = None
    @app.get("/health")
    def health(): return {"status": "ok", "service": name}
    @app.get("/metadata")
    def metadata(): return {"name": name, "description": description, "input": input_name}
    @app.post("/run")
    def run(req: RunRequest):
        value = req.input if not req.context else f"{req.input}\n\nExternal context:\n{req.context}"
        return {"service": name, "result": str(run_fn(value))}
    return app
