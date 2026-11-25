"""
Production AI Agent — Part 6 Final Project

Endpoints:
  GET  /health  liveness  — luôn 200 khi process còn chạy (không phụ thuộc Redis)
  GET  /ready   readiness — 200 khi Redis kết nối được, 503 khi không
  POST /ask     X-API-Key → rate limit → cost guard → mock LLM

Kiểm tra: python check_production_ready.py
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging (tiêu chí 10): mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", None) or {})
        if record.exc_info:
            # Stack trace nằm trong log (để debug), json.dumps escape xuống dòng → vẫn 1 dòng
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False      # không đi qua handler mặc định (dạng text) của root logger
    return log


logger = _setup_logging()
START_TIME = time.time()


# ─────────────────────────────────────────────────────────
# Lifespan + graceful shutdown (tiêu chí 11)
# Uvicorn đã tự xử lý SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong,
# rồi chạy phần sau `yield` và thoát với exit code 0. Không tự đăng ký signal handler
# để khỏi chặn mất handler của uvicorn.
# ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", extra={"fields": {
        "environment": settings.environment,
        "rate_limit_per_minute": settings.rate_limit_per_minute,
        "daily_budget_usd": settings.daily_budget_usd,
        "api_key_configured": bool(settings.agent_api_key),   # chỉ log có/không, không log giá trị
    }})
    if not settings.agent_api_key:
        logger.warning("agent_api_key_missing: every /ask request will be rejected with 401")
    yield
    logger.info("shutdown", extra={"fields": {"uptime_s": round(time.time() - START_TIME, 1)}})
    r.close()


app = FastAPI(title="Production AI Agent", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # Lỗi không lường trước: log đầy đủ, client chỉ nhận thông báo chung (không lộ stack trace)
        logger.exception("unhandled_error", extra={"fields": {"request_id": request_id,
                                                               "path": request.url.path}})
        response = JSONResponse(status_code=500,
                                content={"detail": "Internal server error", "request_id": request_id})
    response.headers["X-Request-ID"] = request_id
    # Chỉ log method/path/status — không log header (chứa X-API-Key) hay query string
    logger.info("request", extra={"fields": {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "ms": round((time.perf_counter() - start) * 1000, 1),
    }})
    return response


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be blank")
        return v


@app.get("/health")
def health():
    """Liveness: process còn sống là 200 — cố ý KHÔNG kiểm tra Redis."""
    return {"status": "ok", "environment": settings.environment,
            "uptime_s": round(time.time() - START_TIME, 1)}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi phụ thuộc (Redis) sẵn sàng."""
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"status": "not_ready", "redis": "unreachable"})
    return {"status": "ready", "redis": "ok"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)          # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()                     # 402 khi hết DAILY_BUDGET_USD
    answer = llm_ask(body.question)
    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    return {
        "question": body.question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8)},
    }
