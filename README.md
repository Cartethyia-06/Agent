# Agent

Hệ nhiều AI phối hợp theo [PLAN.md](PLAN.md): **Claude Code làm nhạc trưởng**, giao việc cho
**Gemini** (tìm web có nguồn, tạo ảnh) và **ChatGPT qua Codex CLI** (góp ý thứ hai, review code),
kiểm tra kết quả theo luật rồi tổng hợp câu trả lời.

```
Bạn
 │
 ▼
Claude Code: nhạc trưởng (CLAUDE.md + skill /orchestrate)
 │  gọi các tool MCP "agent-hub" (tools/mcp_server.py)
 ├── gemini_search ── Gemini API + Google Search ──► JSON {answer, sources, claims}
 │      └─ dự phòng: agy_search (Antigravity CLI) ──► WebSearch của Claude
 ├── gemini_image ─── Gemini API (Nano Banana 2) ──► outputs/images/*.png
 │      └─ dự phòng: image_handoff ──► bạn dán prompt vào Gemini app / ChatGPT web
 ├── codex_opinion, codex_review ── Codex CLI, gói ChatGPT Free, sandbox chỉ đọc
 ├── verify_quote, verify_sources, validate_result ── luật kiểm tra mục 7 PLAN.md
 └── subagent Claude: researcher (Haiku), coder, analyst
```

## Những gì đã đổi so với PLAN.md

Khi xây dựng (03/11/2026), mình kiểm tra lại từng giả định trong PLAN.md bằng cách đọc mã nguồn
SDK, chạy thử CLI thật và tra tài liệu. Có 5 điểm khác:

| PLAN.md giả định | Thực tế 11/2026 | Cách xử lý trong code |
|---|---|---|
| Nối Codex bằng `codex mcp-server` | Lệnh này bị gỡ từ Codex CLI 0.154.0 (9/2026). Bản 0.166.0 báo `unrecognized subcommand` | Adapter gọi `codex exec` (sandbox chỉ đọc, `--output-schema` ép trả JSON). Plugin `codex-plugin-cc` dành cho bạn gõ tay |
| Gemini API miễn phí có Search grounding, khoảng 500 lượt/ngày | Con số đó là của Gemini 2.5 Flash, model đã tắt ngày 16/10/2026. Với Gemini 3 trở lên, grounding không có ở tầng miễn phí; tầng trả phí được 5.000 lượt/tháng không mất tiền | Adapter thử lần lượt nhiều model, báo lỗi rõ ràng, có dự phòng agy và WebSearch. Mục 4 hướng dẫn bật billing bằng credit của gói AI Pro |
| Tạo ảnh Nano Banana qua API có hạn mức miễn phí | Trang giá (10/2026) ghi tầng miễn phí: không có | Dự phòng bán thủ công: Gemini app (gói AI Pro) trước, ChatGPT web sau |
| `agy -p` có lỗi treo | Vẫn còn: treo (#318) và không in gì khi không có terminal (#76) | Đóng stdin, chạy trong pseudo-terminal, giới hạn thời gian, dừng cả cây tiến trình |
| (chưa có trong kế hoạch) | Gói Google AI Pro có 10 USD credit/tháng (Google Developer Program) dùng được cho Gemini API. Billing Gemini API trong AI Studio đã chuyển sang trả trước; credit chỉ kích hoạt sau khi nạp trả trước | Mục 4 |

## 1. Cần có

- Tài khoản: Claude Pro, ChatGPT (Free là đủ), Google AI Pro, và một Gemini API key
  (tạo miễn phí tại <https://aistudio.google.com/apikey>; key này tách riêng với gói AI Pro).
- Máy: Windows 10/11, macOS hoặc Linux; Node.js 18.18 trở lên (để cài Codex);
  [Git for Windows](https://git-scm.com/downloads/win) (khuyên dùng trên Windows).

## 2. Cài đặt (Windows, PowerShell)

1. Cài Claude Code:

   ```powershell
   irm https://claude.ai/install.ps1 | iex
   ```

2. Cài [Node.js LTS](https://nodejs.org), rồi cài Codex CLI và đăng nhập bằng tài khoản ChatGPT:

   ```powershell
   npm install -g @openai/codex
   ```

   ```powershell
   codex login
   ```

3. Cài [uv](https://docs.astral.sh/uv/) (quản lý Python và thư viện, tự cài Python nếu máy chưa có):

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

4. Tải repo, cài thư viện:

   ```powershell
   git clone https://github.com/Cartethyia-06/Agent.git
   ```

   ```powershell
   cd Agent
   ```

   ```powershell
   uv sync
   ```

5. Tạo file `.env` rồi mở ra điền `GEMINI_API_KEY`:

   ```powershell
   Copy-Item .env.example .env
   ```

6. Bật cấu hình Claude Code cho dự án. Mở `setup/claude-settings.json` xem trước: file này cho phép
   Claude tự gọi các tool `agent-hub` và các lệnh `uv run python -m adapters.*`, chặn Claude đọc `.env`,
   tự duyệt MCP server của dự án và khai báo marketplace của plugin Codex. Đồng ý thì chép vào:

   ```powershell
   Copy-Item setup\claude-settings.json .claude\settings.json
   ```

7. (Tùy chọn) Cài [Antigravity CLI](https://antigravity.google/cli) làm kênh tìm web dự phòng, chạy
   `agy` một lần để đăng nhập bằng tài khoản Google AI Pro.

8. Kiểm tra:

   ```powershell
   uv run python -m tools.doctor
   ```

   Sau khi có API key, kiểm tra hạn mức thật của key (tốn khoảng 2 lượt gọi):

   ```powershell
   uv run python -m tools.doctor --probe
   ```

macOS/Linux: bước 1 dùng `curl -fsSL https://claude.ai/install.sh | bash`, bước 3 dùng
`curl -LsSf https://astral.sh/uv/install.sh | sh`, bước 5 và 6 dùng `cp` thay cho `Copy-Item`.

## 3. Chạy

Mở Claude Code trong thư mục dự án:

```powershell
claude
```

Lần đầu Claude Code sẽ hỏi: tin cậy thư mục này không, và có dùng MCP server `agent-hub` không.
Đồng ý cả hai. Gõ `/mcp` để thấy `agent-hub` ở trạng thái connected.

Cài plugin Codex chính thức (marketplace đã được khai báo ở bước 6):

```text
/plugin install codex@openai-codex
/reload-plugins
/codex:setup
```

Thử việc mẫu ở bước 6 của PLAN.md:

```text
/orchestrate So sánh ESP32-S3 với STM32F411 cho robot Xiaozhi, có nguồn, kèm ảnh minh họa
```

Claude sẽ: lập danh sách việc, gọi `gemini_search` song song cho từng chip, kiểm tra nguồn và trích
dẫn, tạo ảnh (hoặc soạn phiếu để bạn tự tạo), có thể nhờ ChatGPT phản biện, rồi trả lời kèm danh
sách nguồn và phần "chưa kiểm chứng".

Lệnh của plugin Codex để bạn gõ trực tiếp: `/codex:review`, `/codex:adversarial-review`,
`/codex:rescue`, `/codex:status`, `/codex:result`.

## 4. Bật Search grounding và tạo ảnh qua API (tùy chọn, tốn tiền)

Từ Gemini 3, Google Search grounding và tạo ảnh không có ở tầng miễn phí. Muốn dùng qua API thì
bật billing trong AI Studio. Billing của Gemini API giờ là **trả trước (Prepay)**:

- Nạp tiền trước (tối thiểu khoảng 5-10 USD, các nguồn ghi khác nhau), dùng tới đâu trừ tới đó gần
  như ngay lập tức.
- Số dư về 0 thì mọi API key thuộc tài khoản billing đó ngừng chạy, không bị trừ thêm. Adapter sẽ
  báo `quota_exceeded` và nhạc trưởng chuyển sang kênh dự phòng.
- Tự nạp (auto-reload) là tùy chọn. Để **tắt** nếu không muốn bị trừ tiền tự động.
- Tiền đã nạp không hoàn lại và hết hạn sau 12 tháng (theo nguồn bên thứ ba).

Gói Google AI Pro có 10 USD credit mỗi tháng (quyền lợi Google Developer Program). Cách dùng:

1. Mở dashboard [Google Developer Program](https://developers.google.com/program), kích hoạt quyền
   lợi đi kèm gói AI Pro.
2. Trong [AI Studio](https://aistudio.google.com), vào Billing, thiết lập Prepay và nạp mức tối
   thiểu. Credit của gói chỉ được kích hoạt khi số dư trả trước lớn hơn 0.
3. Từ đó credit được trừ trước, tiền bạn nạp trừ sau. Credit không đổi được thành số dư; credit cộng
   dồn và hết hạn sau 12 tháng.
4. Xem lại trang Billing đã nhận credit chưa: một số người báo vẫn thấy "No available credits".
5. Giữ giới hạn tự đặt trong `.env`: `GEMINI_SEARCH_DAILY_LIMIT=50`, `GEMINI_IMAGE_DAILY_LIMIT=5`.
   Adapter từ chối gọi tiếp khi chạm giới hạn, để vòng lặp lỗi không tiêu hết tiền.
6. Kiểm tra lại:

   ```powershell
   uv run python -m tools.doctor --probe --image
   ```

Theo các nguồn tham khảo (chưa đối chiếu được với trang giá chính thức): tầng trả phí có 5.000 lượt
grounding mỗi tháng không mất tiền, sau đó 14 USD cho 1.000 lượt; một ảnh Nano Banana 2 cỡ 1K khoảng
0,067 USD. Xem giá hiện hành tại <https://ai.google.dev/gemini-api/docs/pricing>.

Không bật billing cũng dùng được: tìm web sẽ chuyển sang `agy_search` rồi WebSearch của Claude,
tạo ảnh chuyển sang phiếu bàn giao thủ công.

## 5. Gọi từng adapter bằng tay

Mọi adapter in ra JSON theo mẫu mục 6 PLAN.md, chạy được cả khi không có Claude Code:

```powershell
uv run python -m adapters.gemini_search "ESP32-S3 deep sleep current"
```

```powershell
uv run python -m adapters.gemini_image "Flat block diagram of an ESP32-S3 voice robot" --aspect 16:9
```

```powershell
uv run python -m adapters.codex_review opinion "Mạch hạ áp AMS1117 từ 5 V xuống 3.3 V cho ESP32-S3 có ổn không?"
```

```powershell
uv run python -m adapters.codex_review review --uncommitted
```

```powershell
uv run python -m adapters.image_handoff "Flat block diagram of a voice robot" --target gemini_app
```

```powershell
uv run python -m tools.verify_quote https://example.com/datasheet.pdf "Deep-sleep 7 µA"
```

Có thể giao việc bằng file JSON đúng mẫu mục 6: `--task-file task.json` (hoặc `--task-file -` để
đọc từ stdin).

## 6. Cấu trúc thư mục

```
Agent/
├── PLAN.md                    kế hoạch đã thống nhất (+ mục 12: cập nhật khi triển khai)
├── CLAUDE.md                  luật cho nhạc trưởng: phân vai, quy trình, luật kiểm tra
├── .mcp.json                  khai báo MCP server agent-hub cho Claude Code
├── .claude/
│   ├── agents/                subagent: researcher, coder, analyst
│   └── skills/orchestrate/    skill /orchestrate: quy trình 5 bước chi tiết
├── setup/claude-settings.json mẫu .claude/settings.json (quyền, plugin Codex) - bạn tự chép
├── adapters/                  mỗi AI một adapter (PLAN.md mục 8)
│   ├── gemini_search.py       Gemini + Google Search grounding
│   ├── gemini_image.py        Gemini tạo ảnh
│   ├── agy_search.py          Antigravity CLI (dự phòng tìm web)
│   ├── codex_review.py        ChatGPT qua Codex CLI: góp ý thứ hai, review code
│   ├── image_handoff.py       phiếu tạo ảnh thủ công (Gemini app / ChatGPT web)
│   └── _common.py             phần dùng chung: .env, mẫu JSON, lỗi, giới hạn, chạy lệnh
├── tools/
│   ├── mcp_server.py          MCP server: biến adapter thành tool cho Claude Code
│   ├── validate_result.py     luật kiểm tra mục 7
│   ├── verify_quote.py        mở trang/PDF, đối chiếu trích dẫn
│   └── doctor.py              kiểm tra cài đặt và hạn mức thật
├── schemas/                   JSON Schema: task, result, câu trả lời của Codex
├── tests/                     test (không gọi API thật)
├── outputs/                   ảnh, phiếu bàn giao, bộ đếm lượt (không commit)
├── .env.example               mẫu cấu hình; .env thật nằm trong .gitignore
└── pyproject.toml             thư viện Python (uv); requirements.txt cho người dùng pip
```

## 7. Chạy test

```powershell
uv run pytest -q
```

Test dùng kiểu dữ liệu thật của SDK google-genai, lệnh `codex` và `agy` giả (chạy qua subprocess
và pseudo-terminal thật), Internet giả cho phần kiểm tra trích dẫn, và gọi MCP server qua giao thức
MCP. Không test nào gọi API thật.

## 8. Xử lý sự cố

| Hiện tượng | Cách sửa |
|---|---|
| `/mcp` báo `agent-hub` failed | Chạy `uv sync`, rồi chạy thử `uv run python -m tools.mcp_server` để xem lỗi. Kiểm tra `uv` có trong PATH |
| Không muốn dùng uv | Tạo venv bằng pip (`python -m venv .venv`, rồi `.venv\Scripts\pip install -r requirements.txt`) và khai báo server ở scope local (ghi đè bản trong `.mcp.json`), dùng đường dẫn đầy đủ: `claude mcp add agent-hub --scope local -- C:\duong\dan\Agent\.venv\Scripts\python.exe C:\duong\dan\Agent\tools\mcp_server.py` |
| `gemini_search` báo `quota_exceeded`, "Hạn mức = 0" | Key ở tầng miễn phí không có grounding: xem mục 4, hoặc để Claude tự chuyển sang dự phòng |
| `quota_exceeded`, "Hết số dư trả trước" | Nạp thêm trong AI Studio > Billing, hoặc để Claude chuyển sang kênh dự phòng |
| `unavailable`, model không tồn tại (404) | Model đã đổi tên hoặc ngừng: `uv run python -m tools.doctor --probe` rồi sửa `GEMINI_*_MODELS` trong `.env` |
| Codex báo `auth_error` | `codex login`. Adapter kiểm tra đăng nhập trước khi chạy, vì chưa đăng nhập thì `codex exec` treo ở "Reconnecting..." |
| `agy_search` báo không in ra gì (#76) trên Windows | Cập nhật agy (bản 1.2.11 được báo là đã sửa). Hoặc dùng gói cộng đồng [agy-headless-bridge](https://github.com/rhishi99/agy-headless-bridge) như một MCP server riêng; đây là dự án cộng đồng, nên đọc mã trước khi cài |
| Chạm giới hạn tự đặt | Đợi sang ngày mới, hoặc tăng `GEMINI_*_DAILY_LIMIT` trong `.env` nếu chắc chắn |

## 9. Giới hạn đã biết

- Code được viết và test trong môi trường không có API key, không đăng nhập ChatGPT/Google. Phần
  gọi API thật cần bạn chạy `tools.doctor --probe` và việc mẫu ở mục 3 để xác nhận.
- Cờ lệnh của `agy` lấy từ tài liệu cộng đồng; adapter chỉ dùng `-p` và `--model` (khi bạn đặt
  `AGY_MODEL`). Cờ khác thêm qua `AGY_EXTRA_ARGS`.
- `codex exec review` có thể không theo đúng `--output-schema`; khi đó adapter trả nguyên văn câu
  trả lời với `schema_ok: false`.
- Model, giá và hạn mức thay đổi thường xuyên; con số trong file này là tại thời điểm 11/2026.

## Nguồn tham khảo chính

- [Codex CLI: PR gỡ `codex mcp-server`](https://github.com/openai/codex/pull/42993) và
  [bài phân tích việc ngừng hỗ trợ](https://codex.danielvaughan.com/2026/08/30/codex-mcp-server-deprecated-migration-app-server-agents-sdk/)
- [Plugin chính thức codex-plugin-cc](https://github.com/openai/codex-plugin-cc)
- [Gemini API: Grounding with Google Search](https://ai.google.dev/gemini-api/docs/google-search),
  [Tạo ảnh](https://ai.google.dev/gemini-api/docs/image-generation),
  [Lịch ngừng model](https://ai.google.dev/gemini-api/docs/deprecations),
  [Bảng giá](https://ai.google.dev/gemini-api/docs/pricing)
- [Google Developer Program gộp vào gói AI Pro/Ultra (1/2026)](https://yourstory.com/ai-story/google-developer-program-premium-ai-pro-ultra-27-jan-2026)
- [Antigravity CLI: lỗi treo #318](https://github.com/google-antigravity/antigravity-cli/issues/318),
  [lỗi không in output #76](https://github.com/google-antigravity/antigravity-cli/issues/76)
- Claude Code: [subagent](https://code.claude.com/docs/en/sub-agents),
  [skill](https://code.claude.com/docs/en/skills), [MCP](https://code.claude.com/docs/en/mcp),
  [settings](https://code.claude.com/docs/en/settings)
