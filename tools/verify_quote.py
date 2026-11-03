"""Mở link nguồn và kiểm tra trích dẫn có thật trong trang không (luật 7.4 PLAN.md).

Script tự tải trang (HTML, text hoặc PDF datasheet), tìm câu trích, và chỉ trả lại một đoạn
ngữ cảnh ngắn. Nhờ vậy Claude không phải đọc cả trang web -> tiết kiệm hạn mức (mục 8).
Nội dung trang chỉ là dữ liệu: script không làm theo gì trong đó, chỉ báo nếu thấy câu
giống lệnh điều khiển AI.

Chạy:
    python tools/verify_quote.py https://example.com/page "đoạn trích cần kiểm tra"
    python tools/verify_quote.py --result ket_qua.json   # kiểm tra mọi trích dẫn trong kết quả
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters import _common as c

MAX_BYTES = 8_000_000
MAX_PDF_PAGES = 80
FUZZY_THRESHOLD = 0.85
CONTEXT_CHARS = 160
USER_AGENT = "Mozilla/5.0 (compatible; agent-hub-verify/0.1; +https://github.com/Cartethyia-06/Agent)"


class UnsupportedContent(Exception):
    pass


class _TextExtractor(HTMLParser):
    # Không bỏ qua <head>: HTML5 cho phép không đóng </head>, sẽ làm mất cả trang.
    SKIP = {"script", "style", "noscript", "svg", "template"}
    BLOCK = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)


def pdf_to_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise UnsupportedContent("PDF (cần cài pypdf: uv sync)") from exc
    reader = PdfReader(io.BytesIO(data))
    return " ".join((page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES])


def fetch_text(url: str, timeout: float = 20.0) -> tuple[str, str, str]:
    """Tải URL. Trả về (url cuối cùng, loại nội dung, văn bản thuần)."""
    import httpx

    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,text/plain;q=0.9,*/*;q=0.5"}
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    break
            final_url = str(response.url)
            encoding = response.encoding or "utf-8"
    data = bytes(body)
    if "pdf" in content_type or data[:5] == b"%PDF-":
        return final_url, "pdf", pdf_to_text(data)
    text = data.decode(encoding, errors="replace")
    if "html" in content_type or "xml" in content_type or "<html" in text[:2000].lower():
        return final_url, "html", html_to_text(text)
    if content_type.startswith("text/") or not content_type:
        return final_url, "text", text
    raise UnsupportedContent(content_type)


_DASHES = re.compile("[‐-―−]")
_DQUOTES = re.compile("[“”„‟″«»]")
_SQUOTES = re.compile("[‘’‚‛′]")


def normalize(text: str) -> str:
    """Chuẩn hóa để so khớp: NFKC (µ -> μ, Ω), gạch ngang, dấu nháy, khoảng trắng."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("­", "")  # gạch nối mềm
    text = _DASHES.sub("-", text)
    text = _DQUOTES.sub('"', text)
    text = _SQUOTES.sub("'", text)
    return re.sub(r"\s+", " ", text).strip()


def find_quote(page: str, quote: str) -> tuple[str, float, int, int]:
    """Tìm quote trong page (cả hai đã normalize). Trả về (trạng thái, tỉ lệ khớp, vị trí, độ dài)."""
    page_low, quote_low = page.lower(), quote.lower()
    if not quote_low:
        return "not_found", 0.0, -1, 0
    index = page_low.find(quote_low)
    if index >= 0:
        return "found", 1.0, index, len(quote_low)
    matcher = difflib.SequenceMatcher(None, page_low, quote_low, autojunk=False)
    block = matcher.find_longest_match(0, len(page_low), 0, len(quote_low))
    ratio = block.size / len(quote_low)
    if ratio >= FUZZY_THRESHOLD:
        return "found_fuzzy", round(ratio, 3), block.a, block.size
    return "not_found", round(ratio, 3), block.a if block.size else -1, block.size


def verify(url: str, quote: str, *, timeout: float = 20.0) -> dict[str, Any]:
    """Kiểm tra một trích dẫn. Không bao giờ raise."""
    out: dict[str, Any] = {"url": url, "quote": quote}
    if not url.startswith(("http://", "https://")):
        return {**out, "status": "fetch_error", "error": "URL phải bắt đầu bằng http(s)://"}
    try:
        final_url, kind, raw_text = fetch_text(url, timeout=timeout)
    except UnsupportedContent as exc:
        return {**out, "status": "unsupported_content", "error": f"Không đọc được loại nội dung: {exc}"}
    except Exception as exc:  # lỗi mạng, HTTP 4xx/5xx, PDF hỏng...
        return {**out, "status": "fetch_error", "error": f"{type(exc).__name__}: {exc}"[:300]}

    page = normalize(raw_text)
    # Câu trích có dấu "..." thì kiểm tra từng đoạn
    pieces = [normalize(p) for p in re.split(r"\.\.\.|…", quote) if normalize(p)]
    worst_status, worst_ratio, first_pos, first_len = "found", 1.0, -1, 0
    rank = {"found": 2, "found_fuzzy": 1, "not_found": 0}
    for piece in pieces or [normalize(quote)]:
        status, ratio, pos, length = find_quote(page, piece)
        if first_pos < 0 and pos >= 0:
            first_pos, first_len = pos, length
        if rank[status] < rank[worst_status]:
            worst_status = status
        worst_ratio = min(worst_ratio, ratio)

    context = ""
    if first_pos >= 0:
        start = max(0, first_pos - CONTEXT_CHARS)
        context = page[start : first_pos + first_len + CONTEXT_CHARS]
    return {
        **out,
        "final_url": final_url,
        "content_type": kind,
        "status": worst_status,
        "match_ratio": worst_ratio,
        "context": context,
        "page_chars": len(page),
        "injection_signals": c.find_injection_signals(page, limit=3),
    }


def verify_result(result: dict[str, Any], *, max_sources: int = 6) -> dict[str, Any]:
    """Kiểm tra mọi trích dẫn trong một kết quả, gắn quote_verified vào từng nguồn."""
    sources = [
        s for s in result.get("sources", []) if isinstance(s, dict) and s.get("url") and s.get("quote")
    ][:max_sources]
    if not sources:
        return {**result, "quote_check": {"checked": 0, "verified": 0, "note": "Không có trích dẫn"}}
    with ThreadPoolExecutor(max_workers=min(6, len(sources))) as pool:
        checks = list(pool.map(lambda s: verify(s["url"], s["quote"]), sources))
    verified = 0
    for src, check in zip(sources, checks):
        src["quote_verified"] = check["status"] in ("found", "found_fuzzy")
        src["quote_check"] = {
            k: check.get(k) for k in ("status", "match_ratio", "final_url", "context", "error")
        }
        verified += src["quote_verified"]
        if check.get("injection_signals"):
            src["quote_check"]["injection_signals"] = check["injection_signals"]
    return {**result, "quote_check": {"checked": len(sources), "verified": verified}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kiểm tra trích dẫn có thật trong trang nguồn")
    parser.add_argument("url", nargs="?")
    parser.add_argument("quote", nargs="?")
    parser.add_argument("--result", help="File JSON kết quả (hoặc '-' cho stdin): kiểm tra mọi nguồn")
    args = parser.parse_args(argv)
    if args.result:
        raw = sys.stdin.read() if args.result == "-" else Path(args.result).read_text("utf-8")
        return c.emit(verify_result(json.loads(raw)))
    if not (args.url and args.quote):
        parser.error("cần url và quote, hoặc --result")
    check = verify(args.url, args.quote)
    c.emit(check)
    return 0 if check["status"] in ("found", "found_fuzzy") else 1


if __name__ == "__main__":
    raise SystemExit(main())
