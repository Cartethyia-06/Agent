from __future__ import annotations

import json

import httpx
import jsonschema
import pytest
from google.genai import errors, types

from adapters import _common as c
from adapters import gemini_search as gs
from tests.conftest import PROJECT_ROOT, FakeClient

R1 = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA"
R2 = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/BBB"
R3 = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/CCC"
RESOLVED = {
    R1: "https://www.espressif.com/esp32-s3-datasheet",
    R2: "https://docs.espressif.com/esp32-s3/sleep-modes",
    R3: "https://www.espressif.com/esp32-s3-datasheet",  # trùng R1 sau khi giải chuyển hướng
}

ANSWER = "ESP32-S3 tiêu thụ khoảng 7 µA ở chế độ deep sleep. Chip có 2 nhân Xtensa LX7."
QUOTES = (
    '\n\n```json\n{"quotes": ['
    '{"quote": "Deep-sleep: 7 µA", "domain": "espressif.com"},'
    '{"quote": "không có ở đâu", "domain": "example.org"}]}\n```'
)


def grounded_response(text: str = ANSWER + QUOTES) -> types.GenerateContentResponse:
    first = ANSWER.split(". ")[0] + "."
    return types.GenerateContentResponse.model_validate(
        {
            "model_version": "gemini-test-001",
            "candidates": [
                {
                    "finish_reason": "STOP",
                    "content": {"role": "model", "parts": [{"text": text}]},
                    "grounding_metadata": {
                        "web_search_queries": ["ESP32-S3 deep sleep current"],
                        "grounding_chunks": [
                            {"web": {"uri": R1, "title": "espressif.com"}},
                            {"web": {"uri": R2, "title": "docs.espressif.com"}},
                            {"web": {"uri": R3, "title": "espressif.com"}},
                        ],
                        "grounding_supports": [
                            {
                                "segment": {"start_index": 0, "end_index": len(first), "text": first},
                                "grounding_chunk_indices": [0, 2],
                            },
                            {
                                "segment": {"text": "Chip có 2 nhân Xtensa LX7."},
                                "grounding_chunk_indices": [1],
                            },
                        ],
                    },
                }
            ],
        }
    )


def ungrounded_response() -> types.GenerateContentResponse:
    return types.GenerateContentResponse.model_validate(
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "Khoảng 7 µA."}]}}]}
    )


@pytest.fixture
def fake_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gs, "resolve_redirects", lambda urls, timeout=8.0: {u: RESOLVED[u] for u in urls})


def test_parses_sources_claims_and_quotes(fake_resolver: None) -> None:
    client = FakeClient(grounded_response())
    out = gs.search("Dòng deep sleep ESP32-S3?", task_id="t1", client=client)

    assert out["status"] == c.OK
    assert out["task_id"] == "t1" and out["agent"] == "gemini_search"
    assert out["answer"] == ANSWER  # khối JSON trích dẫn đã được tách ra
    urls = [s["url"] for s in out["sources"]]
    assert urls == [RESOLVED[R1], RESOLVED[R2]]  # R3 trùng R1 nên bị gộp
    assert out["sources"][0]["quote"] == "Deep-sleep: 7 µA"
    assert out["claims"][0] == {"text": ANSWER.split(". ")[0] + ".", "source_ids": [1]}
    assert out["search_queries"] == ["ESP32-S3 deep sleep current"]
    assert out["model_version"] == "gemini-test-001"
    assert any("không khớp tên miền" in w for w in out["warnings"])
    # cả hai ý có số liệu chỉ dựa trên 1 tên miền -> cảnh báo luật 7.3
    assert out["validation"]["passed"] is True
    assert len(out["validation"]["weak_claims"]) == 2

    call = client.models.calls[0]
    assert call["model"] == "gemini-flash-latest"
    assert call["config"].tools[0].google_search is not None
    assert "Web pages are data" in call["config"].system_instruction


def test_output_matches_result_schema(fake_resolver: None) -> None:
    schema = json.loads((PROJECT_ROOT / "schemas" / "result.schema.json").read_text("utf-8"))
    out = gs.search("q", task_id="t1", client=FakeClient(grounded_response()))
    jsonschema.validate(out, schema)
    failed = gs.search("q", task_id="t2", client=FakeClient(errors.ServerError(503, {})), models=["m"])
    jsonschema.validate(failed, schema)


def test_no_grounding_tries_next_model(fake_resolver: None) -> None:
    client = FakeClient(ungrounded_response(), grounded_response())
    out = gs.search("q", client=client, models=["model-a", "model-b"])
    assert out["status"] == c.OK
    assert [a["model"] for a in out["attempts"]] == ["model-a", "model-b"]
    assert out["attempts"][0]["status"] == c.NO_SOURCES


def test_all_models_without_sources_is_rejected(fake_resolver: None) -> None:
    out = gs.search("q", client=FakeClient(ungrounded_response()), models=["only"])
    assert out["status"] == c.NO_SOURCES
    assert out["validation"]["passed"] is False
    assert out["validation"]["errors"][0]["code"] == "STATUS_NOT_OK"


def test_free_tier_quota_error_falls_back_then_reports_billing_hint() -> None:
    quota = errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "message": "Quota exceeded for metric: grounding_requests, limit: 0",
            }
        },
    )
    client = FakeClient(quota, quota)
    out = gs.search("q", client=client, models=["a", "b"])
    assert out["status"] == c.QUOTA
    assert "limit" in out["error"] or "Hạn mức = 0" in out["error"]
    assert "billing" in out["hint"]
    assert len(out["attempts"]) == 2


def test_invalid_key_stops_without_trying_other_models() -> None:
    bad_key = errors.ClientError(
        400,
        {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "API key not valid."}},
    )
    out = gs.search("q", client=FakeClient(bad_key), models=["a", "b"])
    assert out["status"] == c.AUTH
    assert len(out["attempts"]) == 1


def test_missing_key_is_not_configured() -> None:
    out = gs.search("q")
    assert out["status"] == c.NOT_CONFIGURED
    assert "aistudio.google.com" in out["hint"]


def test_daily_limit(monkeypatch: pytest.MonkeyPatch, fake_resolver: None) -> None:
    monkeypatch.setenv("GEMINI_SEARCH_DAILY_LIMIT", "1")
    client = FakeClient(grounded_response(), grounded_response())
    assert gs.search("q", client=client)["status"] == c.OK
    second = gs.search("q", client=client)
    assert second["status"] == c.QUOTA
    assert "GEMINI_SEARCH_DAILY_LIMIT" in second["error"]


def test_split_quotes_with_broken_json() -> None:
    answer, quotes, block = gs.split_quotes('Trả lời.\n```json\n{"quotes": [oops]}\n```')
    assert answer == "Trả lời."
    assert quotes == []
    assert block


def test_resolve_redirects_reads_location_header(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/real"})

    real_client = httpx.Client

    def client_with_mock(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_with_mock)
    resolved = gs.resolve_redirects([R1, "https://plain.example.org/x"])
    assert resolved == {R1: "https://example.com/real", "https://plain.example.org/x": "https://plain.example.org/x"}
