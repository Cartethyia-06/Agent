"""Adapter ChatGPT (gói Free) qua Codex CLI: góp ý thứ hai và review code.

Vì sao không dùng `codex mcp-server` như PLAN.md ghi: lệnh này bị đánh dấu ngừng hỗ trợ ngày
24/8/2026 và bị gỡ từ Codex CLI 0.154.0 (PR openai/codex#42993). Adapter này gọi `codex exec`:
  - chạy không tương tác, sandbox chỉ đọc (`--sandbox read-only`) nên Codex không sửa được file
  - `--output-schema` ép câu trả lời cuối theo JSON Schema trong thư mục schemas/
  - `-o` ghi câu trả lời cuối ra file để đọc lại, `--ephemeral` không lưu phiên

Người dùng vẫn có thể dùng plugin chính thức codex-plugin-cc (/codex:review, /codex:rescue...)
trực tiếp trong Claude Code; xem README.

Chạy thử:
    python adapters/codex_review.py opinion "Kế hoạch này có lỗ hổng gì? ..."
    python adapters/codex_review.py review --uncommitted
    python adapters/codex_review.py review --base main --focus "an toàn bộ nhớ"
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters import _common as c

AGENT_OPINION = "codex_opinion"
AGENT_REVIEW = "codex_review"
OPINION_SCHEMA = c.SCHEMA_DIR / "codex_opinion.schema.json"
REVIEW_SCHEMA = c.SCHEMA_DIR / "codex_review.schema.json"

INSTALL_HINT = "Cài: npm install -g @openai/codex, rồi đăng nhập ChatGPT: codex login"
_QUOTA_RE = re.compile(r"usage limit|rate.?limit|\b429\b|quota|too many requests", re.I)
_AUTH_RE = re.compile(r"not logged in|codex login|\b401\b|unauthori[sz]ed|sign in", re.I)

OPINION_PROMPT = """\
You are giving a second opinion to Claude, the AI that coordinates a team of AIs.
You are in a read-only sandbox: you may read files in the working folder, but change nothing.
Review the work below. Say clearly what is wrong, risky or missing, and why. Skip what is fine.
Write every text field in Vietnamese.

<work>
{question}
</work>
{context}"""

FOCUSED_REVIEW_PROMPT = """\
Review the code changes {target}. Use read-only git commands (git status, git diff, git show)
to see them. You are in a read-only sandbox: do not change any file.
Focus on: {focus}
Report real bugs, security problems and risky code with file and line. Skip style nitpicks.
Write every text field in Vietnamese."""


def find_codex() -> str | None:
    return shutil.which("codex")


def logged_in(codex: str) -> bool | None:
    """Hỏi nhanh `codex login status`. None nghĩa là không hỏi được (cứ chạy tiếp)."""
    try:
        code, _, _ = c.run_process([codex, "login", "status"], timeout=30)
    except OSError:
        return None
    return None if code is None else code == 0


def _diagnose(log: str) -> tuple[str, str]:
    """Đoán nguyên nhân lỗi từ log của Codex."""
    if _QUOTA_RE.search(log):
        return c.QUOTA, "Hết lượt dùng Codex của gói ChatGPT (thử lại sau, xem /status trong codex)"
    if _AUTH_RE.search(log):
        return c.AUTH, "Chưa đăng nhập Codex: chạy `codex login`"
    if "waiting for network" in log.lower():
        return c.TIMEOUT, "Codex không kết nối được mạng tới máy chủ OpenAI"
    return c.ERROR, ""


def run_codex(
    agent: str,
    task_id: str,
    exec_args: list[str],
    schema: Path,
    *,
    prompt: str | None,
    cwd: Path,
    timeout: float | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Chạy `codex exec ...`.

    Trả về (dữ liệu JSON theo schema, None) khi thành công,
    hoặc (None, kết quả hoàn chỉnh) khi lỗi hay khi Codex trả lời không theo schema.
    """
    codex = find_codex()
    if not codex:
        return None, c.failure(
            agent, task_id, c.NOT_CONFIGURED, "Không tìm thấy lệnh codex", hint=INSTALL_HINT
        )
    if not cwd.is_dir():
        return None, c.failure(agent, task_id, c.ERROR, f"Thư mục không tồn tại: {cwd}")
    # Chưa đăng nhập thì `codex exec` không báo lỗi ngay mà treo ở "Reconnecting...",
    # nên kiểm tra trước (mất khoảng 1 giây).
    if logged_in(codex) is False:
        return None, c.failure(
            agent, task_id, c.AUTH, "Chưa đăng nhập Codex", hint="Chạy `codex login` (chọn ChatGPT)"
        )
    seconds = timeout or c.env_int("CODEX_TIMEOUT_SECONDS", 600)

    with tempfile.TemporaryDirectory(prefix="codex-") as tmp:
        last_message = Path(tmp) / "last_message.json"
        cmd = [codex, "exec", *exec_args, "--skip-git-repo-check", "--ephemeral"]
        cmd += ["--output-schema", str(schema), "-o", str(last_message)]
        model = c.env_str("CODEX_MODEL")
        if model:
            cmd += ["-m", model]
        if prompt is not None:
            cmd.append("-")  # đọc prompt từ stdin: tránh lỗi trích dẫn tham số trên Windows
        try:
            code, out, err = c.run_process(
                cmd, input_text=prompt if prompt is not None else None, timeout=seconds, cwd=cwd
            )
        except OSError as exc:
            return None, c.failure(agent, task_id, c.ERROR, f"Không chạy được codex: {exc}")
        raw = last_message.read_text("utf-8").strip() if last_message.exists() else ""

    log = err + out
    if code is None:
        reason = _diagnose(log)[1]
        return None, c.failure(
            agent, task_id, c.TIMEOUT, f"Codex chưa xong sau {seconds:.0f} giây. {reason}".strip()
        )
    if code != 0 or not raw:
        status, reason = _diagnose(log)
        message = reason or f"Codex thoát với mã {code}: {log.strip()[-800:]}"
        return None, c.failure(agent, task_id, status, message)
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        # Codex trả lời nhưng không theo schema: vẫn giữ lại để nhạc trưởng tự đọc
        return None, c.result(agent, task_id, c.OK, answer=raw, sources=[], schema_ok=False)
    return data, None


def second_opinion(
    question: str, *, task_id: str = "", context: str = "", cwd: str = ""
) -> dict[str, Any]:
    """Nhờ ChatGPT/Codex góp ý thứ hai cho một câu trả lời, kế hoạch hay đoạn code."""
    task_id = task_id or c.new_task_id("cx")
    if not (question or "").strip():
        return c.failure(AGENT_OPINION, task_id, c.ERROR, "Thiếu nội dung cần góp ý")
    context_block = f"\n<context>\n{context.strip()}\n</context>\n" if context.strip() else ""
    prompt = OPINION_PROMPT.format(question=question.strip(), context=context_block)
    data, early = run_codex(
        AGENT_OPINION,
        task_id,
        ["--sandbox", "read-only"],
        OPINION_SCHEMA,
        prompt=prompt,
        cwd=Path(cwd) if cwd else c.PROJECT_ROOT,
    )
    if early is not None:
        return early
    return c.result(
        AGENT_OPINION,
        task_id,
        c.OK,
        answer=str(data.get("answer", "")),
        sources=[],
        verdict=data.get("verdict", ""),
        issues=data.get("issues", []),
        confidence=data.get("confidence", ""),
        schema_ok=True,
    )


def review(
    *,
    task_id: str = "",
    uncommitted: bool = False,
    base: str = "",
    commit: str = "",
    focus: str = "",
    cwd: str = "",
) -> dict[str, Any]:
    """Nhờ Codex review thay đổi trong một repo git (mặc định: thay đổi chưa commit)."""
    task_id = task_id or c.new_task_id("cx")
    chosen = sum(bool(x) for x in (uncommitted, base, commit))
    if chosen > 1:
        return c.failure(
            AGENT_REVIEW, task_id, c.ERROR, "Chỉ chọn một: uncommitted, base hoặc commit"
        )
    if chosen == 0:
        uncommitted = True
    workdir = Path(cwd) if cwd else c.PROJECT_ROOT

    if focus.strip():
        # `codex exec review --uncommitted` không nhận kèm prompt, nên review theo trọng tâm
        # dùng `codex exec` thường trong sandbox chỉ đọc.
        if base:
            target = f"between branch {base} and HEAD (git diff {base}...HEAD)"
        elif commit:
            target = f"in commit {commit} (git show {commit})"
        else:
            target = "that are not committed yet (git diff HEAD, plus untracked files)"
        data, early = run_codex(
            AGENT_REVIEW,
            task_id,
            ["--sandbox", "read-only"],
            REVIEW_SCHEMA,
            prompt=FOCUSED_REVIEW_PROMPT.format(target=target, focus=focus.strip()),
            cwd=workdir,
        )
    else:
        if base:
            flags = ["--base", base]
        elif commit:
            flags = ["--commit", commit]
        else:
            flags = ["--uncommitted"]
        data, early = run_codex(
            AGENT_REVIEW, task_id, ["review", *flags], REVIEW_SCHEMA, prompt=None, cwd=workdir
        )

    if early is not None:
        return early
    return c.result(
        AGENT_REVIEW,
        task_id,
        c.OK,
        answer=str(data.get("summary", "")),
        sources=[],
        verdict=data.get("verdict", ""),
        findings=data.get("findings", []),
        schema_ok=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ChatGPT qua Codex CLI: góp ý thứ hai / review")
    sub = parser.add_subparsers(dest="mode", required=True)
    op = sub.add_parser("opinion", help="Góp ý thứ hai cho một nội dung")
    c.add_task_arguments(op, "Nội dung cần góp ý")
    op.add_argument("--context", default="", help="Ngữ cảnh thêm")
    op.add_argument("--cwd", default="", help="Thư mục Codex được đọc")
    rv = sub.add_parser("review", help="Review thay đổi trong repo git")
    rv.add_argument("--task-id", default="")
    group = rv.add_mutually_exclusive_group()
    group.add_argument("--uncommitted", action="store_true", help="Thay đổi chưa commit (mặc định)")
    group.add_argument("--base", default="", help="So với nhánh này")
    group.add_argument("--commit", default="", help="Một commit cụ thể")
    rv.add_argument("--focus", default="", help="Trọng tâm review")
    rv.add_argument("--cwd", default="", help="Repo cần review (mặc định: thư mục dự án)")
    args = parser.parse_args(argv)

    if args.mode == "opinion":
        task = c.read_task(args)
        return c.emit(
            second_opinion(
                task.get("goal", ""),
                task_id=task.get("task_id", ""),
                context=args.context,
                cwd=args.cwd,
            )
        )
    return c.emit(
        review(
            task_id=args.task_id,
            uncommitted=args.uncommitted,
            base=args.base,
            commit=args.commit,
            focus=args.focus,
            cwd=args.cwd,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
