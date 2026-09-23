from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import tomllib
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from .config import AgentConfig
from .models import SourceDefinition


class SourceError(RuntimeError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
        elif self._ignored_depth == 0 and tag in {"p", "div", "br", "li", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif self._ignored_depth == 0 and tag in {"p", "div", "li", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts).replace("\xa0", " ")
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n\s*\n+", "\n", value)
        return value.strip()


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        ensure_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class IngestedSource:
    definition: SourceDefinition
    text: str
    raw_path: Path
    extracted_path: Path
    source_hash: str


def load_sources(path: Path) -> list[SourceDefinition]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    sources: list[SourceDefinition] = []
    seen: set[str] = set()
    for item in data.get("sources", []):
        source_id = str(item.get("id", "")).strip()
        if not source_id:
            raise SourceError("each [[sources]] entry requires id")
        if source_id in seen:
            raise SourceError(f"duplicate source id: {source_id}")
        seen.add(source_id)
        source_type = str(item.get("type", "")).strip().lower()
        if source_type not in {"url", "file", "text"}:
            raise SourceError(f"unsupported source type for {source_id}: {source_type}")
        value = str(item.get("value", item.get("text", "")))
        if not value:
            raise SourceError(f"source {source_id} requires value")
        sources.append(
            SourceDefinition(
                source_id=source_id,
                source_type=source_type,
                value=value,
                name=str(item.get("name", "")),
                enabled=bool(item.get("enabled", True)),
                tags=tuple(str(tag) for tag in item.get("tags", [])),
            )
        )
    return sources


def ensure_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SourceError("only http and https URLs are supported")
    if not parsed.hostname:
        raise SourceError("URL has no hostname")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise SourceError("local URLs are not allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or 443)}
    except socket.gaierror as exc:
        raise SourceError(f"cannot resolve URL hostname: {hostname}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise SourceError(f"URL resolves to a non-public address: {address}")


def _fetch_url(url: str, timeout: int, max_bytes: int) -> tuple[bytes, str]:
    ensure_public_url(url)
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "InternshipApplicationAgent/0.1 (+user-supplied sources only)",
            "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
        },
    )
    with opener.open(request, timeout=timeout) as response:
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise SourceError(f"source exceeds {max_bytes} bytes")
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
    try:
        text = data.decode(charset, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    return text.encode("utf-8"), content_type


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


def _safe_slug(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z_-]+", "-", value).strip("-")
    return value[:60] or "source"


def ingest_source(source: SourceDefinition, config: AgentConfig) -> IngestedSource:
    config.snapshots_dir.mkdir(parents=True, exist_ok=True)
    config.extracted_dir.mkdir(parents=True, exist_ok=True)

    content_type = "text/plain"
    if source.source_type == "url":
        raw_bytes, content_type = _fetch_url(
            source.value,
            timeout=config.fetch_timeout_seconds,
            max_bytes=config.max_download_bytes,
        )
    elif source.source_type == "file":
        input_path = Path(source.value).expanduser()
        if not input_path.is_absolute():
            input_path = config.sources_file.parent / input_path
        if not input_path.exists() or not input_path.is_file():
            raise SourceError(f"source file does not exist: {input_path}")
        if input_path.stat().st_size > config.max_download_bytes:
            raise SourceError(f"source exceeds {config.max_download_bytes} bytes")
        raw_bytes = input_path.read_bytes()
        if input_path.suffix.lower() in {".html", ".htm"}:
            content_type = "text/html"
    else:
        raw_bytes = source.value.encode("utf-8")

    source_hash = hashlib.sha256(raw_bytes).hexdigest()
    slug = _safe_slug(source.source_id)
    raw_suffix = ".html" if content_type == "text/html" else ".txt"
    raw_path = config.snapshots_dir / f"{slug}-{source_hash[:12]}{raw_suffix}"
    raw_path.write_bytes(raw_bytes)

    decoded = raw_bytes.decode("utf-8", errors="replace")
    extracted = _html_to_text(decoded) if content_type == "text/html" else decoded.strip()
    if not extracted:
        raise SourceError(f"source {source.source_id} produced no readable text")
    extracted_path = config.extracted_dir / f"{slug}-{source_hash[:12]}.txt"
    extracted_path.write_text(extracted, encoding="utf-8")
    return IngestedSource(source, extracted, raw_path, extracted_path, source_hash)
