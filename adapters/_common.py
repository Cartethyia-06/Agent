"""Phần dùng chung cho mọi adapter.

- Đường dẫn dự án và thư mục outputs/
- Đọc file .env (không cần thư viện ngoài)
- Mẫu kết quả JSON theo PLAN.md mục 6
- Phân loại lỗi Gemini API thành trạng thái dễ xử lý
- Giới hạn lượt dùng mỗi ngày (chặn vòng lặp lỗi tiêu hết credit)
- In kết quả JSON ra stdout (UTF-8, kể cả trên Windows)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "outputs"
SCHEMA_DIR = PROJECT_ROOT / "schemas"

# Trạng thái chung của mọi kết quả
OK = "ok"
NO_SOURCES = "no_sources"  # câu trả lời từ web nhưng không có nguồn -> bị loại (luật 7)
QUOTA = "quota_exceeded"  # hết hạn mức, không có ở tầng miễn phí, hoặc chạm giới hạn tự đặt
AUTH = "auth_error"  # key sai, chưa đăng nhập, không có quyền
NOT_CONFIGURED = "not_configured"  # thiếu key, thiếu CLI, thiếu thư viện
UNAVAILABLE = "unavailable"  # model không tồn tại/đã tắt, tính năng không hỗ trợ, khu vực bị chặn
TIMEOUT = "timeout"
BLOCKED = "blocked"  # bị bộ lọc an toàn chặn
PENDING_USER = "pending_user"  # đang chờ người dùng làm bước thủ công
ERROR = "error"

# Lỗi đáng để thử model kế tiếp của cùng nhà cung cấp
MODEL_FALLBACK_STATUSES = {QUOTA, UNAVAILABLE, TIMEOUT, ERROR, NO_SOURCES}


# ---------------------------------------------------------------- .env

_env_loaded = False


def load_env(path: Path | None = None) -> None:
    """Nạp KEY=VALUE từ .env vào os.environ, không ghi đè biến đã có sẵn."""
    global _env_loaded
    if _env_loaded and path is None:
        return
    env_file = path or PROJECT_ROOT / ".env"
    if path is None:
        _env_loaded = True
    try:
        lines = env_file.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


def env_str(name: str, default: str = "") -> str:
    load_env()
    value = os.environ.get(name, "").strip()
    return value or default


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except ValueError:
        return default


def env_list(name: str, default: list[str]) -> list[str]:
    items = [x.strip() for x in env_str(name).split(",") if x.strip()]
    return items or list(default)


def gemini_api_key() -> str:
    # Cùng thứ tự với SDK google-genai: GOOGLE_API_KEY được ưu tiên nếu có cả hai.
    return env_str("GOOGLE_API_KEY") or env_str("GEMINI_API_KEY")


# ---------------------------------------------------------------- kết quả


def new_task_id(prefix: str = "t") -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


def result(agent: str, task_id: str = "", status: str = OK, **fields: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"task_id": task_id or new_task_id(), "agent": agent, "status": status}
    out.update(fields)
    return out


def failure(
    agent: str, task_id: str, status: str, message: str, *, hint: str = "", **fields: Any
) -> dict[str, Any]:
    out = result(agent, task_id, status, answer="", sources=[], error=message, **fields)
    if hint:
        out["hint"] = hint
    return out


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------- lỗi Gemini


def classify_gemini_error(exc: BaseException) -> tuple[str, str]:
    """Đổi exception của google-genai thành (trạng thái, thông báo ngắn)."""
    try:
        from google.genai import errors as genai_errors
    except ImportError:  # pragma: no cover - chỉ xảy ra khi thiếu thư viện
        genai_errors = None

    if genai_errors is not None and isinstance(exc, genai_errors.APIError):
        code = exc.code or 0
        status = (exc.status or "").upper()
        message = str(exc.message or exc).strip()
        low = message.lower()
        short = message[:400]
        if "api key not valid" in low or "api_key_invalid" in low:
            return AUTH, f"API key không hợp lệ: {short}"
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            if "limit: 0" in low:
                return QUOTA, (
                    "Hạn mức = 0: tính năng/model này không có ở tầng miễn phí của key này "
                    f"(cần bật billing). Chi tiết: {short}"
                )
            return QUOTA, f"Hết hạn mức ({code} {status}): {short}"
        if code in (401, 403) or status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
            return AUTH, f"Không có quyền ({code} {status}): {short}"
        if code == 404 or status == "NOT_FOUND":
            return UNAVAILABLE, f"Model không tồn tại hoặc đã ngừng phục vụ ({code}): {short}"
        if status == "FAILED_PRECONDITION":
            return UNAVAILABLE, f"Không dùng được với tài khoản/khu vực này ({status}): {short}"
        if code == 400 and any(
            w in low for w in ("not supported", "unsupported", "not available", "billing")
        ):
            return UNAVAILABLE, f"Model không hỗ trợ yêu cầu này ({code}): {short}"
        if code >= 500:
            return ERROR, f"Lỗi phía Google ({code} {status}): {short}"
        return ERROR, f"Lỗi Gemini API ({code} {status}): {short}"

    name = type(exc).__name__
    if "timeout" in name.lower():
        return TIMEOUT, f"Hết thời gian chờ ({name})"
    return ERROR, f"{name}: {exc}"[:400]


def gemini_client(timeout_seconds: int | None = None):
    """Tạo client Gemini với timeout và retry cho lỗi tạm thời (không retry 429)."""
    from google import genai
    from google.genai import types

    seconds = timeout_seconds or env_int("GEMINI_TIMEOUT_SECONDS", 120)
    return genai.Client(
        api_key=gemini_api_key(),
        http_options=types.HttpOptions(
            timeout=seconds * 1000,  # SDK tính bằng mili giây
            retry_options=types.HttpRetryOptions(
                attempts=3, http_status_codes=[408, 500, 502, 503, 504]
            ),
        ),
    )


# ---------------------------------------------------------------- giới hạn tự đặt

_usage_lock = threading.Lock()


def usage_file() -> Path:
    return OUTPUT_DIR / ".usage.json"


def reserve_daily(kind: str, daily_limit: int, amount: int = 1) -> tuple[bool, int]:
    """Đếm số lượt gọi API trả phí theo ngày. Trả về (được phép?, số lượt đã dùng).

    daily_limit <= 0 nghĩa là không giới hạn.
    """
    if daily_limit <= 0:
        return True, 0
    with _usage_lock:
        path = usage_file()
        today = date.today().isoformat()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict) or data.get("date") != today:
            data = {"date": today, "counts": {}}
        counts = data.setdefault("counts", {})
        used = int(counts.get(kind, 0))
        if used + amount > daily_limit:
            return False, used
        counts[kind] = used + amount
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return True, used + amount


# ---------------------------------------------------------------- prompt injection

_INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts|messages)",
    r"disregard (all |any )?(the )?(previous|prior|above|earlier)",
    r"forget (all |your )?(previous |prior )?instructions",
    r"new instructions\s*:",
    r"\bsystem prompt\b",
    r"\bdeveloper mode\b",
    r"\bjailbreak",
    r"do not (tell|inform|alert) the user",
    r"<\s*/?\s*(system|assistant|tool_call|instructions?)\s*>",
    r"(curl|wget)\s+\S+\s*\|\s*(ba|z)?sh\b",
    r"bỏ qua (mọi|tất cả|các)?\s*(hướng dẫn|chỉ dẫn|lệnh)",
    r"không (được )?(báo|nói) cho người dùng",
]
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def find_injection_signals(text: str, limit: int = 5) -> list[str]:
    """Tìm câu có dạng "lệnh cho AI" trong nội dung web (luật 7.5: web chỉ là dữ liệu)."""
    hits: list[str] = []
    for pattern in _INJECTION_RE:
        for match in pattern.finditer(text or ""):
            start = max(0, match.start() - 40)
            hits.append(text[start : match.end() + 40].replace("\n", " ").strip())
            if len(hits) >= limit:
                return hits
    return hits


# ---------------------------------------------------------------- chạy lệnh ngoài


def new_process_group() -> dict[str, Any]:
    """Tham số Popen để tiến trình con đứng riêng một nhóm, dừng được cả cây khi hết giờ."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen) -> None:
    """Dừng tiến trình và mọi tiến trình con của nó.

    Lệnh cài qua npm (như codex) là chuỗi launcher -> node -> chương trình thật; chỉ giết
    tiến trình ngoài cùng thì tiến trình con còn chạy và giữ pipe, làm adapter treo.
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    proc.poll()


def run_process(
    cmd: list[str], *, input_text: str | None = None, timeout: float, cwd: Path | None = None
) -> tuple[int | None, str, str]:
    """Chạy lệnh có giới hạn thời gian. Trả về (mã thoát | None khi hết giờ, stdout, stderr)."""
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        **new_process_group(),
    )
    data = input_text.encode("utf-8") if input_text is not None else None
    try:
        out, err = proc.communicate(data, timeout=timeout)
        code: int | None = proc.returncode
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)  # lấy phần output đã có
        except subprocess.TimeoutExpired:
            out, err = b"", b""
        code = None
    return code, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


# ---------------------------------------------------------------- CLI


def add_task_arguments(parser: argparse.ArgumentParser, text_help: str) -> None:
    parser.add_argument("text", nargs="?", help=text_help)
    parser.add_argument(
        "--task-file",
        help="Đọc việc từ file JSON theo mẫu mục 6 PLAN.md (dùng '-' để đọc từ stdin)",
    )
    parser.add_argument("--task-id", default="", help="Mã việc, ví dụ t1")


def read_task(args: argparse.Namespace) -> dict[str, Any]:
    """Gộp đầu vào từ --task-file và tham số dòng lệnh thành một dict việc."""
    task: dict[str, Any] = {}
    if args.task_file:
        raw = sys.stdin.read() if args.task_file == "-" else Path(args.task_file).read_text("utf-8")
        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            raise SystemExit("task-file phải chứa một object JSON")
        task.update(loaded)
    if args.text:
        task["goal"] = args.text
    if args.task_id:
        task["task_id"] = args.task_id
    return task


def emit(data: dict[str, Any]) -> int:
    """In JSON ra stdout. Mã thoát 0 khi thành công, 1 khi có lỗi."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()
    return 0 if data.get("status") in (OK, PENDING_USER) else 1
