"""Kiểm tra cài đặt và hạn mức thật của bạn (PLAN.md mục 11).

    uv run python -m tools.doctor                  # chỉ kiểm tra máy, không gọi API
    uv run python -m tools.doctor --probe          # gọi thử Gemini: model nào dùng được,
                                                   # search grounding có chạy không (tốn ~2 lượt)
    uv run python -m tools.doctor --probe --image  # thử tạo 1 ảnh (tốn tiền nếu đã bật billing)

Số liệu hạn mức trên mạng mỗi nguồn nói một khác; lệnh --probe cho biết đúng tình trạng của
key của bạn.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import shutil
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters import _common as c

INSTALL = {
    "claude": "Windows: irm https://claude.ai/install.ps1 | iex   |   macOS/Linux: "
    "curl -fsSL https://claude.ai/install.sh | bash",
    "codex": "npm install -g @openai/codex   (cần Node.js 18.18+), rồi: codex login",
    "uv": "Windows: powershell -ExecutionPolicy ByPass -c \"irm https://astral.sh/uv/install.ps1 | iex\""
    "   |   macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh",
    "agy": "Tùy chọn (kênh dự phòng): cài tại https://antigravity.google/cli rồi chạy agy để đăng nhập",
}


def row(name: str, ok: bool | None, detail: str, fix: str = "") -> dict[str, Any]:
    """ok=None nghĩa là mục tùy chọn chưa có (không tính là lỗi)."""
    return {"name": name, "ok": ok, "detail": detail, "fix": fix}


def run(cmd: list[str], timeout: float = 20) -> tuple[int | None, str]:
    try:
        code, out, err = c.run_process(cmd, timeout=timeout)
    except OSError as exc:
        return None, type(exc).__name__
    text = (out or err).strip()
    return code, text.splitlines()[0] if text else ""


def check_local() -> list[dict[str, Any]]:
    rows = [
        row(
            "Python >= 3.11",
            sys.version_info >= (3, 11),
            sys.version.split()[0],
            "Cài Python 3.12+ (python.org) hoặc để uv tự cài",
        )
    ]
    for module, dist, optional in [
        ("google.genai", "google-genai", False),
        ("httpx", "httpx", False),
        ("mcp", "mcp", False),
        ("pypdf", "pypdf", True),
    ]:
        try:
            importlib.import_module(module)
            rows.append(row(f"Thư viện {dist}", True, importlib.metadata.version(dist)))
        except ImportError:
            rows.append(row(f"Thư viện {dist}", None if optional else False, "chưa cài", "uv sync"))

    env_file = c.PROJECT_ROOT / ".env"
    rows.append(
        row(".env", env_file.is_file(), str(env_file), "Sao chép .env.example thành .env")
    )
    key = c.gemini_api_key()
    rows.append(
        row(
            "GEMINI_API_KEY",
            bool(key),
            f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "chưa có",
            "Tạo key tại https://aistudio.google.com/apikey và ghi vào .env",
        )
    )

    for tool, optional in [("claude", False), ("uv", False), ("codex", False), ("agy", True)]:
        path = shutil.which(tool)
        if tool == "agy" and c.env_str("AGY_PATH"):
            path = c.env_str("AGY_PATH")
        if not path:
            rows.append(row(f"Lệnh {tool}", None if optional else False, "không có trong PATH", INSTALL[tool]))
            continue
        code, first_line = run([path, "--version"], timeout=15)
        rows.append(row(f"Lệnh {tool}", code == 0, first_line or path, INSTALL[tool] if code else ""))
        if tool == "codex" and code == 0:
            status_code, status_line = run([path, "login", "status"], timeout=30)
            rows.append(
                row("Codex đăng nhập", status_code == 0, status_line or "?", "Chạy: codex login")
            )
    return rows


def probe_gemini(include_image: bool) -> list[dict[str, Any]]:
    if not c.gemini_api_key():
        return [row("Gemini probe", False, "bỏ qua vì chưa có GEMINI_API_KEY")]
    from adapters import gemini_image, gemini_search

    rows: list[dict[str, Any]] = []
    client = c.gemini_client(timeout_seconds=90)
    try:
        available = {m.name.removeprefix("models/") for m in client.models.list() if m.name}
    except Exception as exc:
        status, message = c.classify_gemini_error(exc)
        return [row("Gemini: liệt kê model", False, f"{status}: {message}", "Kiểm tra lại API key")]
    rows.append(row("Gemini: liệt kê model", True, f"{len(available)} model"))

    search_models = c.env_list("GEMINI_SEARCH_MODELS", gemini_search.DEFAULT_MODELS)
    image_models = c.env_list("GEMINI_IMAGE_MODELS", gemini_image.DEFAULT_MODELS)
    for model in search_models + image_models:
        listed = model in available
        rows.append(
            row(
                f"Model {model}",
                True if listed else None,
                "có trong danh sách" if listed else "không thấy (alias *-latest có thể không được liệt kê)",
            )
        )

    first = search_models[0]
    try:
        reply = client.models.generate_content(model=first, contents="Reply with the single word OK.")
        rows.append(row(f"Gọi thường ({first})", True, (reply.text or "").strip()[:40]))
    except Exception as exc:
        status, message = c.classify_gemini_error(exc)
        rows.append(row(f"Gọi thường ({first})", False, f"{status}: {message}"))

    found = gemini_search.search(
        "What is the latest stable version of Python? Answer in one sentence.",
        task_id="probe-search",
        resolve_urls=False,
    )
    detail = (
        f"{len(found.get('sources', []))} nguồn, model {found.get('model', '?')}"
        if found["status"] == c.OK
        else f"{found['status']}: {found.get('error', '')}"
    )
    rows.append(
        row("Google Search grounding", found["status"] == c.OK, detail, found.get("hint", ""))
    )

    if include_image:
        image = gemini_image.generate(
            "A small flat icon of a red resistor on a white background",
            task_id="probe-image",
            out_dir=c.OUTPUT_DIR / "probe",
        )
        detail = (
            image["files"][0]["path"]
            if image["status"] == c.OK
            else f"{image['status']}: {image.get('error', '')}"
        )
        rows.append(row("Tạo ảnh", image["status"] == c.OK, detail, image.get("hint", "")))
    return rows


def print_table(rows: list[dict[str, Any]]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    width = max(len(r["name"]) for r in rows) + 2
    for r in rows:
        mark = {True: "[OK  ]", False: "[LỖI ]", None: "[ -- ]"}[r["ok"]]
        print(f"{mark} {r['name']:<{width}} {r['detail']}")
        if r["ok"] is not True and r["fix"]:
            print(f"        -> {r['fix']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kiểm tra cài đặt hệ agent")
    parser.add_argument("--probe", action="store_true", help="Gọi thử Gemini API (tốn vài lượt)")
    parser.add_argument("--image", action="store_true", help="Cùng với --probe: thử tạo 1 ảnh")
    parser.add_argument("--json", action="store_true", help="In kết quả dạng JSON")
    args = parser.parse_args(argv)

    rows = check_local()
    if args.probe:
        rows += probe_gemini(include_image=args.image)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print_table(rows)
    return 0 if all(r["ok"] is not False for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
