from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
    pass


def _expanded(value: str) -> str:
    return os.path.expanduser(os.path.expandvars(value))


def _resolve(base: Path, value: str) -> Path:
    expanded = Path(_expanded(value))
    return expanded if expanded.is_absolute() else base / expanded


@dataclass(frozen=True)
class IdentityConfig:
    name: str = ""
    school: str = ""
    major: str = ""
    grade: str = ""
    availability: str = ""


@dataclass(frozen=True)
class PreferenceConfig:
    cities: tuple[str, ...] = ()
    target_roles: tuple[str, ...] = ()
    available_days_per_week: int = 0
    internship_months: int = 0


@dataclass(frozen=True)
class ResumeGroup:
    paragraph_indices: tuple[int, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class RoleConfig:
    name: str
    keywords: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    base_docx: Path
    groups: tuple[ResumeGroup, ...] = ()


@dataclass(frozen=True)
class AgentConfig:
    config_path: Path
    case_dir: Path
    sources_file: Path
    jobs_file: Path
    matches_file: Path
    tracker_file: Path
    snapshots_dir: Path
    extracted_dir: Path
    drafts_dir: Path
    runs_dir: Path
    identity: IdentityConfig
    preferences: PreferenceConfig
    roles: tuple[RoleConfig, ...]
    preparation_threshold: int = 70
    fetch_timeout_seconds: int = 20
    max_download_bytes: int = 2_000_000
    soffice_command: str = "soffice"
    default_email_body: str = ""
    risk_terms: tuple[str, ...] = field(
        default_factory=lambda: ("交钱培训", "先贷款", "包就业", "无经验高薪")
    )


def load_config(path: str | Path) -> AgentConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    case_data = data.get("case", {})
    case_value = case_data.get("case_dir")
    if not case_value:
        raise ConfigurationError("[case].case_dir is required")
    case_dir = _resolve(config_path.parent, str(case_value)).resolve()

    def case_path(key: str, default: str) -> Path:
        return _resolve(case_dir, str(case_data.get(key, default))).resolve()

    source_value = case_data.get("sources_file", "sources.local.toml")
    sources_file = _resolve(config_path.parent, str(source_value)).resolve()

    identity_data = data.get("identity", {})
    identity = IdentityConfig(
        name=str(identity_data.get("name", "")),
        school=str(identity_data.get("school", "")),
        major=str(identity_data.get("major", "")),
        grade=str(identity_data.get("grade", "")),
        availability=str(identity_data.get("availability", "")),
    )

    preference_data = data.get("preferences", {})
    preferences = PreferenceConfig(
        cities=tuple(str(item) for item in preference_data.get("cities", [])),
        target_roles=tuple(str(item) for item in preference_data.get("target_roles", [])),
        available_days_per_week=int(preference_data.get("available_days_per_week", 0) or 0),
        internship_months=int(preference_data.get("internship_months", 0) or 0),
    )

    roles: list[RoleConfig] = []
    for role_data in data.get("roles", []):
        groups: list[ResumeGroup] = []
        for group_data in role_data.get("groups", []):
            indices = tuple(int(item) for item in group_data.get("paragraph_indices", []))
            evidence_ids = tuple(str(item) for item in group_data.get("evidence_ids", []))
            if len(indices) != len(evidence_ids):
                raise ConfigurationError(
                    f"role {role_data.get('name', '')!r} has a resume group with mismatched indices and evidence IDs"
                )
            groups.append(ResumeGroup(indices, evidence_ids))
        base_docx = role_data.get("base_docx")
        if not base_docx:
            raise ConfigurationError(f"role {role_data.get('name', '')!r} requires base_docx")
        roles.append(
            RoleConfig(
                name=str(role_data.get("name", "")),
                keywords=tuple(str(item) for item in role_data.get("keywords", [])),
                evidence_ids=tuple(str(item) for item in role_data.get("evidence_ids", [])),
                base_docx=_resolve(case_dir, str(base_docx)).resolve(),
                groups=tuple(groups),
            )
        )
    if not roles:
        raise ConfigurationError("at least one [[roles]] entry is required")

    matching = data.get("matching", {})
    source_policy = data.get("source_policy", {})
    resume = data.get("resume", {})
    email = data.get("email", {})
    risk = data.get("risk", {})

    return AgentConfig(
        config_path=config_path,
        case_dir=case_dir,
        sources_file=sources_file,
        jobs_file=case_path("jobs_file", "jobs.jsonl"),
        matches_file=case_path("matches_file", "job-matches.csv"),
        tracker_file=case_path("tracker_file", "application-tracker.csv"),
        snapshots_dir=case_path("snapshots_dir", "raw/job-posts/snapshots"),
        extracted_dir=case_path("extracted_dir", "raw/job-posts/extracted"),
        drafts_dir=case_path("drafts_dir", "application-drafts"),
        runs_dir=case_path("runs_dir", "agent-runs"),
        identity=identity,
        preferences=preferences,
        roles=tuple(roles),
        preparation_threshold=int(matching.get("preparation_threshold", 70)),
        fetch_timeout_seconds=int(source_policy.get("timeout_seconds", 20)),
        max_download_bytes=int(source_policy.get("max_download_bytes", 2_000_000)),
        soffice_command=str(resume.get("soffice_command", "soffice")),
        default_email_body=str(email.get("default_body", "")),
        risk_terms=tuple(str(item) for item in risk.get("terms", ["交钱培训", "先贷款", "包就业", "无经验高薪"])),
    )


def doctor(config: AgentConfig) -> list[str]:
    problems: list[str] = []
    if not config.case_dir.exists():
        problems.append(f"case directory does not exist: {config.case_dir}")
    if not config.sources_file.exists():
        problems.append(f"sources file does not exist: {config.sources_file}")
    for role in config.roles:
        if not role.base_docx.exists():
            problems.append(f"base resume does not exist for {role.name}: {role.base_docx}")
    if not config.identity.name:
        problems.append("identity.name is empty")
    if not config.preferences.cities:
        problems.append("preferences.cities is empty")
    return problems


def as_public_summary(config: AgentConfig) -> dict[str, Any]:
    return {
        "case_dir_exists": config.case_dir.exists(),
        "sources_file_exists": config.sources_file.exists(),
        "role_count": len(config.roles),
        "roles": [role.name for role in config.roles],
        "preparation_threshold": config.preparation_threshold,
        "email_sending_available": False,
    }
