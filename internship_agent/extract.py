from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .models import JobRecord
from .sources import IngestedSource


EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)

FIELD_PATTERNS = {
    "company": (
        re.compile(r"^(?:公司|单位|机构|招聘主体)\s*[：:]\s*(.+)$", re.MULTILINE),
    ),
    "title": (
        re.compile(r"^(?:岗位|职位|招聘岗位|实习岗位)\s*[：:]\s*(.+)$", re.MULTILINE),
    ),
    "location": (
        re.compile(r"^(?:地点|工作地点|实习地点)\s*[：:]\s*(.+)$", re.MULTILINE),
    ),
    "deadline": (
        re.compile(r"^(?:截止日期|申请截止|投递截止|截止时间)\s*[：:]\s*(.+)$", re.MULTILINE),
        re.compile(r"(?:截止至|截止到|截至)\s*([^，。；;\n]+)"),
    ),
    "email_subject_rule": (
        re.compile(r"^(?:邮件主题|邮件标题|主题格式|邮件命名)\s*[：:]\s*(.+)$", re.MULTILINE),
    ),
    "attachment_naming_rule": (
        re.compile(r"^(?:附件命名|简历命名|文件命名|附件名称)\s*[：:]\s*(.+)$", re.MULTILINE),
    ),
}

BLOCK_HEADINGS = {
    "requirements": ("任职要求", "岗位要求", "实习要求", "申请要求", "职位要求"),
    "responsibilities": ("岗位职责", "工作职责", "工作内容", "职位描述"),
}

KNOWN_LOCATIONS = (
    "北京",
    "上海",
    "深圳",
    "广州",
    "杭州",
    "南京",
    "苏州",
    "成都",
    "武汉",
    "大连",
    "远程",
)


def _first_match(text: str, patterns: tuple[re.Pattern[str], ...]) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1).strip(" \t：:，,。")
    return ""


def _extract_block(lines: list[str], headings: tuple[str, ...]) -> list[str]:
    result: list[str] = []
    active = False
    all_headings = {heading for values in BLOCK_HEADINGS.values() for heading in values}
    all_headings.update({"投递方式", "联系方式", "岗位信息", "公司介绍", "关于我们"})
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if active and result:
                break
            continue
        normalized = line.rstrip("：:")
        if any(normalized.startswith(heading) for heading in headings):
            active = True
            remainder = re.sub(r"^[^：:]+[：:]?", "", line).strip()
            if remainder:
                result.append(remainder)
            continue
        if active and any(normalized.startswith(heading) for heading in all_headings):
            break
        if active:
            cleaned = re.sub(r"^[\-•·*\d.、)）\s]+", "", line).strip()
            if cleaned:
                result.append(cleaned)
    return result


def _canonical_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))


def _normalize_identity(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def stable_job_id(company: str, title: str, url: str, email: str) -> str:
    basis = "|".join(
        (
            _normalize_identity(company),
            _normalize_identity(title),
            _canonical_url(url),
            email.lower(),
        )
    )
    return "job-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def extract_job(source: IngestedSource) -> JobRecord:
    text = source.text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines()]
    values = {name: _first_match(text, patterns) for name, patterns in FIELD_PATTERNS.items()}
    email_match = EMAIL_RE.search(text)
    email = email_match.group(0) if email_match else ""

    if not values["location"]:
        values["location"] = "、".join(location for location in KNOWN_LOCATIONS if location in text)

    requirements = _extract_block(lines, BLOCK_HEADINGS["requirements"])
    responsibilities = _extract_block(lines, BLOCK_HEADINGS["responsibilities"])
    source_url = source.definition.value if source.definition.source_type == "url" else ""
    needs_review: list[str] = []
    for field_name, label in (
        ("company", "公司"),
        ("title", "岗位"),
        ("location", "地点"),
        ("deadline", "截止日期"),
    ):
        if not values[field_name]:
            needs_review.append(f"{label}未识别")
    if not requirements:
        needs_review.append("实习要求未识别")
    if not email:
        needs_review.append("投递邮箱未识别")
    if not values["email_subject_rule"]:
        needs_review.append("邮件主题规则未识别")
    if not values["attachment_naming_rule"]:
        needs_review.append("附件命名规则未识别")

    job_id = stable_job_id(values["company"], values["title"], source_url, email)
    return JobRecord(
        job_id=job_id,
        source_id=source.definition.source_id,
        source_type=source.definition.source_type,
        source_name=source.definition.name,
        source_url=source_url,
        observed_at=date.today().isoformat(),
        raw_path=str(source.raw_path),
        extracted_path=str(source.extracted_path),
        source_hash=source.source_hash,
        company=values["company"],
        title=values["title"],
        location=values["location"],
        requirements=requirements,
        responsibilities=responsibilities,
        deadline=values["deadline"],
        email=email,
        email_subject_rule=values["email_subject_rule"],
        attachment_naming_rule=values["attachment_naming_rule"],
        needs_review=needs_review,
    )


def is_same_job(left: JobRecord, right: JobRecord) -> bool:
    if left.job_id == right.job_id:
        return True
    if left.source_url and right.source_url and _canonical_url(left.source_url) == _canonical_url(right.source_url):
        return True
    identity_left = (_normalize_identity(left.company), _normalize_identity(left.title), left.email.lower())
    identity_right = (_normalize_identity(right.company), _normalize_identity(right.title), right.email.lower())
    return bool(all(identity_left)) and identity_left == identity_right


def preserve_relative_path(path: str, case_dir: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(case_dir.resolve()))
    except ValueError:
        return path
