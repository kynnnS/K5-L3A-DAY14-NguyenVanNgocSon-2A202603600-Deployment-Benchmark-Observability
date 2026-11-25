# Benchmark & Observability Report — Day 14

**Họ tên:** Nguyễn Văn Ngọc Sơn — 2A202603600
**Ngày chạy:** 2026-11-25 (giờ UTC trong báo cáo, GMT+7 = UTC+7)
**Môi trường:** MacBook (Apple M5, 24 GB RAM), macOS 26.5; Docker Desktop 29.9.3 cấp 10 CPU / 7.7 GiB; Python 3.11.18. Load generator và agent chạy chung một máy.

> Mọi con số dưới đây lấy từ file đã nộp: `reports/*.json` (load test, eval), `benchmark-results/*_stats.csv` (Locust), `reports/diagnostics/*` (số liệu chẩn đoán bổ sung, log chuyển trạng thái alert, dòng log JSON theo `trace_id`) và 4 ảnh trong `screenshots/`.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Mock LLM mất ~190 ms mỗi call (p50 đo được 190–197 ms). 500 ms chừa ~2.5 lần cho jitter + hàng đợi nhưng vẫn ở mức người dùng chat thấy "tức thì". Trùng ngưỡng mặc định của `load_test.py` nên kết quả `SLO` trong JSON so được trực tiếp. Đặt theo p95 chứ không theo avg (xem 2.2). |
| Error rate `/ask` | < 2 % | Mock LLM có ~1% lỗi upstream (`MOCK_ERROR_RATE=0.01`); 2% = error budget gấp đôi mức lỗi "bình thường" để không báo động vì dao động ngẫu nhiên. Alert production đặt lỏng hơn (5% trong 1 phút) để tránh alert fatigue. |
| Accuracy trên golden dataset | ≥ 85 % | Ngưỡng job CI ở Exercise 7.3; kèm điều kiện **không regression** so với baseline. Bản v2 sau khi sửa đạt 100% nên lần chạy cuối dùng `--min-accuracy 1.0`. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

Lệnh (trong `06-lab-complete/benchmark-observability/develop/`, agent v2 ở cổng 8002, `MOCK_SLOW_RATE` mặc định 0.03):
`python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json`

### 2.1 Concurrency vs latency (agent v2)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.14 | 195.2 | 221.1 | 1709.9 | 0.67% | ✅ PASS |
| 10 | 39.62 | 190.6 | 220.2 | 1702.9 | 0.00% | ✅ PASS |
| 50 | 129.7 | 197.0 | 263.7 | 310.3 | 0.67% | ✅ PASS |
| 100 | 65.18 | 308.7 | 2662.6 | 3481.8 | 1.67% | ❌ FAIL (p95) |

Nguồn: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`. Lệnh c=100 trả exit code 1 (vi phạm SLO p95) nhưng vẫn lưu report — giữ nguyên, không chạy lại để "đẹp" số.

**Nhận xét:** RPS **tăng gần tuyến tính tới khi chạm điểm bão hoà, sau đó giảm**.

- 1 → 10: RPS tăng 9.6 lần (4.14 → 39.62) trong khi p50 gần như không đổi (~190 ms). Lý do: mock LLM là `asyncio.sleep`, agent không tốn CPU khi "chờ LLM", nên 10 request song song chỉ là 10 lần chờ chồng lên nhau. Theo Little's law, RPS ≈ concurrency / latency = 10 / 0.24 s ≈ 42, khớp số đo.
- 10 → 50: RPS chỉ tăng 3.3 lần (39.62 → 129.7) thay vì 5 lần, p95 bắt đầu tăng (220 → 264 ms) — dấu hiệu request bắt đầu phải chờ ở đâu đó trong hệ thống (client, kết nối hoặc server).
- 50 → 100: **RPS giảm còn 65.18**, p50 lên 309 ms, p95 lên 2663 ms → vượt điểm bão hoà. Mình chạy lại c=100 hai lần nữa (`reports/diagnostics/load_v2_c100_rerun.json`: 65.21 req/s, p95 3018 ms; `load_v2_c100_diag.json`: 67.01 req/s, p95 2654 ms) → hiện tượng lặp lại, không phải ngẫu nhiên.
- **Phần lớn latency tăng thêm nằm ngoài phần xử lý của app (đã đo):** trong lần chạy chẩn đoán, histogram phía server (`reports/diagnostics/c100_server_side_metrics.txt`, chênh lệch 2 lần chụp `/metrics`) cho 301 request, avg = (414.04 − 341.00) / 301 = **242.6 ms** và 290/301 = **96.3 %** request xử lý xong ≤ 300 ms; trong khi client đo avg **850.3 ms** (`reports/diagnostics/load_v2_c100_diag.json`). CPU của process agent lúc đó chỉ 0.1–3.5 %. Histogram của app chỉ đo từ lúc middleware nhận request tới lúc trả response, nên **~600 ms chênh lệch là overhead nằm ngoài phần app đo được** — không phải code agent hay mock LLM chậm đi.
- **Nguyên nhân cụ thể là giả thuyết (chưa đo trực tiếp):** ứng viên gồm (a) hàng đợi kết nối/accept TCP khi 100 kết nối mở cùng lúc vào một process uvicorn; (b) thời gian thiết lập kết nối; (c) chính load generator (`httpx` asyncio, một process) chạy chung máy với agent bị chậm khi xử lý 100 coroutine. Muốn xác nhận cần đo thêm — vd chạy load generator trên máy khác, đo thời gian connect bằng `curl -w '%{time_connect}'`, hoặc chạy uvicorn nhiều worker rồi so sánh. Vì vậy mình không kết luận chắc chắn "scale ngang sẽ sửa được" cho trường hợp này.
- Ghi chú thống kê: p99 ở c=50 thấp bất thường (310 ms) dù slow rate 3%. Với ~300 mẫu thành công, p99 (nội suy tuyến tính) chỉ phụ thuộc vào ~3 request chậm nhất; lần chạy này rất ít request rơi vào nhánh "slow" (max = 1681.9 ms cho thấy có ít nhất một). **Percentile phụ thuộc số lượng mẫu**: p99 trên 300 mẫu dao động mạnh giữa các lần chạy, nên mọi con số p99 trong bảng là của **một** lần chạy, không phải giá trị ổn định — muốn so sánh tin cậy cần nhiều request hơn hoặc lặp lại nhiều lần.

### 2.2 Tail latency

Khởi động lại agent v2 với từng giá trị `MOCK_SLOW_RATE`, mỗi lần `--requests 300 --concurrency 20`:

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 189.6 | 217.8 | 224.8 |
| 0.03 (mặc định) | 249.7 | 220.1 | 1705.0 |
| 0.1 | 346.8 | 1692.1 | 1707.7 |

Nguồn: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json` (p50 giữ ~190 ms ở cả ba lần: 191.2 / 191.4 / 189.7 ms; slow 0.1 vi phạm SLO p95, exit code 1).

**Nhận xét:**

- **p99 nhạy nhất khi tỉ lệ chậm nhỏ:** từ 0 → 0.03, p99 nhảy 224.8 → 1705.0 ms (×7.6) trong khi avg chỉ tăng 60 ms (189.6 → 249.7) và p95 gần như đứng yên (217.8 → 220.1). 3% request chậm < 5% nên chưa "chạm" tới p95, nhưng nằm trong 1% chậm nhất → p99 bắt được.
- **Khi > 5% request chậm thì p95 cũng vỡ:** slow 0.1 → p95 = 1692.1 ms (×7.8), avg mới tăng 157 ms so với baseline (346.8 vs 189.6).
- **Vì sao không đặt SLO theo average:** average **có thể che khuất** tail latency — 3% người dùng chờ ~1.7 s bị "pha loãng" bởi 97% người dùng nhanh, avg 249.7 ms trông vẫn ổn. SLO p95/p99 nói thẳng "x% người dùng chờ lâu hơn ngưỡng" — đúng trải nghiệm của nhóm bị chậm. Đổi lại, percentile cần đủ mẫu mới ổn định (ở đây mỗi cấu hình chỉ 300 request, xem ghi chú thống kê ở 2.1), nên trong production SLO được đo trên cửa sổ đủ dài (vd 28–30 ngày) với rất nhiều request.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | 100.0% (23/23) |
| Avg tokens / câu | 57.6 | 109.1 | 115.9 |
| Tổng cost (USD) | 0.000411 | 0.000762 | 0.000810 |
| Cost / câu đúng (USD) | 0.00041055 | 0.00003627 | 0.00003521 |
| Quality gate | FAIL (accuracy < 80%) | PASS (≥ 80%, 0 regression so với v1) | PASS (≥ 100%, 0 regression so với v2) |

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json` (chạy với `--baseline reports/eval_v1.json`), `reports/eval_v2_fixed.json` (chạy với `--min-accuracy 1.0 --baseline reports/eval_v2.json`, 2 câu cải thiện, 0 regression).

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | `KNOWLEDGE_BASE` không có tài liệu nào khớp → retrieval rỗng → trả câu chung chung. Thêm entry từ khoá `["jwt", "api key"]` mô tả API key là chuỗi bí mật cố định (bản thân chuỗi không mang thời hạn), còn JWT là token có chữ ký số + thời điểm hết hạn (`exp`). Lưu ý chính xác hơn: API key vẫn **có thể được server cấu hình hết hạn hoặc bị thu hồi** (lưu metadata phía server); điểm khác là JWT tự chứa hạn và chữ ký nên xác thực được mà không tra cứu. Mình giữ nguyên text trong `KNOWLEDGE_BASE` vì `eval_v2_fixed.json` được sinh từ đúng text đó. |
| `scaling-03` ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Thêm entry từ khoá `["hpa", "kubernetes", "autoscal"]` mô tả HPA scale theo CPU, memory hoặc custom metrics. |

Chỉ sửa `develop/utils/mock_llm.py` (và chép sang `production/utils/mock_llm.py` theo lưu ý K5 số 10), **không sửa** `golden_dataset.jsonl`. Trước khi thêm, mình grep golden dataset: các từ khoá mới chỉ xuất hiện trong đúng 2 câu cần sửa → không "cướp" retrieval của câu khác, xác nhận bằng `regressions: []` trong `eval_v2_fixed.json`.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chất lượng:** v2 (có retrieval) đúng 21/23 so với 1/23 của v1 → +87 điểm %.
- **Chi phí mỗi câu:** v2 tốn 109.1 token/câu so với 57.6 (×1.9) vì prompt mang thêm context retrieve được → tổng cost tăng ×1.85 (0.000411 → 0.000762 USD).
- **Chi phí mỗi câu đúng:** v2 rẻ hơn **11.3 lần** (0.00041055 / 0.00003627). Đây mới là chỉ số nên dùng để so: v1 rẻ mỗi request nhưng gần như toàn trả lời sai, tiền bỏ ra không mua được câu trả lời nào dùng được.
- **Latency:** bước retrieval làm p95 eval tăng 190.1 → 223.0 ms (+32.9 ms, ghi trong `latency_ms` của hai report) — vẫn dưới SLO 500 ms.
- Sửa KB tăng thêm 6.8 token/câu (115.9) nhưng cost/câu đúng còn giảm nhẹ (0.00003521) vì thêm 2 câu đúng.
- **Giới hạn của 23/23:** accuracy 100% chỉ phản ánh kết quả trên **golden dataset 23 câu hiện tại** với cách chấm theo keyword (≥ 60% keyword). Hai entry KB được viết riêng cho đúng 2 câu đang sai, nên đây là "sửa để qua bộ test đã biết" — **không chứng minh** agent trả lời đúng câu hỏi mới hay cách diễn đạt khác. Muốn đánh giá khả năng tổng quát cần thêm câu hỏi chưa từng dùng khi sửa (held-out set), paraphrase, và cách chấm theo ngữ nghĩa (semantic similarity / LLM-as-judge).

**Quality gate trong CI (Exercise 7.3):** `run_eval.py` trả **exit code 1** khi accuracy < `--min-accuracy` hoặc có câu regression so với `--baseline`, exit code 0 khi đạt. Kiểm chứng: chạy eval v1 với `--baseline reports/eval_v2.json` → in `Câu bị regression : 20` và `Quality gate ... ❌ FAIL`, **exit code = 1**. Trong GitHub Actions, một step `run:` trả exit code khác 0 làm job fail; đặt job này là *required status check* của branch protection thì PR không merge được khi chất lượng giảm. Job tối thiểu:

```yaml
# .github/workflows/eval.yml (mô tả — không cần thiết để chạy lab)
on: [pull_request]
jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - working-directory: 06-lab-complete/benchmark-observability/develop
        run: |
          pip install -r requirements.txt
          AGENT_VERSION=v2 MOCK_ERROR_RATE=0 PORT=8000 python app.py &
          sleep 3
          python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json
```

`MOCK_ERROR_RATE=0` trong CI để lỗi upstream ngẫu nhiên không làm gate fail oan; baseline là report của bản đang chạy production để bắt regression từng câu.

---

## 4. Observability stack (production)

Stack chạy bằng `docker compose -p k5-day14-observability up -d --build --scale agent=1` trong `06-lab-complete/benchmark-observability/production/`. Unit test alert rules: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

> **Sự cố khi bật stack (đã sửa, ghi lại để minh bạch):** agent crash liên tục với `ModuleNotFoundError: No module named 'uvicorn'`. Nguyên nhân: `Dockerfile` cài package bằng `pip install --user` rồi copy vào `/home/agent/.local`, nhưng user được tạo với `useradd -d /app` nên Python tìm user site-packages ở `/app/.local`. Sửa một dòng `ENV PYTHONUSERBASE=/home/agent/.local` trong `production/Dockerfile` (không đụng app, metric hay alert rule).

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải → [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png) — chụp 08:17Z, giữa lần Locust 100 user với 1 instance. Panel: Traffic 51.8 req/s, Error rate 1.33%, Latency p95 293.12 ms, In-flight 11, Instances up 1.
- [x] Một trace chậm trong Jaeger → [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`) → [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing** → [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png) — chụp 08:23:34Z, **3 alert cùng firing**: `LLMCostBurnRateHigh`, `AgentHighErrorRate`, `AgentInstanceDown`.

Ảnh được chụp bằng Chrome headless trỏ vào chính các trang Grafana / Jaeger / Prometheus của stack đang chạy (không chỉnh sửa ảnh). Trên panel "Requests/s theo instance" của ảnh Grafana có một đường `172.18.0.8` ngắn trước 15:16: đó là instance thứ hai bị `docker compose run locust` tự bật lên trong lần chạy đầu (xem mục 5, ghi chú phương pháp); lần đo hợp lệ chỉ có `172.18.0.7`.

**Trả lời Exercise 7.4** (truy vấn Prometheus lúc 08:17:07Z, lưu ở `reports/diagnostics/prometheus_queries_scale1.txt`):

1. **p95 và avg:** p95 = 292.7 ms, avg = 228.8 ms. Khoảng cách nhỏ (~64 ms) vì chỉ ~3% request chậm (< 5% nên chưa đẩy p95 lên); nhưng panel p50/p95/p99 cho thấy p99 nằm ở ~1.8 s, tách hẳn khỏi p95 → tail latency tồn tại, avg không phản ánh. Khoảng cách avg ↔ p95/p99 càng lớn thì phân phối càng lệch, càng có nhóm người dùng bị chậm mà avg che đi.
2. **Nginx có chia đều không:** có. Sau khi scale lên 3 instance, 9 request liên tiếp qua Nginx cho header `X-Served-By` chia đúng 3/3/3 cho `172.18.0.7`, `.8`, `.9` (round-robin). (Mẫu 9 request là nhỏ; mình không đo phân bổ theo instance trong lần chạy scale_3.)
3. **Input tokens/s > output tokens/s:** 4797 vs 2530 tokens/s. Prompt gửi LLM gồm system prompt + đoạn tài liệu retrieve được (agent v2) + câu hỏi, còn câu trả lời chỉ là một đoạn ngắn → input luôn dài hơn output. Đây cũng là lý do RAG tăng chi phí (input token là phần tăng).
4. **`curl http://localhost:8080/metrics` → `404`.** Nginx chặn `/metrics` ở cổng public: metric lộ thông tin nội bộ (số request, chi phí, version) và có thể bị gọi dồn dập. Prometheus scrape trực tiếp từng agent qua mạng nội bộ Docker (`agent:8000`, DNS service discovery), không đi qua Nginx.

### 4.2 Thí nghiệm "phá hệ thống"

Tải nền: Locust headless 30 user suốt thí nghiệm, 3 instance agent. Đổi cấu hình qua file `.env` (đã `.gitignore`) rồi `docker compose -p k5-day14-observability up -d --scale agent=3 --no-deps agent`. Một script đọc `/api/v1/alerts` mỗi 2 giây và ghi lại mọi lần đổi trạng thái kèm giờ UTC: `reports/diagnostics/alert_transitions.log` (dòng đầu `08:20:46Z LLMCostBurnRateHigh: inactive -> firing` là trạng thái lúc script bắt đầu chạy, không phải một lần chuyển).

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` (08:20:59Z) | `AgentHighErrorRate` | **85 s** (pending sau 25 s, firing 08:22:24Z sau `for: 1m`) | Error rate (Golden Signals) + "LLM calls theo kết quả" (error tăng) |
| B | `MOCK_SLOW_RATE=0.3` (08:29:51Z) | `AgentHighLatencyP95` | **141 s** (pending sau 23 s, firing 08:32:12Z sau `for: 2m`). Lúc firing: p50 230 ms, avg 653 ms, p95 1918 ms, p99 1984 ms | Latency /ask — p50/p95/p99: p95, p99 vọt lên ~1.9 s, p50 gần như không đổi |
| C | `LLM_MODEL=mock-llm-large` (08:27:10Z) | `LLMCostBurnRateHigh` | **144 s** (pending sau 23 s, firing 08:29:34Z sau `for: 2m`). Cost burn 16.04 USD/giờ (rate 5m) / 34.75 USD/giờ (rate 1m); LLM p95 495 ms, `/ask` p95 686 ms (< 1 s nên alert latency không firing) | Cost burn rate (USD/giờ) + LLM latency p95 theo model |
| D | `docker pause k5-day14-observability-agent-2` (08:22:35Z, đang chạy A) | `AgentInstanceDown` (172.18.0.8) | **43 s** (scrape fail → pending 08:22:48Z, firing 08:23:18Z sau `for: 30s`) | Instances up giảm 3 → 2; Requests/s theo instance mất 1 đường |
| E | `AGENT_VERSION=v1` (08:23:55Z, quan sát tới 08:26:55Z) | **Không có** | — | Retrieval hit ratio mất hẳn (metric `agent_retrieval_total` không còn được tăng), Tokens/s giảm (input 1097 → 689 tokens/s), bảng "Phiên bản đang chạy" đổi sang v1; error rate 0.76%, p95 197 ms — mọi panel hạ tầng vẫn xanh |

Phục hồi: unpause + trả `MOCK_ERROR_RATE` về 0.01 lúc 08:23:55Z → `AgentInstanceDown` hết sau 3 s (08:23:58Z), `AgentHighErrorRate` hết sau 47 s (08:24:42Z) — đúng thời gian cửa sổ `rate(...[1m])` "quên" các lỗi cũ. Sau thí nghiệm, xoá `.env` về cấu hình mặc định rồi mới `down -v`.

Quan sát thêm:

- **`for` chống báo động giả:** mỗi lần recreate container (A, C, B), `AgentInstanceDown` lên `pending` 4–6 giây rồi về `inactive`, không firing vì chưa đủ `for: 30s`.
- **Lỗi 504 ở Nginx không hiện trên metric của agent (D):** trong cửa sổ pause, log Nginx có `722 × 200`, `160 × 503` và `29 × 504`. 503 là lỗi agent tự trả (do A) nên có trong `http_requests_total{status="503"}`; 504 là Nginx tự trả khi chờ instance bị pause quá `proxy_read_timeout` — request không bao giờ được agent xử lý xong nên metric agent không đếm. Muốn thấy phải giám sát ở tầng load balancer hoặc synthetic check từ bên ngoài.
- **Vì sao `pause` chứ không `stop`:** container bị stop biến mất khỏi Docker DNS → Prometheus (`dns_sd_configs`) coi như target không còn tồn tại, không có `up == 0`, không alert. Pause giữ DNS nhưng không trả lời → `up == 0`.
- **Alert cost đã firing trước thí nghiệm:** `LLMCostBurnRateHigh` firing từ 08:16:02Z (`activeAt`) trong lần Locust 100 user với model nhỏ mặc định (~60 req/s; panel "Cost burn rate" trên ảnh Grafana leo tới ~3.8 USD/giờ lúc 15:17 GMT+7, vượt ngưỡng 2 USD/giờ; lúc 08:23Z giá trị của alert vẫn là 3.75 vì `rate[5m]` còn chứa giai đoạn 100 user), rồi tự hết lúc 08:24:52Z khi tải giảm xuống 30 user. Với thí nghiệm C, mình chờ alert này hết rồi mới đổi model để đo thời gian firing cho đúng.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Metric hạ tầng chỉ trả lời "có nhanh, có lỗi HTTP không" — v1 vẫn trả 200 rất nhanh nên mọi thứ xanh, dù accuracy rơi từ 100% xuống 4.3% (mục 3). Cách bắt:

1. **Eval là cổng chặn trước deploy:** chạy `run_eval.py --baseline` trong CI/CD; đổi version/prompt/model mà accuracy giảm → exit code 1 → không deploy được (đã kiểm chứng ở mục 3).
2. **Giám sát chất lượng sau deploy:** chạy eval định kỳ (canary/synthetic) trên golden dataset với bản đang chạy và đẩy accuracy thành metric (vd `agent_eval_accuracy`) để alert khi < SLO.
3. **Proxy metric chất lượng:** alert khi `retrieval hit ratio` tụt mạnh hoặc token/câu thay đổi bất thường so với baseline — chính là hai panel thay đổi rõ nhất ở E.
4. **Tín hiệu từ người dùng:** tỉ lệ 👍/👎, tỉ lệ hỏi lại, LLM-as-judge lấy mẫu một phần trace.

### 4.3 Debug bằng trace

Chọn trace chậm nhất tìm được (Jaeger, service `observable-agent`, `POST /ask`, lọc ≥ 1.5 s, lấy trace dài nhất):

- `trace_id`: `461c0940088069d5b0a33107e8cb6694`
- Tổng thời gian: **1742.1 ms**
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (1683.6 ms, 96.6 %)**. Các span khác: `agent.run` 1706.7 ms (span cha), `agent.retrieve_context` 22.9 ms (1.3 %). Attributes: `gen_ai.request.model=mock-llm-small`, `gen_ai.usage.input_tokens=83`, `gen_ai.usage.output_tokens=44`. → Chậm nằm ở upstream LLM (request rơi vào nhánh "slow" của mock), không phải code agent hay retrieval.
- Dòng log tương ứng (grep theo `trace_id` trong `docker compose -p k5-day14-observability logs agent`, lưu ở `reports/diagnostics/trace_log_lines.jsonl`):

```json
{"ts": "2026-11-25T08:16:51.404337+00:00", "level": "INFO", "service": "observable-agent", "instance": "26f95df616bf", "event": "llm_call", "trace_id": "461c0940088069d5b0a33107e8cb6694", "span_id": "cb07519ad0b65646", "request_id": "b542e0dc526c", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 83, "output_tokens": 44, "cost_usd": 3.9e-05, "llm_ms": 1637.5}
{"ts": "2026-11-25T08:16:51.436033+00:00", "level": "INFO", "service": "observable-agent", "instance": "26f95df616bf", "event": "http_request", "trace_id": "461c0940088069d5b0a33107e8cb6694", "span_id": "cb07519ad0b65646", "request_id": "b542e0dc526c", "method": "POST", "route": "/ask", "status": 200, "ms": 1739.0}
```

Trace lỗi (ảnh `jaeger-error-trace.png`): `trace_id` `e95d0e3078f8c1450f6069c51fd44729`, tổng 103.2 ms, `POST /ask` trả **503**. Span `chat mock-llm-small` (81.6 ms) có `error.type=upstream_unavailable` và event `exception` với `exception.type=utils.mock_llm.LLMError`, `exception.message=Upstream LLM error: 503 Service Unavailable (mock)`; lỗi lan lên `agent.run` và `POST /ask` (status code ERROR). Log tương ứng:

```json
{"ts": "2026-11-25T08:18:43.714174+00:00", "level": "INFO", "service": "observable-agent", "instance": "26f95df616bf", "event": "llm_error", "trace_id": "e95d0e3078f8c1450f6069c51fd44729", "span_id": "4dfc8688079e23be", "request_id": "ed614e068a65", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

Lỗi đến sớm (103 ms so với ~200 ms của request thành công) vì mock trả lỗi sau nửa thời gian latency — giống provider thật trả 503 nhanh.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Lệnh: `docker compose -p k5-day14-observability run --rm --no-deps locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`. Số liệu dòng `POST /ask` trong `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv`:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.81 | 190 | 230 | 1700 | 85 / 7137 (1.19 %) |
| 3 | 60.16 | 190 | 230 | 1700 | 79 / 7179 (1.10 %) |

Toàn bộ failures là `503 Service Unavailable` (lỗi upstream giả lập ~1% của mock LLM, `scale_*_failures.csv`); Locust trả exit code 1 vì có request lỗi — bình thường theo README.

**Giải thích kết quả:** Scale 1 → 3 **không cải thiện** RPS (+0.6 %) hay p95 (230 ms cả hai) — 3 instance không nhanh gấp 3.

- **Tải bị giới hạn bởi số user, không phải bởi server.** Locust là tải closed-loop: mỗi user gửi request, chờ trả lời, rồi nghỉ `between(0.5, 2)` giây (trung bình 1.25 s). Trần lý thuyết = 100 user / (1.25 s + ~0.21 s phản hồi) ≈ 68 req/s cho tất cả endpoint; đo được 66.1 req/s (dòng Aggregated) ở cả hai lần. Với 1 instance, hệ thống chưa bão hoà (in-flight ~11, Error rate và p95 bình thường trên Grafana) nên thêm instance không có gì để cải thiện.
- **Latency có "sàn" do LLM:** p50 = 190 ms ≈ thời gian chờ LLM (LLM p95 197.6 ms lúc 08:17:07Z). Mỗi request vẫn phải chờ LLM dù có bao nhiêu instance — scale ngang tăng **sức chứa** (throughput tối đa), không giảm latency của từng request. p99 = 1700 ms là các request rơi vào nhánh slow 1.5 s của mock, cũng không đổi theo số instance.
- **Khi nào 3 instance mới có ích:** khi 1 instance thật sự bão hoà — ví dụ tăng user lên vài trăm (giống c=100 ở mục 2.1, nơi latency phía client tăng mạnh vượt xa thời gian xử lý của app) hoặc khi agent tốn CPU (mỗi container bị giới hạn `cpus: 0.5`). Nút thắt trong lab này là upstream LLM + số user giả lập, không phải số instance.

**Ghi chú phương pháp:** lần chạy scale_1 đầu tiên dùng đúng lệnh trong CHECKPOINTS (không có `--no-deps`). `docker compose run locust` khởi động lại dependency và đưa service `agent` về `deploy.replicas: 2` — container `agent-2` được tạo cùng giây với container Locust — nên lần đó thực chất chạy 2 instance. Mình huỷ lần chạy đó, xoá CSV, scale về 1 và chạy lại với `--no-deps`; script ghi lại danh sách container agent sau mỗi lần chạy (scale_1: chỉ `agent-1`; scale_3: `agent-1..3`). Prometheus/Nginx đã thấy đủ 3 target `up` trước khi chạy scale_3.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Average nói dối, percentile thì không.** Chỉ 3% request chậm làm p99 tăng ×7.6 trong khi avg tăng 60 ms; SLO và alert phải đặt theo p95/p99, và p99 cần đủ mẫu mới ổn định.
  2. **Phải đo đúng chỗ mới tìm đúng nút thắt.** Ở c=100, latency phía client 850 ms nhưng phía server chỉ 243 ms → phần chậm thêm nằm ngoài code app (nguyên nhân cụ thể — hàng đợi kết nối hay load generator chạy chung máy — vẫn là giả thuyết cần đo tiếp). Scale 1 → 3 không giúp gì vì tải bị giới hạn bởi số user và latency có sàn từ LLM. Trace cho thấy 96.6% thời gian của request chậm nằm ở span LLM.
  3. **Hạ tầng xanh ≠ agent đúng.** Đổi sang v1 không làm alert nào firing dù accuracy rơi 100% → 4.3%. Chất lượng phải được chặn bằng eval + quality gate (exit code) và giám sát riêng. Ngược lại metric của chính service cũng có điểm mù (504 ở Nginx không có trên metric agent).
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - Metric ở tầng load balancer (tỉ lệ 5xx/504 của Nginx) + synthetic check từ bên ngoài gọi `/ask` mỗi phút.
  - `agent_eval_accuracy` từ eval định kỳ và alert khi < 85%; alert khi retrieval hit ratio giảm > 20% so với tuần trước.
  - Alert dựa trên **error budget burn rate** (multi-window, vd 1h và 6h) thay cho ngưỡng tức thời; ngưỡng cost alert tính theo traffic dự kiến (ở 100 user, model nhỏ đã vượt 2 USD/giờ).
  - Saturation: thời gian chờ trong hàng đợi / số kết nối đang chờ, CPU/memory theo container, rate limit 429 và cost guard 402 của agent Part 6.
  - Sampling trace (vd giữ 100% trace lỗi/chậm, 10% trace bình thường) để giảm chi phí lưu trữ.

**Giới hạn của thí nghiệm:** load generator chạy chung máy với agent (tranh CPU/mạng loopback); mỗi mức load test chỉ 300 request nên p99 dao động; mock LLM dùng `asyncio.sleep` nên không phản ánh chi phí CPU thật; thời gian alert đo với độ phân giải 2 giây của script theo dõi và phụ thuộc chu kỳ scrape/evaluate 5–15 giây của Prometheus.
