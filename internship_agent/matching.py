from __future__ import annotations

import re

from .config import AgentConfig, RoleConfig
from .models import JobRecord, MatchResult


def _keyword_hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    return [keyword for keyword in keywords if keyword and keyword.lower() in text.lower()]


def choose_role(job: JobRecord, config: AgentConfig) -> RoleConfig:
    text = job.searchable_text
    ranked = sorted(
        config.roles,
        key=lambda role: (
            len(_keyword_hits(text, role.keywords)),
            role.name in config.preferences.target_roles,
        ),
        reverse=True,
    )
    return ranked[0]


def _required_days(text: str) -> int:
    patterns = (
        r"每周(?:至少)?\s*([1-7])\s*天",
        r"一周(?:至少)?\s*([1-7])\s*天",
        r"([1-7])\s*天/周",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return 0


def _required_months(text: str) -> int:
    patterns = (
        r"(?:至少|持续|实习)\s*([1-9]\d*)\s*个?月",
        r"([1-9]\d*)\s*个?月(?:以上|起)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return 0


def score_job(job: JobRecord, config: AgentConfig) -> MatchResult:
    role = choose_role(job, config)
    text = job.searchable_text
    hits = _keyword_hits(text, role.keywords)

    experience_fit = min(40, 8 + 6 * len(hits)) if hits else 8
    interest_direction = 15 if role.name in config.preferences.target_roles else 8

    must_have_gaps: list[str] = []
    unknowns: list[str] = []
    hard_requirements = 20
    practical_constraints = 0

    if job.location:
        if any(city in job.location for city in config.preferences.cities):
            practical_constraints += 5
        elif "远程" not in job.location:
            must_have_gaps.append(f"地点不在目标范围：{job.location}")
            hard_requirements -= 10
    else:
        unknowns.append("工作地点待确认")

    if any(term in text for term in ("在读", "实习", "应届", "硕士", "本科")):
        practical_constraints += 5
    else:
        unknowns.append("学历或在读要求待确认")

    required_days = _required_days(text)
    if required_days:
        if config.preferences.available_days_per_week:
            if config.preferences.available_days_per_week >= required_days:
                practical_constraints += 3
            else:
                must_have_gaps.append(
                    f"每周要求{required_days}天，当前可实习{config.preferences.available_days_per_week}天"
                )
                hard_requirements -= 5
        else:
            unknowns.append(f"每周{required_days}天要求待与个人时间确认")

    required_months = _required_months(text)
    if required_months:
        if config.preferences.internship_months:
            if config.preferences.internship_months >= required_months:
                practical_constraints += 2
            else:
                must_have_gaps.append(
                    f"要求持续{required_months}个月，当前可持续{config.preferences.internship_months}个月"
                )
                hard_requirements -= 5
        else:
            unknowns.append(f"持续{required_months}个月要求待与个人时间确认")

    if not required_days and not required_months:
        unknowns.append("每周天数和实习时长未从JD识别")

    risk_flags = [term for term in config.risk_terms if term and term in text]
    risk_screen = max(0, 10 - 5 * len(risk_flags))
    total = max(
        0,
        min(
            100,
            experience_fit
            + max(0, hard_requirements)
            + interest_direction
            + practical_constraints
            + risk_screen,
        ),
    )
    blocking = bool(must_have_gaps or risk_flags)
    eligible = total >= config.preparation_threshold and not blocking and bool(job.email)
    evidence = list(role.evidence_ids[: max(1, min(len(role.evidence_ids), len(hits) or 1))])

    return MatchResult(
        job_id=job.job_id,
        role_name=role.name,
        total_score=total,
        experience_fit=experience_fit,
        hard_requirements=max(0, hard_requirements),
        interest_direction=interest_direction,
        practical_constraints=practical_constraints,
        risk_screen=risk_screen,
        must_have_gaps=must_have_gaps,
        unknowns=unknowns,
        matching_evidence=evidence,
        resume_focus=hits[:5],
        risk_flags=risk_flags,
        eligible_for_preparation=eligible,
    )
