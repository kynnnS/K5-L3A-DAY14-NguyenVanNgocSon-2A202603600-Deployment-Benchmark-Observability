# Deployment Information

## Public URL
https://agent-production-bb3b87e3.up.railway.app

## Platform
Railway — project `k5-day14-agent`, environment `production`, gồm 2 service:

| Service | Nguồn | Vai trò |
|---|---|---|
| `agent` | `06-lab-complete/` (agent Part 6), build bằng `Dockerfile` theo `railway.toml` | FastAPI agent, healthcheck `GET /health` |
| `Redis` | Railway Redis database | State rate limit + cost guard; agent kết nối qua mạng nội bộ `redis.railway.internal:6379` |

Deploy bằng Railway CLI, chạy trong `06-lab-complete/`:

```bash
railway init --name k5-day14-agent
railway add --database redis
railway add --service agent --variables ...   # xem mục Environment Variables
railway up --service agent --detach
railway domain --service agent
```

Log khởi động trên Railway: uvicorn chạy là PID 1 (`Started server process [1]`), lắng nghe cổng `8080` do Railway cấp qua `PORT`, log app dạng JSON (`event="startup" ... api_key_configured=true`).

## Test Commands

Đặt biến trước khi test (key lấy từ Railway Variables, không ghi trong repo):

```bash
BASE=https://agent-production-bb3b87e3.up.railway.app
AGENT_API_KEY=<key đặt trên Railway>
```

### Health Check
```bash
curl https://agent-production-bb3b87e3.up.railway.app/health
# {"status":"ok","environment":"production","uptime_s":...}

curl https://agent-production-bb3b87e3.up.railway.app/ready
# {"status":"ready","redis":"ok"}
```

### API Test (with authentication)
```bash
curl -X POST https://agent-production-bb3b87e3.up.railway.app/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":5,"output_tokens":20,"cost_usd":1.275e-05}}
```

### Bốn lệnh tự kiểm (CP6) — kết quả thực tế ngày 2026-11-25
```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Output:
```text
200
200
401
200
```

### Rate limit trên cloud (RATE_LIMIT_PER_MINUTE=10)
12 request liên tiếp cùng một key:
```text
200 200 200 200 200 200 200 200 200 200 429 429
HTTP/2 429
retry-after: 55
```

### Script chấm với URL public
```bash
cd 06-lab-complete
python check_production_ready.py --public-url https://agent-production-bb3b87e3.up.railway.app
```
```text
✅ 12. Public URL ready (Railway / Render config)
     ✅ Public URL: GET https://agent-production-bb3b87e3.up.railway.app/health → 200
  Result: 12/12 tiêu chí đạt · 44/44 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
```

## Environment Variables Set
Đặt trong tab **Variables** của service `agent` trên Railway (chỉ ghi tên, không ghi giá trị secret):

| Biến | Giá trị / nguồn |
|---|---|
| `AGENT_API_KEY` | Secret ngẫu nhiên (`secrets.token_urlsafe(32)`), chỉ lưu trên Railway và file `.env` local (đã `.gitignore`) |
| `REDIS_URL` | Tham chiếu `${{Redis.REDIS_URL}}` tới service Redis của project |
| `ENVIRONMENT` | `production` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `LOG_LEVEL` | `INFO` |
| `PORT` | Railway tự cấp (`8080`); Dockerfile `CMD` lắng nghe `${PORT:-8000}` |

## Screenshots
- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
