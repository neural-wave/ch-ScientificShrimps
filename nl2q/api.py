"""REST API of the challenge (API_specification.md):
  POST /nl-to-sparql {"text": "..."} -> 200 {sparql_query, tokens_consumed, is_compliant[, compliance_reason]}
  errors -> {"error": "..."}
Run: python -m nl2q.api   (or uvicorn nl2q.api:app --host 0.0.0.0 --port 8080)"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from . import settings as S
from .pipeline import translate
from .translation import warm_up


@asynccontextmanager
async def lifespan(app):
    warm_up()                  # BM25 indexes built and generator awake before the first request
    yield


app = FastAPI(title="Natural Language to SPARQL API", version="1.0.0", lifespan=lifespan)


class Body(BaseModel):
    text: str


@app.exception_handler(RequestValidationError)
async def bad_input(request: Request, exc: RequestValidationError):
    return JSONResponse({"error": "body must be JSON with a non-empty string field 'text'."}, status_code=400)


@app.post("/nl-to-sparql")
def nl_to_sparql(body: Body):
    if not body.text.strip():
        return JSONResponse({"error": "'text' must not be empty."}, status_code=400)
    try:
        r = translate(body.text)
    except Exception as e:  # the generator is down or answered garbage
        return JSONResponse({"error": f"translation failed: {str(e)[:300]}"}, status_code=502)
    out = {"sparql_query": r["sparql_query"], "tokens_consumed": r["tokens_consumed"], "is_compliant": r["is_compliant"]}
    if not r["is_compliant"]:
        out["compliance_reason"] = r["compliance_reason"]
    return out


@app.get("/health")
def health():
    return {"status": "ok", "prescreen": S.PRESCREEN, "model": S.LLM_MODEL}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=S.API_HOST, port=S.API_PORT)
