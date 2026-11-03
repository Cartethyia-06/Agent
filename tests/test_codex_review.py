"""Test adapter Codex bằng một lệnh `codex` giả: nó ghi lại tham số, đọc prompt từ stdin và
ghi câu trả lời JSON vào file -o, giống `codex exec` thật."""

from __future__ import annotations

import json
import os

import pytest

from adapters import _common as c
from adapters import codex_review as cx
from tests.conftest import make_fake_cli

FAKE_CODEX = r'''
import json, os, sys
sys.stdin.reconfigure(encoding="utf-8")
args = sys.argv[1:]
log = os.environ["FAKE_CODEX_LOG"]
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
if args[:2] == ["login", "status"]:
    if mode == "logged_out":
        print("Not logged in")
        sys.exit(1)
    print("Logged in using ChatGPT")
    sys.exit(0)
stdin = sys.stdin.read() if "-" in args else ""
with open(log, "w", encoding="utf-8") as f:
    json.dump({"args": args, "stdin": stdin, "cwd": os.getcwd()}, f)
if mode == "network_wait":
    import time
    print("ERROR: Reconnecting... waiting for network", file=sys.stderr, flush=True)
    time.sleep(30)
if mode == "limit":
    print("ERROR: You've hit your usage limit.", file=sys.stderr)
    sys.exit(1)
out = args[args.index("-o") + 1]
if mode == "text":
    answer = "Không theo schema"
elif "review" in args or "Review the code changes" in stdin:
    answer = json.dumps({"verdict": "changes_requested", "summary": "Có 1 lỗi",
                         "findings": [{"severity": "high", "file": "a.py", "line": 3,
                                       "issue": "chia cho 0", "suggestion": "kiểm tra mẫu số"}]})
else:
    answer = json.dumps({"verdict": "partly_agree", "answer": "Thiếu tụ decoupling",
                         "issues": [{"severity": "medium", "point": "Thiếu tụ 100 nF",
                                     "suggestion": "Thêm tụ gần chân VDD"}],
                         "confidence": "medium"})
with open(out, "w", encoding="utf-8") as f:
    f.write(answer)
'''


@pytest.fixture
def fake_codex(monkeypatch: pytest.MonkeyPatch, tmp_path):
    bin_dir = tmp_path / "bin"
    make_fake_cli(bin_dir, "codex", FAKE_CODEX)
    log = tmp_path / "codex_log.json"
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    return lambda: json.loads(log.read_text("utf-8"))


def test_second_opinion_uses_read_only_sandbox_and_stdin(fake_codex, tmp_path) -> None:
    out = cx.second_opinion("Mạch nguồn này ổn không?", task_id="cx1", context="AMS1117", cwd=str(tmp_path))
    assert out["status"] == c.OK
    assert out["verdict"] == "partly_agree"
    assert out["issues"][0]["point"] == "Thiếu tụ 100 nF"
    call = fake_codex()
    args = call["args"]
    assert args[:3] == ["exec", "--sandbox", "read-only"]
    assert {"--skip-git-repo-check", "--ephemeral", "--output-schema", "-o"} <= set(args)
    assert args[-1] == "-"
    assert args[args.index("--output-schema") + 1].endswith("codex_opinion.schema.json")
    assert "Mạch nguồn này ổn không?" in call["stdin"] and "AMS1117" in call["stdin"]


def test_review_defaults_to_uncommitted_without_prompt(fake_codex, tmp_path) -> None:
    out = cx.review(task_id="cx2", cwd=str(tmp_path))
    assert out["status"] == c.OK
    assert out["verdict"] == "changes_requested"
    assert out["findings"][0]["line"] == 3
    args = fake_codex()["args"]
    assert args[:3] == ["exec", "review", "--uncommitted"]
    assert "-" not in args  # --uncommitted không được kèm prompt


def test_focused_review_uses_exec_with_prompt(fake_codex, tmp_path) -> None:
    out = cx.review(base="main", focus="tràn bộ đệm", cwd=str(tmp_path))
    assert out["status"] == c.OK
    call = fake_codex()
    assert call["args"][:3] == ["exec", "--sandbox", "read-only"]
    assert "git diff main...HEAD" in call["stdin"] and "tràn bộ đệm" in call["stdin"]


def test_model_option_is_passed(fake_codex, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CODEX_MODEL", "gpt-test")
    cx.second_opinion("x", cwd=str(tmp_path))
    args = fake_codex()["args"]
    assert args[args.index("-m") + 1] == "gpt-test"


def test_usage_limit_is_quota(fake_codex, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "limit")
    assert cx.second_opinion("x", cwd=str(tmp_path))["status"] == c.QUOTA


def test_non_json_answer_is_kept(fake_codex, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "text")
    out = cx.second_opinion("x", cwd=str(tmp_path))
    assert out["status"] == c.OK and out["schema_ok"] is False
    assert out["answer"] == "Không theo schema"


def test_review_target_conflict_and_missing_codex(monkeypatch, tmp_path) -> None:
    assert cx.review(base="main", commit="abc")["status"] == c.ERROR
    monkeypatch.setenv("PATH", "")
    out = cx.second_opinion("x", cwd=str(tmp_path))
    assert out["status"] == c.NOT_CONFIGURED
    assert "npm install -g @openai/codex" in out["hint"]


def test_schemas_are_strict_for_openai_structured_output() -> None:
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node.get("properties", {}))
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    for schema in (cx.OPINION_SCHEMA, cx.REVIEW_SCHEMA):
        data = json.loads(schema.read_text("utf-8"))
        assert "$schema" not in data
        check(data)


def test_logged_out_fails_fast_without_running_exec(fake_codex, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "logged_out")
    out = cx.second_opinion("x", cwd=str(tmp_path))
    assert out["status"] == c.AUTH
    assert "codex login" in out["hint"]
    with pytest.raises(FileNotFoundError):
        fake_codex()  # exec chưa từng chạy nên chưa có log


def test_timeout_explains_network_wait(fake_codex, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "network_wait")
    monkeypatch.setenv("CODEX_TIMEOUT_SECONDS", "2")
    out = cx.second_opinion("x", cwd=str(tmp_path))
    assert out["status"] == c.TIMEOUT
    assert "không kết nối được mạng" in out["error"]
