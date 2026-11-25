"""
Rate limiting (tiêu chí 7) — sliding window log trên Redis sorted set.

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State nằm trong Redis → mọi worker / instance dùng chung, còn nguyên sau khi restart agent

Thuật toán (mỗi request):
  1. ZREMRANGEBYSCORE: xoá các request cũ hơn 60 giây
  2. ZADD: ghi request hiện tại (score = timestamp)
  3. ZCARD: đếm số request trong cửa sổ (đã gồm request hiện tại)
  4. EXPIRE: key tự hết hạn khi không còn dùng
  Bốn lệnh chạy trong MULTI/EXEC (transaction) nên hai worker không thể cùng đọc
  "còn 1 suất" rồi cùng được cho qua. Nếu count > limit thì xoá lại request vừa ghi
  (request bị từ chối không chiếm suất) và trả 429.
"""
import hashlib
import math
import time
import uuid

import redis
from fastapi import HTTPException, status

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _bucket(api_key: str) -> str:
    # Lưu hash thay cho key gốc: không để lộ API key trong Redis
    return "ratelimit:" + hashlib.sha256(api_key.encode()).hexdigest()[:24]


def check_rate_limit(key: str) -> None:
    limit = settings.rate_limit_per_minute
    now = time.time()
    bucket = _bucket(key)
    member = f"{now:.6f}:{uuid.uuid4().hex}"   # duy nhất kể cả khi 2 request cùng timestamp

    try:
        pipe = r.pipeline(transaction=True)
        pipe.zremrangebyscore(bucket, 0, now - WINDOW_SECONDS)
        pipe.zadd(bucket, {member: now})
        pipe.zcard(bucket)
        pipe.expire(bucket, WINDOW_SECONDS + 1)
        _, _, count, _ = pipe.execute()
        if count <= limit:
            return
        r.zrem(bucket, member)
        oldest = r.zrange(bucket, 0, 0, withscores=True)
    except redis.RedisError as exc:
        # Không xác định được hạn mức → từ chối thay vì cho qua (fail closed)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Rate limiter unavailable, try again later") from exc

    # Retry-After: số giây tới khi request cũ nhất trong cửa sổ hết hạn
    retry_after = math.ceil(oldest[0][1] + WINDOW_SECONDS - now) if oldest else WINDOW_SECONDS
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Rate limit exceeded: {limit} requests per {WINDOW_SECONDS}s",
        headers={"Retry-After": str(max(1, retry_after))},
    )
