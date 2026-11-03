"""Test adapter Antigravity bằng một lệnh `agy` giả.

Lệnh giả mô phỏng lỗi #76: chỉ in kết quả khi stdout là terminal. Nếu adapter chạy được nó
trong pseudo-terminal thì test qua.
"""

from __future__ import annotations

import os

import pytest

from adapters import _common as c
from adapters import agy_search as agy
from tests.conftest import make_fake_cli

TTY_ONLY_AGY = r'''
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8")
if not os.isatty(1):
    sys.exit(0)  # giống lỗi #76: im lặng khi không có terminal
payload = {"answer": "Khoảng 7 µA", "sources": [
    {"url": "https://www.espressif.com/ds", "quote": "Deep-sleep 7 µA"},
    {"url": "not-a-url", "quote": "x"}]}
print("\x1b[32m⠋ Thinking...\x1b[0m\r")
print("Here you go:")
print(json.dumps(payload, ensure_ascii=False))
'''

PLAIN_AGY = TTY_ONLY_AGY.replace("if not os.isatty(1):\n    sys.exit(0)", "")
QUOTA_AGY = 'import sys\nprint("Error: quota exceeded, resets in 3h")\nsys.exit(3)\n'
SILENT_AGY = "import sys\nsys.exit(0)\n"
HANGING_AGY = "import time\ntime.sleep(60)\n"


def use_fake(monkeypatch: pytest.MonkeyPatch, tmp_path, body: str) -> None:
    monkeypatch.setenv("AGY_PATH", str(make_fake_cli(tmp_path / "bin", "agy", body)))


@pytest.mark.skipif(os.name == "nt", reason="pseudo-terminal chỉ có trên Linux/macOS")
def test_pty_runner_gets_output_from_tty_only_cli(monkeypatch, tmp_path) -> None:
    use_fake(monkeypatch, tmp_path, TTY_ONLY_AGY)
    out = agy.search("Dòng deep sleep ESP32-S3?", task_id="t1", timeout=30)
    assert out["status"] == c.OK, out
    assert out["answer"] == "Khoảng 7 µA"
    assert [s["url"] for s in out["sources"]] == ["https://www.espressif.com/ds"]
    assert out["grounded"] is False
    warning_codes = {w["code"] for w in out["validation"]["warnings"]}
    assert "SELF_REPORTED_SOURCES" in warning_codes


def test_plain_runner_on_windows_style_cli(monkeypatch, tmp_path) -> None:
    use_fake(monkeypatch, tmp_path, PLAIN_AGY)
    code, raw = agy.run_plain([os.environ["AGY_PATH"], "-p", "q"], timeout=30)
    assert code == 0
    assert agy.extract_json(agy.clean_output(raw))["answer"] == "Khoảng 7 µA"


def test_quota_message_is_classified(monkeypatch, tmp_path) -> None:
    use_fake(monkeypatch, tmp_path, QUOTA_AGY)
    out = agy.search("q", timeout=30)
    assert out["status"] == c.QUOTA


def test_silent_cli_reports_bug_76(monkeypatch, tmp_path) -> None:
    use_fake(monkeypatch, tmp_path, SILENT_AGY)
    out = agy.search("q", timeout=30)
    assert out["status"] == c.ERROR
    assert "#76" in out["error"]


@pytest.mark.skipif(os.name == "nt", reason="trên Windows tiến trình cháu giữ pipe, test chậm")
def test_hanging_cli_times_out(monkeypatch, tmp_path) -> None:
    use_fake(monkeypatch, tmp_path, HANGING_AGY)
    out = agy.search("q", timeout=2)
    assert out["status"] == c.TIMEOUT


def test_missing_agy_is_not_configured(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "")
    assert agy.search("q")["status"] == c.NOT_CONFIGURED


def test_build_command_uses_model_and_extra_args(monkeypatch) -> None:
    monkeypatch.setenv("AGY_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("AGY_EXTRA_ARGS", "--foo bar")
    assert agy.build_command("agy", "hi") == ["agy", "-p", "hi", "--model", "gemini-3.8-flash", "--foo", "bar"]


def test_extract_json_picks_outer_object_with_answer() -> None:
    text = 'noise {"x": 1} more\n```json\n{"answer": "A", "sources": [{"url": "https://a.b"}]}\n```'
    assert agy.extract_json(text) == {"answer": "A", "sources": [{"url": "https://a.b"}]}
    assert agy.extract_json("no json here") is None
