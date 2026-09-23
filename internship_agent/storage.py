from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Iterable

from .extract import is_same_job
from .models import JobRecord, MatchResult


MATCH_FIELDS = [
    "job_id",
    "title",
    "company",
    "platform",
    "match_score",
    "must_have_gaps",
    "matching_evidence",
    "resume_focus",
    "apply_priority",
    "next_action",
    "risk_flags",
]

TRACKER_FIELDS = [
    "date",
    "platform",
    "company",
    "title",
    "url",
    "resume_version",
    "status",
    "next_action",
    "user_confirmed",
    "feedback",
    "review_notes",
    "job_id",
    "source_path",
]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def load_jobs(path: Path) -> list[JobRecord]:
    if not path.exists():
        return []
    jobs: list[JobRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            jobs.append(JobRecord.from_dict(json.loads(line)))
    return jobs


def merge_jobs(existing: list[JobRecord], incoming: Iterable[JobRecord]) -> list[JobRecord]:
    merged = list(existing)
    for candidate in incoming:
        duplicate_index = next(
            (index for index, current in enumerate(merged) if is_same_job(current, candidate)),
            None,
        )
        if duplicate_index is None:
            merged.append(candidate)
            continue
        previous = merged[duplicate_index]
        candidate.job_id = previous.job_id
        merged[duplicate_index] = candidate
    return merged


def write_jobs(path: Path, jobs: Iterable[JobRecord]) -> None:
    ordered = sorted(jobs, key=lambda item: (item.observed_at, item.job_id))
    text = "".join(json.dumps(job.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for job in ordered)
    _atomic_text(path, text)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def update_matches(path: Path, jobs: dict[str, JobRecord], matches: Iterable[MatchResult]) -> None:
    fields, rows = _read_csv(path)
    fields = fields or MATCH_FIELDS
    by_id = {row.get("job_id", ""): row for row in rows if row.get("job_id")}
    for match in matches:
        job = jobs[match.job_id]
        if match.total_score >= 80:
            priority = "A"
        elif match.total_score >= 70:
            priority = "B"
        elif match.total_score >= 60:
            priority = "C"
        else:
            priority = "D"
        by_id[match.job_id] = {
            "job_id": match.job_id,
            "title": job.title,
            "company": job.company,
            "platform": job.source_name or job.source_type,
            "match_score": str(match.total_score),
            "must_have_gaps": "；".join(match.must_have_gaps),
            "matching_evidence": "；".join(match.matching_evidence),
            "resume_focus": "；".join(match.resume_focus),
            "apply_priority": priority,
            "next_action": "准备申请材料" if match.eligible_for_preparation else "人工核对硬性要求和缺失字段",
            "risk_flags": "；".join(match.risk_flags),
        }
    _write_csv(path, fields, sorted(by_id.values(), key=lambda row: row.get("job_id", "")))


def update_tracker(
    path: Path,
    jobs: Iterable[JobRecord],
    resume_versions: dict[str, str],
    prepared_ids: set[str],
) -> None:
    fields, rows = _read_csv(path)
    fields = fields or TRACKER_FIELDS
    by_id = {row.get("job_id", ""): row for row in rows if row.get("job_id")}
    completed_statuses = {"已投递", "面试中", "已录用", "已拒绝", "已撤回"}
    for job in jobs:
        previous = by_id.get(job.job_id, {})
        prepared = job.job_id in prepared_ids
        previous_status = previous.get("status", "")
        status = previous_status if previous_status in completed_statuses else ("待确认" if prepared else previous_status or "待评估")
        by_id[job.job_id] = {
            "date": previous.get("date") or job.observed_at,
            "platform": job.source_name or job.source_type,
            "company": job.company,
            "title": job.title,
            "url": job.source_url,
            "resume_version": resume_versions.get(job.job_id, previous.get("resume_version", "")),
            "status": status,
            "next_action": "核对邮件和附件后由本人确认" if prepared else "补充字段并人工核对匹配结果",
            "user_confirmed": previous.get("user_confirmed", "false"),
            "feedback": previous.get("feedback", ""),
            "review_notes": previous.get("review_notes", ""),
            "job_id": job.job_id,
            "source_path": job.raw_path,
        }
    _write_csv(path, fields, sorted(by_id.values(), key=lambda row: row.get("date", "")))
