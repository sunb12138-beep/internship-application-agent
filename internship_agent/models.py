from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceDefinition:
    source_id: str
    source_type: str
    value: str
    name: str = ""
    enabled: bool = True
    tags: tuple[str, ...] = ()


@dataclass
class JobRecord:
    job_id: str
    source_id: str
    source_type: str
    source_name: str
    source_url: str
    observed_at: str
    raw_path: str
    extracted_path: str
    source_hash: str
    company: str = ""
    title: str = ""
    location: str = ""
    requirements: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    deadline: str = ""
    email: str = ""
    email_subject_rule: str = ""
    attachment_naming_rule: str = ""
    needs_review: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "JobRecord":
        known = {item.name for item in cls.__dataclass_fields__.values()}
        return cls(**{key: val for key, val in value.items() if key in known})

    @property
    def searchable_text(self) -> str:
        parts = [
            self.company,
            self.title,
            self.location,
            *self.requirements,
            *self.responsibilities,
        ]
        return "\n".join(part for part in parts if part)


@dataclass
class MatchResult:
    job_id: str
    role_name: str
    total_score: int
    experience_fit: int
    hard_requirements: int
    interest_direction: int
    practical_constraints: int
    risk_screen: int
    must_have_gaps: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    matching_evidence: list[str] = field(default_factory=list)
    resume_focus: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    eligible_for_preparation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
