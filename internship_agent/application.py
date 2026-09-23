from __future__ import annotations

import json
from email.message import EmailMessage
from pathlib import Path

from .config import AgentConfig
from .models import JobRecord, MatchResult
from .resume import ResumePackage, render_template


def _default_subject(config: AgentConfig, job: JobRecord) -> str:
    parts = [config.identity.name or "姓名待补", config.identity.school, job.title or "岗位待补"]
    return "-".join(part for part in parts if part)


def _subject(config: AgentConfig, job: JobRecord) -> tuple[str, list[str]]:
    if job.email_subject_rule:
        rendered, unresolved = render_template(job.email_subject_rule, config, job)
        if rendered:
            return rendered, unresolved
    return _default_subject(config, job), []


def _body(config: AgentConfig, job: JobRecord) -> str:
    if config.default_email_body:
        rendered, _ = render_template(config.default_email_body, config, job)
        return rendered
    name = config.identity.name or "姓名待补"
    return (
        "尊敬的招聘负责人：\n\n"
        f"您好！我希望申请{job.company or '贵司'}的{job.title or '实习岗位'}。"
        "附件为按招聘要求准备的个人简历，烦请查收。\n\n"
        "感谢您的时间与考虑。\n\n"
        f"{name}"
    )


def create_application_draft(
    config: AgentConfig,
    job: JobRecord,
    match: MatchResult,
    resume: ResumePackage,
) -> dict[str, str]:
    subject, unresolved = _subject(config, job)
    body = _body(config, job)

    message = EmailMessage()
    message["To"] = job.email
    message["Subject"] = subject
    message.set_content(body)
    message.add_attachment(
        resume.attachment_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=resume.attachment_path.name,
    )

    email_markdown = resume.draft_dir / "email.md"
    email_markdown.write_text(
        f"收件人：{job.email}\n\n主题：{subject}\n\n{body}\n\n附件：{resume.attachment_path.name}\n",
        encoding="utf-8",
    )
    eml_path = resume.draft_dir / "email.eml"
    eml_path.write_bytes(message.as_bytes())

    approval = {
        "job_id": job.job_id,
        "recipient": job.email,
        "subject": subject,
        "body_path": str(email_markdown),
        "eml_path": str(eml_path),
        "attachment_path": str(resume.attachment_path),
        "match_score": match.total_score,
        "score_meaning": "岗位整理初筛，不代表录取概率",
        "unresolved_subject_tokens": unresolved,
        "status": "待确认",
        "user_confirmed": False,
        "external_action_performed": False,
    }
    approval_path = resume.draft_dir / "approval.json"
    approval_path.write_text(json.dumps(approval, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "email_markdown": str(email_markdown),
        "eml": str(eml_path),
        "approval": str(approval_path),
    }
