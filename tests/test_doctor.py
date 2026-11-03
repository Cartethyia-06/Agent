from __future__ import annotations

from types import SimpleNamespace

from google.genai import errors, types

from adapters import _common as c
from adapters import gemini_image, gemini_search
from tools import doctor


class ProbeModels:
    def __init__(self, fail_list: bool = False) -> None:
        self.fail_list = fail_list

    def list(self):
        if self.fail_list:
            raise errors.ClientError(400, {"error": {"status": "INVALID_ARGUMENT", "message": "API key not valid."}})
        return [SimpleNamespace(name="models/gemini-3.1-flash-image"), SimpleNamespace(name="models/other")]

    def generate_content(self, *, model, contents, config=None):
        return types.GenerateContentResponse.model_validate(
            {"candidates": [{"content": {"role": "model", "parts": [{"text": "OK"}]}}]}
        )


def test_probe_reports_each_capability(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123456")
    monkeypatch.setattr(c, "gemini_client", lambda timeout_seconds=None: SimpleNamespace(models=ProbeModels()))
    monkeypatch.setattr(
        gemini_search,
        "search",
        lambda *a, **k: {"status": "quota_exceeded", "error": "limit: 0", "hint": "bật billing"},
    )
    monkeypatch.setattr(
        gemini_image, "generate", lambda *a, **k: {"status": "ok", "files": [{"path": "outputs/probe/x.png"}]}
    )
    rows = {r["name"]: r for r in doctor.probe_gemini(include_image=True)}

    assert rows["Gemini: liệt kê model"]["ok"] is True
    assert rows["Model gemini-3.1-flash-image"]["ok"] is True
    assert rows["Model gemini-flash-latest"]["ok"] is None  # alias có thể không được liệt kê
    assert rows["Gọi thường (gemini-flash-latest)"]["detail"] == "OK"
    assert rows["Google Search grounding"]["ok"] is False
    assert rows["Google Search grounding"]["fix"] == "bật billing"
    assert rows["Tạo ảnh"]["ok"] is True


def test_probe_stops_on_bad_key(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "bad-key-123456")
    monkeypatch.setattr(
        c, "gemini_client", lambda timeout_seconds=None: SimpleNamespace(models=ProbeModels(fail_list=True))
    )
    rows = doctor.probe_gemini(include_image=False)
    assert len(rows) == 1 and rows[0]["ok"] is False
    assert "auth_error" in rows[0]["detail"]


def test_probe_without_key_is_skipped() -> None:
    rows = doctor.probe_gemini(include_image=False)
    assert rows[0]["ok"] is False and "GEMINI_API_KEY" in rows[0]["detail"]


def test_local_checks_mask_the_key(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyVerySecretValue1234")
    row = next(r for r in doctor.check_local() if r["name"] == "GEMINI_API_KEY")
    assert row["ok"] is True
    assert "VerySecret" not in row["detail"]
    assert row["detail"] == "AIza...1234"
