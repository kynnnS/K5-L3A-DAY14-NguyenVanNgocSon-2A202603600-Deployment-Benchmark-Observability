# Day 14 Lab - Mission Answers

> Lớp K5 · Môi trường: macOS 26.5 (Apple M5, arm64), Python 3.11.18 (venv), Docker Desktop 29.9.3 / Compose v5.7.1.
> Mọi output dưới đây là output thật của lệnh mình đã chạy ngày 2026-11-25. Token / API key đã được che.
> Cổng 8000 trên máy đang bị một tiến trình khác chiếm, nên các app mẫu được chạy ở cổng khác (ghi rõ ở từng lệnh).

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

Trong `01-localhost-vs-production/develop/app.py`:

1. **Hardcode secret trong code** — dòng 17–18: `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"` và `DATABASE_URL = "postgresql://admin:password123@localhost:5432/mydb"`. Push lên GitHub là lộ key và mật khẩu DB; muốn đổi key phải sửa code và deploy lại.
2. **Ghi secret ra log** — dòng 34: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")`. Khi chạy thật, log server in nguyên key (mình đã thấy dòng `[DEBUG] Using key: sk-...` trong stdout). Log thường được gửi sang hệ thống thứ ba, ai đọc log cũng thấy key.
3. **Không có config management** — dòng 21–22: `DEBUG = True`, `MAX_TOKENS = 500` cố định; không đọc biến môi trường nên dev/staging/production không thể khác nhau.
4. **`print()` thay vì logging có cấu trúc** — dòng 33, 34, 38: không có level, timestamp, không parse được bằng máy, không lọc/tìm kiếm được.
5. **Không có health check** — không có `/health` hay `/ready` (gọi `GET /health` → `404 Not Found`). Platform không biết khi nào app treo để restart, load balancer không biết khi nào nên ngừng gửi traffic.
6. **Port cố định + bind `localhost`** — dòng 51–52: `host="localhost"`, `port=8000`. Trong container, bind `localhost` thì bên ngoài không gọi vào được; Railway/Render cấp `PORT` động nên app sẽ không nhận traffic. Chứng minh thực tế trên máy mình (cổng 8000 đang bận): chạy `python app.py` → `ERROR: [Errno 48] Address already in use`, và không có cách nào đổi cổng ngoài sửa code.
7. **`reload=True` khi chạy** — dòng 53: chế độ dev theo dõi file và tự restart, tốn tài nguyên và không ổn định trong production.
8. **Không xử lý shutdown / lifecycle** — không có lifespan hay xử lý SIGTERM; không có chỗ đóng kết nối hay chờ request đang chạy.

### Exercise 1.2: Chạy basic version

Bản develop nhận `question` qua **query parameter** (lưu ý K5 số 3). Vì cổng 8000 bận, chạy bằng uvicorn ở cổng 8005 (`cd 01-localhost-vs-production/develop && uvicorn app:app --port 8005`):

```text
$ curl -i -X POST "http://localhost:8005/ask?question=Hello"
HTTP/1.1 200 OK
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ curl -i -X POST http://localhost:8005/ask -H 'Content-Type: application/json' -d '{"question":"Hello"}'
HTTP/1.1 422 Unprocessable Entity
{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}

$ curl -i http://localhost:8005/health
HTTP/1.1 404 Not Found
```

Stdout của server (key đã che):

```text
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-***REDACTED***
[DEBUG] Response: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.
```

**Quan sát:** chạy được, nhưng không production-ready: lộ secret trong log, không health check, cố định cổng (`python app.py` → `[Errno 48] Address already in use`).

### Exercise 1.3: Comparison table

Bản production chạy bằng `PORT=8006 ENVIRONMENT=staging python app.py` — **chỉ đổi biến môi trường, không sửa dòng code nào**:

```text
POST /ask  (JSON body)  → HTTP/1.1 200 OK  {"question":"Hello","answer":"Agent đang hoạt động tốt! ...","model":"gpt-4o-mini"}
GET /health             → HTTP/1.1 200 OK  {"status":"ok","uptime_seconds":0.4,"version":"1.0.0","environment":"staging",...}
GET /ready              → HTTP/1.1 200 OK  {"ready":true}
kill -TERM <pid>        → exit code 0; log: "Shutting down" → "Waiting for application shutdown." → "Application shutdown complete."
```

| Feature | Develop | Production | Why Important? |
|---------|---------|------------|----------------|
| Config | Hardcode trong code (`DEBUG = True`, port 8000, key, DB URL) | `config.py` đọc env (`os.getenv`) qua dataclass, có `.env.example`; `validate()` bắt buộc `AGENT_API_KEY` khi `ENVIRONMENT=production` | Cùng một code/image chạy được ở nhiều môi trường; đổi cấu hình không cần sửa code; secret không nằm trong git; fail fast khi thiếu config bắt buộc |
| Secrets | Key `sk-...` và mật khẩu DB trong code, còn in ra log | Đọc từ env, không log secret (chỉ log `question_length`, `client_ip`) | Lộ key = mất tiền / mất dữ liệu; secret trong lịch sử git không xoá được |
| Health check | ❌ không có (`/health` → 404) | ✅ `/health` (liveness: uptime, version) + `/ready` (readiness: 503 khi chưa sẵn sàng) | Platform biết khi nào restart container; load balancer biết khi nào ngừng route traffic vào instance |
| Logging | `print()` không cấu trúc, có cả secret | `logging` với format JSON (`{"time","level","msg"}`) | Log có cấu trúc thì máy parse/lọc/tìm kiếm được, gắn được với request |
| Host / Port | `localhost:8000` cố định, `reload=True` | `HOST=0.0.0.0`, `PORT` từ env, reload chỉ khi `DEBUG=true` | Container cần bind `0.0.0.0`; cloud cấp `PORT` động; không chạy chế độ dev ở production |
| Shutdown | Đột ngột, không lifecycle | `lifespan` (startup/shutdown) + handler SIGTERM; uvicorn ngừng nhận request mới, chờ request đang chạy | Deploy / scale down không cắt ngang request của người dùng, đóng kết nối sạch |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi được từ trình duyệt |

**Hai điều mình quan sát được khi chạy bản production (không có trong lý thuyết):**

- Dòng log `"Received SIGTERM — initiating graceful shutdown"` của `handle_sigterm` **không xuất hiện** khi kill: `signal.signal(SIGTERM, ...)` được gọi lúc import, sau đó `uvicorn.run()` đăng ký handler SIGTERM của chính nó và đè lên. Graceful shutdown vẫn đúng (exit 0, chạy lifespan shutdown) là nhờ uvicorn, không phải nhờ handler tự viết.
- Các dòng log JSON của app (`startup`, `"Agent is ready..."`, `"shutting down gracefully..."`) cũng **không xuất hiện**, chỉ thấy log text của uvicorn. Lý do: `logging.basicConfig` chạy lúc import, sau đó uvicorn áp `dictConfig` của nó cho logging. Ngoài ra format `'{"msg":"%(message)s"}'` sẽ tạo JSON hỏng nếu message chứa dấu `"` (vd `json.dumps(...)`). Ở Part 6 mình tránh cả hai bằng một `logging.Formatter` dùng `json.dumps` cho logger riêng `agent` (`propagate = False`).

---

## Part 2: Docker

### Exercise 2.1: Dockerfile questions

File `02-docker/develop/Dockerfile`:

1. **Base image:** `python:3.11` — bản Python đầy đủ trên Debian (có sẵn compiler, header, nhiều tool), nên rất nặng.
2. **Working directory:** `/app` (`WORKDIR /app`) — thư mục làm việc trong container; các lệnh `COPY`, `RUN`, `CMD` sau đó chạy tương đối với `/app`.
3. **Vì sao `COPY requirements.txt` trước:** Docker cache theo từng layer, layer bị build lại khi input của nó thay đổi và kéo theo mọi layer phía sau. Copy `requirements.txt` → `pip install` trước, rồi mới copy code: khi chỉ sửa code, layer cài thư viện (bước lâu nhất) vẫn lấy từ cache. Nếu `COPY . .` trước thì sửa 1 dòng code cũng cài lại toàn bộ thư viện.
4. **CMD vs ENTRYPOINT:** `CMD` là lệnh/tham số **mặc định**, bị thay thế khi chạy `docker run image <lệnh khác>`. `ENTRYPOINT` là chương trình **cố định** của container, tham số của `docker run` được nối vào sau nó (muốn đổi phải dùng `--entrypoint`). Dùng chung: `ENTRYPOINT` = chương trình, `CMD` = tham số mặc định. Dạng exec (`["python","app.py"]`) giúp process là PID 1 và nhận được SIGTERM.

### Exercise 2.2: Build và run

Build từ gốc repo (lưu ý K5 số 4) và chạy (map ra cổng 8007 vì 8000 bận):

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -d -p 127.0.0.1:8007:8000 my-agent:develop
$ curl -i -X POST "http://localhost:8007/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ curl http://localhost:8007/health
{"status":"ok","uptime_seconds":0.6,"container":true}
$ docker exec <container> whoami
root
```

Container develop chạy bằng **root** — một điểm yếu bảo mật mà bản production sửa.

### Exercise 2.3: Image size comparison

`02-docker/production/Dockerfile`:

- **Stage 1 (builder):** `python:3.11-slim` + cài `gcc`, `libpq-dev`, rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này có đủ công cụ build nhưng **không được deploy**.
- **Stage 2 (runtime):** `python:3.11-slim` mới tinh, tạo user non-root `appuser`, chỉ `COPY --from=builder /root/.local` (thư viện đã cài) + `main.py` + `utils/`, có `HEALTHCHECK`, chạy `uvicorn` bằng `appuser`.
- **Vì sao nhỏ hơn:** image cuối chỉ gồm các layer của stage cuối. `gcc`, header, cache apt/pip ở builder bị bỏ lại; base `slim` không có bộ tool/compiler như `python:3.11` full.

```text
$ docker build -f 02-docker/production/Dockerfile -t my-agent:advanced .
$ docker images my-agent
REPOSITORY:TAG      SIZE
my-agent:advanced   265MB
my-agent:develop    1.67GB
$ docker image inspect -f '{{.Size}}' my-agent:develop  → 1672157571
$ docker image inspect -f '{{.Size}}' my-agent:advanced → 265493156
```

- Develop: **1672 MB** (1,672,157,571 bytes)
- Production: **265 MB** (265,493,156 bytes)
- Difference: (1672157571 − 265493156) / 1672157571 = **84.1 % nhỏ hơn** (image production chỉ bằng ~15.9 % image develop)

(Agent Part 6 của mình cũng multi-stage `python:3.11-slim` + venv, script chấm đo **297 MB**.)

### Exercise 2.4: Docker Compose stack

```text
                       ┌───────────────────── network "internal" (bridge) ─────────────────────┐
  Client ──HTTP :80──▶ │  nginx (nginx:alpine)  ──proxy_pass──▶  agent:8000 (FastAPI, 2 worker) │
  (host 80/443)        │   rate limit 10 r/s,                      │            │               │
                       │   security headers                        ▼            ▼               │
                       │                                   redis:6379     qdrant:6333           │
                       │                                   (cache/session) (vector DB)          │
                       └────────────────────────────────────────────────────────────────────────┘
```

- **Services được start:** `agent`, `redis`, `qdrant`, `nginx` (4 container). Chỉ `nginx` mở cổng ra host (80, 443); `agent`, `redis`, `qdrant` chỉ có cổng nội bộ.
- **Cách giao tiếp:** cùng network `internal`, gọi nhau bằng **tên service** qua DNS nội bộ của Docker (`agent:8000`, `redis://redis:6379/0`, `http://qdrant:6333`). `agent` có `depends_on` với `condition: service_healthy` nên chỉ start khi Redis và Qdrant đã healthy.
- Chạy với tên project riêng: `touch .env.local && docker compose -p k5-day14-docker up -d --build`.

Test qua Nginx:

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
{"status":"ok","uptime_seconds":8.6,"version":"2.0.0","timestamp":"2026-11-25T08:34:16.459656"}

$ curl -i http://localhost/ask -X POST -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
X-Frame-Options: DENY
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ docker compose -p k5-day14-docker exec agent whoami
appuser
```

Response đi qua Nginx (header `Server: nginx`, `X-Frame-Options: DENY` do Nginx thêm), agent chạy bằng user non-root `appuser`, PID 1 là `uvicorn main:app --workers 2`.

---

## Part 3: Cloud Deployment

### Exercise 3.1: Railway deployment

Theo K5, deploy luôn **agent Part 6** (`06-lab-complete/`), không deploy app mẫu.

- **URL:** https://agent-production-bb3b87e3.up.railway.app
- **Platform:** Railway — project `k5-day14-agent`, 2 service: `agent` (build từ `06-lab-complete/Dockerfile` theo `railway.toml`) + `Redis` (Railway Redis). `REDIS_URL` của agent = `${{Redis.REDIS_URL}}` (host nội bộ `redis.railway.internal:6379`).
- **Biến môi trường trên Railway:** `AGENT_API_KEY` (secret ngẫu nhiên, không ghi trong repo), `REDIS_URL`, `ENVIRONMENT=production`, `RATE_LIMIT_PER_MINUTE=10`, `DAILY_BUDGET_USD=1.0`, `LOG_LEVEL=INFO`; `PORT` do Railway cấp (8080).
- **Screenshot:** [dashboard](screenshots/dashboard.png) · [service running](screenshots/running.png) · [test](screenshots/test.png)
- Chi tiết: [DEPLOYMENT.md](DEPLOYMENT.md)

4 lệnh tự kiểm ở CP6 (chỉ in mã HTTP):

```text
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     → 200
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      → 200
$ curl ... -X POST "$BASE/ask" (không có key)                   → 401
$ curl ... -X POST "$BASE/ask" -H "X-API-Key: $AGENT_API_KEY"   → 200
```

`python check_production_ready.py --public-url https://agent-production-bb3b87e3.up.railway.app` → `✅ Public URL: GET .../health → 200`, `Result: 12/12 tiêu chí đạt · 44/44 checks passed`. Rate limit trên cloud: 12 request liên tiếp → `200 ×10` rồi `429 ×2` (`retry-after: 55`).

### Exercise 3.2: So sánh `railway.toml` với `render.yaml`

So sánh hai file mình viết trong `06-lab-complete/`:

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Định dạng / phạm vi | TOML, cấu hình **một service** (service chứa file) | YAML Blueprint, mô tả **cả hạ tầng**: web service + Key Value (Redis) | Railway tạo service/DB bằng dashboard hoặc CLI (`railway add --database redis`), file chỉ nói cách build/deploy; Render Blueprint là Infrastructure-as-Code cho toàn bộ stack |
| Build | `[build] builder = "DOCKERFILE"`, `dockerfilePath = "Dockerfile"` | `runtime: docker`, `dockerfilePath: ./06-lab-complete/Dockerfile`, `dockerContext: ./06-lab-complete` | `railway up` chạy trong `06-lab-complete/` nên đường dẫn tương đối với thư mục đó; Render đọc blueprint từ repo nên đường dẫn tính từ gốc repo (code nằm ở thư mục con) |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout = 100` | `healthCheckPath: /health` | Cùng ý tưởng: platform chỉ chuyển traffic sang bản deploy mới khi `/health` trả 200 (zero-downtime deploy) |
| Restart | `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries = 3` | Render tự restart, không cấu hình trong file | Railway cho chỉnh policy trong config-as-code |
| Secret | Không có trong file; đặt bằng `railway variables` / dashboard | `AGENT_API_KEY: generateValue: true` (Render tự sinh), không ghi giá trị | Không bao giờ commit secret; Render cho khai báo "biến này là secret, hãy tự sinh" ngay trong blueprint |
| Redis | Service riêng, nối bằng biến tham chiếu `${{Redis.REDIS_URL}}` | Khai báo `type: keyvalue` + `fromService: { type: keyvalue, property: connectionString }` | Cả hai đều inject URL Redis nội bộ qua env, không hardcode host/mật khẩu |
| PORT | Railway cấp `PORT` (8080 lúc chạy thật) | Render cấp `PORT` (mặc định 10000) | Nên Dockerfile `CMD` dùng `${PORT:-8000}` — cùng một image chạy được ở cả hai nơi |
| Gói / tự deploy | Không khai báo plan trong file | `plan: free`, `autoDeploy: true` | Render deploy lại mỗi lần push nhánh; Railway mình deploy bằng `railway up` từ CLI |

Ghi chú: Railway CLI 5.75.0 báo config-as-code (`railway.toml`) sẽ bị thay bằng `.railway/railway.ts`, file cũ vẫn chạy tới 2026-12-01; mình giữ `railway.toml` vì đề bài và script chấm yêu cầu file này.

### Exercise 3.3: Cloud Run CI/CD (đọc `cloudbuild.yaml` + `service.yaml`)

Pipeline Cloud Build (`03-cloud-deployment/production-cloud-run/cloudbuild.yaml`) chạy 4 bước nối tiếp nhau bằng `waitFor`, mỗi bước là một container:

1. **test** — trong `python:3.11-slim`: `pip install -r requirements.txt pytest` rồi `pytest tests/`; test fail thì dừng toàn bộ pipeline.
2. **build** — `docker build` image, gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tái sử dụng layer.
3. **push** — đẩy mọi tag lên Container Registry.
4. **deploy** — `gcloud run deploy ai-agent` với image tag `$COMMIT_SHA` (truy vết được commit nào đang chạy, rollback dễ), region `asia-southeast1`, `min-instances=1` (tránh cold start), `max-instances=10`, 512Mi / 1 CPU, `ENVIRONMENT=production`, secret `OPENAI_API_KEY` lấy từ **Secret Manager** (không nằm trong image hay repo).

`service.yaml` là khai báo Knative tương ứng: autoscale 1–10 instance theo `containerConcurrency: 80`, `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready`, `AGENT_API_KEY`/`OPENAI_API_KEY` lấy bằng `secretKeyRef`. Một commit được push → test → build → push → deploy tự động; test fail thì không có image nào được deploy.

---

## Part 4: API Security

### Exercise 4.1-4.3: Test results

**4.1 — API key (`04-api-gateway/develop`)**, chạy `AGENT_API_KEY=secret-key-123 PORT=8008 python app.py` (lưu ý K5 số 6; cổng 8008 vì 8000 bận). Key được kiểm trong dependency `verify_api_key` (dùng `APIKeyHeader(name="X-API-Key", auto_error=False)`), gắn vào `/ask` bằng `Depends`.

```text
# Không có key
$ curl -i http://localhost:8008/ask -X POST -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

# Sai key
$ curl -i ... -H "X-API-Key: wrong-key" ...
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

# Đúng key
$ curl -i ... -H "X-API-Key: secret-key-123" ...
HTTP/1.1 200 OK
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

- **Sai key thì sao:** bản mẫu trả **403** (thiếu key mới 401). Agent Part 6 của mình trả **401** cho cả thiếu lẫn sai (đúng contract), và so sánh bằng `secrets.compare_digest` để chống timing attack.
- **Rotate key thế nào:** key đọc từ env `AGENT_API_KEY` → đổi biến trên platform (vd `railway variables --set AGENT_API_KEY=<key mới>`), platform redeploy, phát key mới cho client. Không cần sửa code. Muốn không downtime thì cho app chấp nhận đồng thời key cũ + key mới trong một khoảng chuyển tiếp rồi bỏ key cũ.

**4.2 — JWT (`04-api-gateway/production`)**, `PORT=8009 python app.py`, lấy token ở `POST /auth/token` với `student` / `demo123` (lưu ý K5 số 6):

```text
$ curl -i http://localhost:8009/auth/token -X POST -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***MASKED***"}

$ curl -i ... -d '{"username":"student","password":"nope"}'          → HTTP/1.1 401 Unauthorized {"detail":"Invalid credentials"}
$ curl -i http://localhost:8009/ask -X POST ... (không có token)         → HTTP/1.1 401 Unauthorized {"detail":"Authentication required. Include: Authorization: Bearer <token>"}
$ curl -i ... -H "Authorization: Bearer abc.def.ghi" ...                 → HTTP/1.1 403 Forbidden {"detail":"Invalid token."}
```

JWT flow (`auth.py`): `authenticate_user` kiểm username/password → `create_token` ký payload `{sub, role, iat, exp = now + 60 phút}` bằng HS256 với `JWT_SECRET` → client gửi `Authorization: Bearer <token>` → `verify_token` dùng `jwt.decode` kiểm chữ ký và hạn; hợp lệ thì lấy `username`, `role` từ payload **không cần tra database**.

**4.3 — Rate limiting:** vòng lặp 20 request bằng token của `student` (`Authorization: Bearer eyJ***MASKED***`):

```text
req 1: HTTP 200 remaining=9
req 2: HTTP 200 remaining=8
...
req 10: HTTP 200 remaining=0
req 11: HTTP 429 {"error": "Rate limit exceeded", "limit": 10, "window_seconds": 60, "retry_after_seconds": 59} x-ratelimit-remaining: 0 retry-after: 59
req 12: HTTP 429 ... retry-after: 59
...
req 20: HTTP 429 ... retry-after: 58
```

- **Algorithm:** sliding window log — mỗi user một `deque` timestamp, bỏ timestamp cũ hơn 60 s, đủ 10 thì trả 429 kèm `Retry-After`, `X-RateLimit-*`.
- **Limit:** user thường 10 req/phút (`rate_limiter_user`), admin 100 req/phút (`rate_limiter_admin`).
- **Bypass cho admin:** `app.py` chọn limiter theo `role` trong JWT (`teacher` có role `admin` → limiter 100/phút). Muốn bỏ hẳn giới hạn: không gọi `limiter.check()` khi `role == "admin"` (vẫn nên giữ một trần cao để chống lạm dụng khi token admin bị lộ).
- **Vì sao state phải ở Redis:** bản mẫu lưu `deque` trong RAM. Mình kiểm chứng: sau khi bị 429, restart app và dùng lại đúng token → `HTTP 200 remaining=9` (bộ đếm mất sạch). Chạy nhiều instance thì mỗi instance đếm riêng, user được nhân giới hạn lên N lần. Agent Part 6 của mình lưu state trong Redis: script chấm restart agent vẫn nhận 429, và trên Railway cũng đúng 10 lần 200 rồi 429.

### Exercise 4.4: Cost guard implementation

**Rate limit khác cost guard:** rate limit bảo vệ **tốc độ** (số request / phút / key, chống spam/DoS, trả 429 — chờ vài giây là được gọi lại); cost guard bảo vệ **tiền** (tổng chi phí LLM theo ngày/tháng, trả 402 — hết ngân sách thì chờ tới kỳ sau). Cần cả hai: một user gọi đúng 10 req/phút nhưng mỗi request prompt rất dài vẫn có thể đốt hết budget; ngược lại nhiều request rẻ vẫn có thể làm sập service.

Cách mình làm trong `06-lab-complete/app/cost_guard.py`:

1. **Key theo kỳ:** `cost:daily:<YYYY-MM-DD theo UTC>` trong Redis, `EXPIRE` 2 ngày → sang ngày mới tự có key mới (= reset), key cũ tự xoá.
2. **`check_budget()` gọi TRƯỚC khi gọi LLM:** đọc tổng đã tiêu; `spent >= DAILY_BUDGET_USD` → `HTTPException(402, "Daily budget exhausted")`. Redis lỗi → 503 (fail closed, không cho gọi LLM khi không kiểm được tiền).
3. **`record_cost()` gọi SAU khi LLM trả lời:** cost = `input_tokens/1000 × 0.00015 + output_tokens/1000 × 0.0006`, cộng bằng `INCRBYFLOAT` (**atomic** — nhiều worker cộng cùng lúc không mất dữ liệu) trong transaction kèm `EXPIRE`. Mock LLM không trả usage nên ước lượng token ≈ số ký tự / 4.
4. **Giới hạn đã biết:** check và record là 2 bước tách rời nên khi nhiều request cùng lọt qua check sát ngưỡng, tổng có thể vượt budget một lượng bằng chi phí các request đang chạy (vài micro-USD). Muốn chặt hơn thì "đặt trước" chi phí ước tính bằng Lua script (check + cộng atomic) rồi điều chỉnh sau.

Biến thể đúng yêu cầu Exercise 4.4 ($10/tháng/user): key `budget:<user_id>:<YYYY-MM>`, `check_budget(user_id, estimated_cost)` trả `False` nếu `current + estimated_cost > 10`, ngược lại `INCRBYFLOAT` rồi `EXPIRE` 32 ngày — cùng ý tưởng, chỉ khác phạm vi (per-user) và kỳ (tháng). Kiểm chứng của mình: script chấm đặt `DAILY_BUDGET_USD=0.000001` → `402 ở request thứ 1` (vì key ngày đã có chi phí từ lần chạy trước).

---

## Part 5: Scaling & Reliability

### Exercise 5.1-5.5: Implementation notes

**5.1 — Health vs readiness** (`05-scaling-reliability/develop`, `PORT=8011 python app.py`):

```text
GET /health → HTTP/1.1 200 OK {"status":"ok","uptime_seconds":0.9,...,"checks":{"memory":{"status":"ok","used_percent":81.9}}}
GET /ready  → HTTP/1.1 200 OK {"ready":true,"in_flight_requests":1}
```

- **Liveness (`/health`)**: "process còn sống không?" — fail thì platform **restart** container. Không được phụ thuộc dependency bên ngoài: nếu `/health` kiểm Redis, Redis chết sẽ làm mọi instance bị restart liên tục mà không giải quyết được gì.
- **Readiness (`/ready`)**: "có nên nhận traffic không?" — fail (503) thì load balancer **ngừng route** vào instance đó nhưng không restart; dùng khi đang khởi động, đang shutdown, hoặc dependency (Redis/DB) chưa sẵn sàng.
- Agent Part 6 của mình: `/health` luôn 200; `/ready` ping Redis với timeout 1 s → 503 khi Redis chết. Kiểm chứng bằng `docker compose stop redis`: `health: 200  ready: 503`.

**5.2 — Graceful shutdown:** gửi request rồi `kill -TERM` sau 0.05 s:

```text
process exit code: 0
in-flight result: {"answer":"Agent đang hoạt động tốt! ..."} HTTP 200 in 3.043630s
server log:
INFO:     Shutting down
INFO:     127.0.0.1:54116 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
... INFO 🔄 Graceful shutdown initiated...
... INFO ✅ Shutdown complete
INFO:     Finished server process [87994]
```

`Shutting down` xuất hiện **trước** dòng `POST /ask ... 200` → uvicorn ngừng nhận kết nối mới nhưng vẫn để request đang chạy hoàn thành (`timeout_graceful_shutdown=30`), sau đó mới chạy phần shutdown của lifespan và thoát với exit code 0. Trong container, uvicorn phải là **PID 1** (exec form / `exec uvicorn`) để nhận SIGTERM từ `docker stop`; nếu PID 1 là shell thì SIGTERM không tới uvicorn, sau 10 s Docker SIGKILL (exit 137). Agent Part 6: `docker stop` xong trong 0.4 s, exit code 0.

**5.3 — Stateless design:** `production/app.py` lưu session/history trong Redis (`setex session:<id>` TTL 1 giờ, giữ 20 message gần nhất). Lý do: mỗi instance có RAM riêng; nếu history nằm trong dict của process, request thứ 2 của cùng user tới instance khác sẽ không thấy history, và restart là mất. Ở Part 4 mình đã thấy đúng hiện tượng này với rate limiter trong RAM (restart → bộ đếm về 0).

**5.4 — Load balancing** (`cd 05-scaling-reliability/production && touch .env.local && docker compose -p k5-day14-scaling up -d --build --scale agent=3`, cổng **8080**, endpoint **`POST /chat`**):

```text
k5-day14-scaling-agent-1   running   8000/tcp
k5-day14-scaling-agent-2   running   8000/tcp
k5-day14-scaling-agent-3   running   8000/tcp
k5-day14-scaling-nginx-1   running   0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   running   6379/tcp
```

Nginx (`upstream agent_cluster { server agent:8000; }` + `resolver 127.0.0.11 valid=10s`) phân phối round-robin tới 3 container; header `X-Served-By` cho biết instance nào phục vụ; `proxy_next_upstream error timeout http_503` thử instance khác khi một instance lỗi.

**5.5 — Test stateless** (`python test_stateless.py`):

```text
Session ID: 6390e6d7-042f-4ce2-aad8-ce46582ef914
Request 1: [instance-531c21]   Q: What is Docker?
Request 2: [instance-132264]   Q: Why do we need containers?
Request 3: [instance-531c21]   Q: What is Kubernetes?
Request 4: [instance-4ae51a]   Q: How does load balancing work?
Request 5: [instance-132264]   Q: What is Redis used for?
Total requests: 5
Instances used: {'instance-531c21', 'instance-4ae51a', 'instance-132264'}
✅ All requests served despite different instances!
--- Conversation History ---
Total messages: 10
✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu trong **cùng một session** được 3 instance khác nhau phục vụ, nhưng history đủ 10 message (5 user + 5 assistant) vì mọi instance đọc/ghi session ở Redis. Lưu ý: script **không tự tắt instance nào** — nó chỉ chứng minh state dùng chung.

Mình làm thêm thí nghiệm thủ công cho ý "1 instance die thì traffic chuyển sang instance khác": tạo session, `docker stop k5-day14-scaling-agent-1`, rồi gửi tiếp 8 request cùng session:

```text
   4 HTTP 200 instance-132264
   4 HTTP 200 instance-531c21
history messages for session: 18
```

8/8 request vẫn 200, chia đều cho 2 instance còn lại; session còn nguyên 18 message (1 + 8 lượt hỏi đáp). Xong thì `docker compose -p k5-day14-scaling down -v`.

---

## Part 6: Final Project — output `check_production_ready.py`

Chạy bản đầy đủ (có Docker) trong `06-lab-complete/`: `python check_production_ready.py`

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Chi tiết đáng chú ý từ cùng lần chạy: image **297 MB**; `docker stop` exit code 0 trong **0.4 s**; 429 sau 5 request thành công (`RATE_LIMIT_PER_MINUTE=5`, `--workers 2`); restart agent vẫn 429 (state trong Redis); Redis mất kết nối → `/ready` 503, `/health` 200; budget 0.000001 USD → 402; SIGTERM khi đang có request → request vẫn 200, exit code 0. Thêm `--public-url https://agent-production-bb3b87e3.up.railway.app` → `12/12 · 44/44 checks passed`.

---

## AI log

> Ghi trung thực theo RULES mục 1. Công cụ: **Claude Code** (trợ lý AI chạy trong VS Code), toàn bộ trong ngày 2026-11-25.

| # | Việc nhờ AI | AI đã làm / gợi ý | Kiểm chứng đã chạy (output thật) | File liên quan |
|---|---|---|---|---|
| 1 | Audit repo, đọc đề và script chấm, lập kế hoạch CP0–CP8 | Đọc README/RULES/CHECKPOINTS/RUBRIC/script chấm, chỉ ra mâu thuẫn giữa tài liệu cũ và hướng dẫn K5, các điều kiện ngầm của script chấm (timeout Redis, `--workers 2`) | `check_production_ready.py --static-only` lúc đầu: 0/12 | — |
| 2 | Cài môi trường | Tải + cài Docker Desktop (kiểm chữ ký Docker Inc), tạo venv Python 3.11 bằng `uv` | Mình tự thêm `~/.docker/bin` vào PATH (`~/.zprofile`); `docker run --rm hello-world` → `Hello from Docker!` | — |
| 3 | **Viết code Part 6** | AI viết toàn bộ phần TODO: `app/config.py`, `auth.py`, `redis_client.py`, `rate_limiter.py` (sorted set + MULTI/EXEC), `cost_guard.py` (INCRBYFLOAT), `main.py` (JSON logging, middleware, lifespan), `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.env.example`, `railway.toml`, `render.yaml` | `python check_production_ready.py` → PASS 12/12; test qua `docker compose`: 401 / 422 / 429 + Retry-After / restart vẫn 429 / tắt Redis → `/ready` 503, log không chứa key | `06-lab-complete/` |
| 4 | Deploy Railway | AI chạy Railway CLI: tạo project, Redis, biến môi trường (key sinh ngẫu nhiên, lưu ở `.env` đã gitignore), `railway up`, tạo domain; viết `DEPLOYMENT.md` | Mình tự đăng nhập Railway; 4 lệnh curl → 200/200/401/200; `--public-url` → 12/12 | `DEPLOYMENT.md` |
| 5 | Part 7 benchmark + eval | AI chạy load test, eval, chẩn đoán điểm bão hoà ở c=100 (so latency server vs client), thêm 2 entry `KNOWLEDGE_BASE` | `eval_v2_fixed.json`: 23/23, `regressions: []`; grep golden dataset: từ khoá mới chỉ khớp 2 câu cần sửa | `develop/utils/mock_llm.py`, `production/utils/mock_llm.py`, `reports/` |
| 6 | Part 7 observability | AI sửa lỗi `ModuleNotFoundError: uvicorn` của stack (`ENV PYTHONUSERBASE` trong `production/Dockerfile`), phát hiện `docker compose run` tự scale agent lên 2 nên huỷ lần đo và chạy lại với `--no-deps`, chạy Locust, thí nghiệm alert A–E, chụp ảnh Grafana/Jaeger/Prometheus bằng Chrome headless | `alert_rules_test.yml` → SUCCESS; danh sách container sau mỗi lần Locust; log chuyển trạng thái alert (`reports/diagnostics/alert_transitions.log`) | `production/Dockerfile`, `benchmark-results/`, `screenshots/`, `BENCHMARK_REPORT.md` |
| 7 | Part 1–5 | AI chạy các app mẫu, thu output thật, soạn câu trả lời trong file này | Output dán nguyên văn từ terminal | `MISSION_ANSWERS.md` |

**Phần tôi tự làm / tự kiểm tra thêm** (tự điền, chỉ ghi việc mình thật sự làm):

- Chụp 3 ảnh deploy `screenshots/dashboard.png`, `running.png`, `test.png`: …
- Đọc lại và tự giải thích được code `06-lab-complete/app/` và các số liệu trong `BENCHMARK_REPORT.md`: …
