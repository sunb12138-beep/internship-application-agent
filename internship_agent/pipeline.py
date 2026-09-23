from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .application import create_application_draft
from .config import AgentConfig
from .extract import extract_job, preserve_relative_path
from .matching import score_job
from .models import JobRecord
from .resume import ResumeError, build_resume_package
from .sources import SourceError, ingest_source, load_sources
from .storage import load_jobs, merge_jobs, update_matches, update_tracker, write_jobs


def _relative_job_paths(job: JobRecord, case_dir: Path) -> JobRecord:
    job.raw_path = preserve_relative_path(job.raw_path, case_dir)
    job.extracted_path = preserve_relative_path(job.extracted_path, case_dir)
    return job


def run_pipeline(config: AgentConfig) -> dict[str, object]:
    config.runs_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir = config.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    existing_jobs = load_jobs(config.jobs_file)
    incoming_jobs: list[JobRecord] = []
    errors: list[dict[str, str]] = []
    for source in load_sources(config.sources_file):
        if not source.enabled:
            continue
        try:
            ingested = ingest_source(source, config)
            incoming_jobs.append(_relative_job_paths(extract_job(ingested), config.case_dir))
        except (SourceError, OSError, ValueError) as exc:
            errors.append({"source_id": source.source_id, "error": str(exc)})

    merged_jobs = merge_jobs(existing_jobs, incoming_jobs)
    write_jobs(config.jobs_file, merged_jobs)
    by_id = {job.job_id: job for job in merged_jobs}
    current_jobs = [by_id[job.job_id] for job in incoming_jobs]

    matches = [score_job(job, config) for job in current_jobs]
    update_matches(config.matches_file, by_id, matches)

    prepared_ids: set[str] = set()
    resume_versions: dict[str, str] = {}
    prepared: list[dict[str, object]] = []
    for job, match in zip(current_jobs, matches):
        if not match.eligible_for_preparation:
            continue
        try:
            resume = build_resume_package(config, job, match)
            application = create_application_draft(config, job, match, resume)
        except (ResumeError, OSError, ValueError) as exc:
            errors.append({"source_id": job.source_id, "job_id": job.job_id, "error": str(exc)})
            continue
        prepared_ids.add(job.job_id)
        resume_versions[job.job_id] = preserve_relative_path(str(resume.attachment_path), config.case_dir)
        prepared.append(
            {
                "job_id": job.job_id,
                "match_score": match.total_score,
                "role": match.role_name,
                "draft_dir": preserve_relative_path(str(resume.draft_dir), config.case_dir),
                "resume": resume_versions[job.job_id],
                "email": preserve_relative_path(application["eml"], config.case_dir),
                "status": "待确认",
            }
        )

    update_tracker(config.tracker_file, current_jobs, resume_versions, prepared_ids)
    summary: dict[str, object] = {
        "run_id": run_id,
        "sources_processed": len(incoming_jobs),
        "jobs_in_store": len(merged_jobs),
        "jobs_scored": len(matches),
        "applications_prepared": len(prepared),
        "prepared": prepared,
        "errors": errors,
        "external_action_performed": False,
        "email_sending_available": False,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "matches.json").write_text(
        json.dumps([asdict(item) for item in matches], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary
