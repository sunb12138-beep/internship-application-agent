from __future__ import annotations

import re
from pathlib import Path


PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)

IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache"}
FORBIDDEN_SUFFIXES = {".docx", ".pdf", ".eml", ".env"}


def scan_repository(path: Path) -> list[str]:
    findings: list[str] = []
    for candidate in path.rglob("*"):
        if any(part in IGNORED_PARTS for part in candidate.parts):
            continue
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(path)
        if candidate.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(f"forbidden artifact type: {relative}")
            continue
        if candidate.name in {"config.local.toml", "sources.local.toml"}:
            findings.append(f"local configuration must not be tracked: {relative}")
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(f"unexpected binary file: {relative}")
            continue
        if ("/" + "Users" + "/") in text:
            findings.append(f"absolute user path: {relative}")
        if PHONE_RE.search(text):
            findings.append(f"possible mobile number: {relative}")
        for email in EMAIL_RE.findall(text):
            if not email.lower().endswith("@example.com"):
                findings.append(f"possible personal email: {relative}")
                break
    return sorted(set(findings))
