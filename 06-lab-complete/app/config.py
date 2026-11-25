"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường.

  • Mỗi biến có giá trị mặc định an toàn để chạy local.
  • AGENT_API_KEY mặc định rỗng: không có key nào được hard-code trong code.
    Khi key rỗng, auth từ chối MỌI request (fail closed), không phải cho qua.
  • Danh sách biến đầy đủ: xem .env.example.
"""
import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Biến môi trường {name} phải là số nguyên, nhận {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Biến môi trường {name} phải là số, nhận {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    # default_factory: đọc env tại thời điểm tạo Settings() (lúc mỗi process/worker khởi động)
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    environment: str = field(default_factory=lambda: _env_str("ENVIRONMENT", "development"))
    agent_api_key: str = field(default_factory=lambda: _env_str("AGENT_API_KEY", ""))
    redis_url: str = field(default_factory=lambda: _env_str("REDIS_URL", "redis://localhost:6379/0"))
    rate_limit_per_minute: int = field(default_factory=lambda: _env_int("RATE_LIMIT_PER_MINUTE", 10))
    daily_budget_usd: float = field(default_factory=lambda: _env_float("DAILY_BUDGET_USD", 1.0))
    log_level: str = field(default_factory=lambda: _env_str("LOG_LEVEL", "INFO").upper())


settings = Settings()
