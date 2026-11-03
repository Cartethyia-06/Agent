"""Test MCP server qua giao thức MCP thật (client trong bộ nhớ), không gọi API ngoài."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mcp import Client

from adapters import gemini_search
from tools import mcp_server

EXPECTED_TOOLS = {
    "gemini_search",
    "agy_search",
    "gemini_image",
    "image_handoff",
    "codex_opinion",
    "codex_review",
    "verify_quote",
    "verify_sources",
    "validate_result",
}


def call(name: str, arguments: dict) -> dict:
    async def run() -> dict:
        async with Client(mcp_server.mcp) as client:
            result = await client.call_tool(name, arguments)
            return result.structured_content

    return asyncio.run(run())


def test_all_tools_are_registered() -> None:
    async def run() -> set[str]:
        async with Client(mcp_server.mcp) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    assert asyncio.run(run()) == EXPECTED_TOOLS


def test_validate_result_tool() -> None:
    verdict = call(
        "validate_result",
        {"result": {"task_id": "t1", "agent": "gemini_search", "status": "ok", "answer": "7 µA", "sources": []}},
    )
    assert verdict["passed"] is False
    assert verdict["errors"][0]["code"] == "NO_SOURCES"


def test_image_handoff_tool_writes_ticket(isolated_env: Path) -> None:
    out = call("image_handoff", {"prompt": "A robot block diagram", "task_id": "img7", "target": "chatgpt_web"})
    assert out["status"] == "pending_user"
    ticket = Path(out["handoff_file"])
    assert ticket.parent == isolated_env / "handoff"
    assert "chatgpt.com" in ticket.read_text("utf-8")


def test_unexpected_exception_becomes_error_result(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("bất ngờ")

    monkeypatch.setattr(gemini_search, "search", boom)
    out = call("gemini_search", {"question": "q", "task_id": "t5"})
    assert out["status"] == "error"
    assert out["task_id"] == "t5"
    assert "bất ngờ" in out["error"]
