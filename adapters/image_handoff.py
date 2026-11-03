"""Bàn giao tạo ảnh bán thủ công (khi Gemini API hết hạn mức hoặc chưa bật billing).

Claude viết sẵn prompt, file này lưu thành một phiếu bàn giao trong outputs/handoff/.
Người dùng tự dán prompt vào web, tải ảnh về đúng chỗ, rồi báo Claude. Không tự động hóa
giao diện web, nên không vi phạm điều khoản (PLAN.md mục 2 và 8).

Đích:
  gemini_app   https://gemini.google.com/app  (gói Google AI Pro, hạn mức ảnh cao hơn)
  chatgpt_web  https://chatgpt.com/           (gói Free, khoảng 2-3 ảnh/ngày)

Chạy thử:
    python adapters/image_handoff.py "A clean block diagram of ..." --target chatgpt_web
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters import _common as c
from adapters.gemini_image import safe_name

AGENT = "image_handoff"
TARGETS = {
    "gemini_app": ("Gemini app (gói Google AI Pro)", "https://gemini.google.com/app"),
    "chatgpt_web": ("ChatGPT web (gói Free, khoảng 2-3 ảnh/ngày)", "https://chatgpt.com/"),
}


def handoff(
    prompt: str,
    *,
    task_id: str = "",
    target: str = "gemini_app",
    aspect_ratio: str = "",
    notes: str = "",
    out_dir: Path | None = None,
) -> dict[str, Any]:
    task_id = task_id or c.new_task_id("img")
    prompt = (prompt or "").strip()
    if not prompt:
        return c.failure(AGENT, task_id, c.ERROR, "Thiếu prompt")
    if target not in TARGETS:
        return c.failure(AGENT, task_id, c.ERROR, f"target phải là một trong {sorted(TARGETS)}")

    label, url = TARGETS[target]
    base = out_dir or c.OUTPUT_DIR
    name = safe_name(task_id, "image")
    expected_image = base / "images" / f"{name}.png"
    ticket = base / "handoff" / f"{name}.md"
    ticket.parent.mkdir(parents=True, exist_ok=True)
    expected_image.parent.mkdir(parents=True, exist_ok=True)

    full_prompt = prompt
    if aspect_ratio:
        full_prompt += f"\n\nAspect ratio: {aspect_ratio}."
    lines = [
        f"# Phiếu tạo ảnh {task_id}",
        "",
        f"1. Mở **{label}**: {url}",
        "2. Dán nguyên prompt dưới đây (một tin nhắn), gửi đi.",
        f"3. Tải ảnh về và lưu thành: `{expected_image}`",
        "4. Quay lại Claude Code và nhắn: `xong ảnh " + task_id + "`",
        "",
        "## Prompt",
        "",
        "```text",
        full_prompt,
        "```",
    ]
    if notes.strip():
        lines += ["", "## Ghi chú", "", notes.strip()]
    ticket.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return c.result(
        AGENT,
        task_id,
        c.PENDING_USER,
        answer=(
            f"Chờ người dùng tạo ảnh trên {label}. Phiếu bàn giao: {ticket}. "
            f"Ảnh cần lưu tại: {expected_image}"
        ),
        sources=[],
        target=target,
        target_url=url,
        handoff_file=str(ticket),
        expected_image=str(expected_image),
        prompt=full_prompt,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tạo phiếu bàn giao prompt ảnh cho người dùng")
    c.add_task_arguments(parser, "Prompt mô tả ảnh")
    parser.add_argument("--target", choices=sorted(TARGETS), default="gemini_app")
    parser.add_argument("--aspect", default="", help="Tỉ lệ khung, ví dụ 16:9")
    parser.add_argument("--notes", default="", help="Ghi chú thêm cho người dùng")
    args = parser.parse_args(argv)
    task = c.read_task(args)
    return c.emit(
        handoff(
            task.get("goal", ""),
            task_id=task.get("task_id", ""),
            target=args.target,
            aspect_ratio=args.aspect,
            notes=args.notes,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
