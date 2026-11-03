from __future__ import annotations

from pathlib import Path

import pytest
from google.genai import errors, types

from adapters import _common as c
from adapters import gemini_image as gi
from tests.conftest import FakeClient

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
DRAFT = b"\x89PNG\r\n\x1a\nDRAFT"


def image_response() -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=types.FinishReason.STOP,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(thought=True, inline_data=types.Blob(data=DRAFT, mime_type="image/png")),
                        types.Part(text="Here is your diagram."),
                        types.Part(inline_data=types.Blob(data=PNG, mime_type="image/png")),
                    ],
                ),
            )
        ]
    )


def test_saves_final_image_and_skips_thought_draft(isolated_env: Path) -> None:
    client = FakeClient(image_response())
    out = gi.generate("A block diagram", task_id="img1", aspect_ratio="16:9", client=client)

    assert out["status"] == c.OK
    assert len(out["files"]) == 1
    saved = Path(out["files"][0]["path"])
    assert saved.read_bytes() == PNG
    assert saved.parent == isolated_env / "images"
    assert saved.name == "img1.png"
    assert out["text"] == "Here is your diagram."

    config = client.models.calls[0]["config"]
    assert config.response_modalities == ["IMAGE"]
    assert config.image_config.aspect_ratio == "16:9"
    assert client.models.calls[0]["model"] == "gemini-3.1-flash-image"


def test_quota_error_points_to_handoff() -> None:
    quota = errors.ClientError(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "limit: 0"}})
    out = gi.generate("x", client=FakeClient(quota, quota), models=["a", "b"])
    assert out["status"] == c.QUOTA
    assert "image_handoff" in out["hint"]
    assert len(out["attempts"]) == 2


def test_safety_block_does_not_try_other_models() -> None:
    blocked = types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason=types.FinishReason.IMAGE_SAFETY)]
    )
    out = gi.generate("x", client=FakeClient(blocked), models=["a", "b"])
    assert out["status"] == c.BLOCKED
    assert len(out["attempts"]) == 1


def test_rejects_unknown_aspect_ratio() -> None:
    out = gi.generate("x", aspect_ratio="5:4", client=FakeClient())
    assert out["status"] == c.ERROR
    assert "aspect_ratio" in out["error"]


def test_daily_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_IMAGE_DAILY_LIMIT", "1")
    client = FakeClient(image_response(), image_response())
    assert gi.generate("x", client=client)["status"] == c.OK
    assert gi.generate("x", client=client)["status"] == c.QUOTA


def test_missing_key_is_not_configured() -> None:
    assert gi.generate("x")["status"] == c.NOT_CONFIGURED
