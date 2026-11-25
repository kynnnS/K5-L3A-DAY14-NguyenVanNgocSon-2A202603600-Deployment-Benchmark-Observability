"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis, theo ngày (UTC)
  • Tổng chi phí trong ngày ≥ DAILY_BUDGET_USD → HTTP 402

Luồng: check_budget() TRƯỚC khi gọi LLM → gọi LLM → record_cost() SAU khi gọi.
  • INCRBYFLOAT là lệnh atomic → nhiều worker cộng đồng thời không mất dữ liệu.
  • Giới hạn đã biết: check và record là hai bước tách rời, nên khi nhiều request
    cùng lọt qua check ngay sát ngưỡng, tổng có thể vượt budget một lượng bằng
    chi phí của các request đang chạy dở (vài micro-USD) — chấp nhận được với
    budget theo ngày.
"""
import logging
import math
from datetime import datetime, timezone

import redis
from fastapi import HTTPException, status

from app.config import settings
from app.redis_client import r

logger = logging.getLogger("agent")

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006
KEY_TTL_SECONDS = 2 * 24 * 3600   # giữ key 2 ngày rồi tự xoá


def _daily_key() -> str:
    return f"cost:daily:{datetime.now(timezone.utc):%Y-%m-%d}"


def estimate_tokens(text: str) -> int:
    """Mock LLM không trả usage → ước lượng ~4 ký tự / token."""
    return max(1, math.ceil(len(text) / 4))


def current_spend() -> float:
    return float(r.get(_daily_key()) or 0.0)


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    try:
        spent = current_spend()
    except redis.RedisError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Cost guard unavailable, try again later") from exc
    if spent >= settings.daily_budget_usd:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Daily budget exhausted, try again tomorrow",
        )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    cost = (input_tokens / 1000) * PRICE_INPUT_PER_1K + (output_tokens / 1000) * PRICE_OUTPUT_PER_1K
    key = _daily_key()
    try:
        pipe = r.pipeline(transaction=True)
        pipe.incrbyfloat(key, cost)
        pipe.expire(key, KEY_TTL_SECONDS)
        pipe.execute()
    except redis.RedisError:
        # Câu trả lời đã có — không bỏ đi, nhưng ghi cảnh báo để biết chi phí bị thiếu
        logger.warning("cost_record_failed", extra={"fields": {"cost_usd": cost}})
    return cost
