"""Adapter Gemini: tạo ảnh bằng Nano Banana (gemini-*-image) và lưu ra file.

Chạy thử:
    python adapters/gemini_image.py "Sơ đồ khối robot Xiaozhi dùng ESP32-S3, phong cách flat" --aspect 16:9

Lưu ý (11/2026): tạo ảnh qua API không có ở tầng miễn phí. Khi gặp lỗi hạn mức, nhạc trưởng
chuyển sang adapters/image_handoff.py (người dùng dán prompt vào Gemini app hoặc ChatGPT web).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters import _common as c

AGENT = "gemini_image"
DEFAULT_MODELS = ["gemini-3.1-flash-image", "gemini-3.1-flash-lite-image"]
ASPECT_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"}  # theo SDK 2.34
IMAGE_SIZES = {"1K", "2K", "4K"}
EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}

QUOTA_HINT = (
    "Tạo ảnh qua Gemini API cần billing trả trước (Prepay) trong AI Studio, có thể dùng credit "
    "của gói Google AI Pro (README mục 4). Hoặc dùng image_handoff: người dùng dán prompt vào "
    "Gemini app / ChatGPT web."
)


def safe_name(text: str, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")[:60]
    return name or fallback


def save_images(response: Any, out_dir: Path, base_name: str) -> tuple[list[dict[str, Any]], str]:
    """Lưu các phần ảnh trong response (bỏ ảnh nháp 'thought'). Trả về (files, text kèm theo)."""
    candidates = response.candidates or []
    parts = []
    if candidates and candidates[0].content and candidates[0].content.parts:
        parts = candidates[0].content.parts
    files: list[dict[str, Any]] = []
    texts: list[str] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for part in parts:
        if getattr(part, "thought", False):
            continue  # ảnh/chữ nháp trong lúc model "suy nghĩ"
        if part.text:
            texts.append(part.text.strip())
        blob = part.inline_data
        if blob is None or not blob.data:
            continue
        mime = (blob.mime_type or "image/png").lower()
        suffix = "" if not files else f"-{len(files) + 1}"
        path = out_dir / f"{base_name}{suffix}{EXTENSIONS.get(mime, '.png')}"
        path.write_bytes(blob.data)
        files.append({"path": str(path), "mime_type": mime, "bytes": len(blob.data)})
    return files, "\n".join(t for t in texts if t)


def generate(
    prompt: str,
    *,
    task_id: str = "",
    aspect_ratio: str = "",
    image_size: str = "",
    file_name: str = "",
    models: list[str] | None = None,
    out_dir: Path | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Tạo ảnh từ prompt. Không bao giờ raise."""
    task_id = task_id or c.new_task_id("img")
    prompt = (prompt or "").strip()
    if not prompt:
        return c.failure(AGENT, task_id, c.ERROR, "Thiếu prompt")
    if aspect_ratio and aspect_ratio not in ASPECT_RATIOS:
        return c.failure(
            AGENT, task_id, c.ERROR, f"aspect_ratio phải là một trong {sorted(ASPECT_RATIOS)}"
        )
    if image_size and image_size.upper() not in IMAGE_SIZES:
        return c.failure(AGENT, task_id, c.ERROR, "image_size phải là 1K, 2K hoặc 4K")
    if client is None and not c.gemini_api_key():
        return c.failure(
            AGENT,
            task_id,
            c.NOT_CONFIGURED,
            "Chưa có GEMINI_API_KEY",
            hint="Tạo key tại https://aistudio.google.com/apikey rồi ghi vào .env",
        )

    limit = c.env_int("GEMINI_IMAGE_DAILY_LIMIT", 5)
    allowed, used = c.reserve_daily(AGENT, limit)
    if not allowed:
        return c.failure(
            AGENT,
            task_id,
            c.QUOTA,
            f"Đã tạo {used}/{limit} ảnh hôm nay (giới hạn tự đặt GEMINI_IMAGE_DAILY_LIMIT)",
            hint="Dùng image_handoff, hoặc tăng GEMINI_IMAGE_DAILY_LIMIT nếu chắc chắn",
        )

    try:
        from google.genai import types
    except ImportError:
        return c.failure(AGENT, task_id, c.NOT_CONFIGURED, "Chưa cài google-genai", hint="uv sync")
    if client is None:
        client = c.gemini_client()

    image_config = None
    if aspect_ratio or image_size:
        image_config = types.ImageConfig(
            aspect_ratio=aspect_ratio or None, image_size=image_size.upper() or None
        )
    config = types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=image_config)
    target_dir = out_dir or c.OUTPUT_DIR / "images"
    base_name = safe_name(file_name or task_id, "image")

    attempts: list[dict[str, str]] = []
    status, message = c.ERROR, "Không có model nào để thử"
    for model in models or c.env_list("GEMINI_IMAGE_MODELS", DEFAULT_MODELS):
        try:
            response = client.models.generate_content(model=model, contents=prompt, config=config)
        except Exception as exc:  # lỗi mạng/HTTP từ SDK
            status, message = c.classify_gemini_error(exc)
            attempts.append({"model": model, "status": status, "error": message})
            if status in c.MODEL_FALLBACK_STATUSES:
                continue
            break

        files, text = save_images(response, target_dir, base_name)
        if files:
            attempts.append({"model": model, "status": c.OK, "error": ""})
            return c.result(
                AGENT,
                task_id,
                c.OK,
                answer=f"Đã tạo {len(files)} ảnh: " + ", ".join(f["path"] for f in files),
                sources=[],
                files=files,
                text=text,
                model=model,
                prompt=prompt,
                attempts=attempts,
            )
        candidates = response.candidates or []
        finish = str(getattr(candidates[0], "finish_reason", "") or "") if candidates else ""
        blocked = any(w in finish for w in ("SAFETY", "PROHIBITED", "RECITATION"))
        status = c.BLOCKED if blocked else c.ERROR
        message = f"Model không trả ảnh (finish_reason={finish or 'không rõ'}). {text}".strip()
        attempts.append({"model": model, "status": status, "error": message})
        if blocked:
            break  # đổi model không giúp được khi prompt bị chặn

    hint = QUOTA_HINT if status in (c.QUOTA, c.UNAVAILABLE) else ""
    if status == c.BLOCKED:
        hint = "Viết lại prompt: bỏ người thật, thương hiệu, nội dung nhạy cảm"
    return c.failure(AGENT, task_id, status, message, hint=hint, attempts=attempts, prompt=prompt)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gemini (Nano Banana) tạo ảnh -> file PNG")
    c.add_task_arguments(parser, "Prompt mô tả ảnh")
    parser.add_argument("--aspect", default="", help="Tỉ lệ khung, ví dụ 16:9")
    parser.add_argument("--size", default="", help="1K, 2K hoặc 4K")
    parser.add_argument("--name", default="", help="Tên file (không cần đuôi)")
    parser.add_argument("--model", action="append", help="Model (lặp lại để đặt thứ tự dự phòng)")
    args = parser.parse_args(argv)
    task = c.read_task(args)
    return c.emit(
        generate(
            task.get("goal", ""),
            task_id=task.get("task_id", ""),
            aspect_ratio=args.aspect,
            image_size=args.size,
            file_name=args.name,
            models=args.model,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
