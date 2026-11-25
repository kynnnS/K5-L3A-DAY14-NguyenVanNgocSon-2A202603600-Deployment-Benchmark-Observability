"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • redis.from_url() chỉ tạo connection pool, KHÔNG kết nối ngay
    → app vẫn khởi động được khi Redis chưa sẵn sàng.
  • Timeout ngắn: khi Redis chết, /ready trả 503 nhanh thay vì treo request.
  • redis_healthy() dùng cho GET /ready.
"""
import redis

from app.config import settings

r = redis.Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    """True nếu PING Redis thành công; mọi lỗi kết nối/timeout → False."""
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False
