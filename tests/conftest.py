"""Cấu hình chung cho test. Không test nào gọi API thật hay đọc file .env thật."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters import _common as c  # noqa: E402

ENV_KEYS = [
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_SEARCH_MODELS",
    "GEMINI_IMAGE_MODELS",
    "GEMINI_SEARCH_DAILY_LIMIT",
    "GEMINI_IMAGE_DAILY_LIMIT",
    "CODEX_MODEL",
    "AGY_PATH",
    "AGY_MODEL",
    "AGY_EXTRA_ARGS",
]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Bỏ qua .env thật, xóa biến môi trường liên quan, ghi outputs vào thư mục tạm."""
    monkeypatch.setattr(c, "_env_loaded", True)
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    out = tmp_path / "outputs"
    monkeypatch.setattr(c, "OUTPUT_DIR", out)
    return out


class FakeModels:
    def __init__(self, behaviors: list[Any]) -> None:
        self.behaviors = list(behaviors)
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, *, model: str, contents: Any, config: Any = None) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        behavior = self.behaviors.pop(0)
        if isinstance(behavior, BaseException):
            raise behavior
        return behavior


class FakeClient:
    """Client giả: mỗi lần generate_content lấy một phản hồi (hoặc exception) theo thứ tự."""

    def __init__(self, *behaviors: Any) -> None:
        self.models = FakeModels(list(behaviors))


def make_fake_cli(directory: Path, name: str, body: str) -> Path:
    """Tạo một lệnh giả `name` (chương trình Python) chạy được qua PATH trên mọi hệ điều hành."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / f"{name}_impl.py"
    script.write_text(body, encoding="utf-8")
    if os.name == "nt":
        launcher = directory / f"{name}.bat"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        launcher = directory / name
        launcher.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return launcher
