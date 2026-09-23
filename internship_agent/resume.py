from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from docx import Document

from .config import AgentConfig, RoleConfig
from .models import JobRecord, MatchResult


class ResumeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResumePackage:
    draft_dir: Path
    docx_path: Path
    pdf_path: Path
    attachment_path: Path
    edit_plan_path: Path
    qa_path: Path
    selected_role: str
    evidence_order: tuple[str, ...]


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_component(value: str, limit: int = 60) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", value)
    value = re.sub(r"\s+", "", value).strip("._")
    return (value[:limit] or "未命名")


def _role(config: AgentConfig, name: str) -> RoleConfig:
    for role in config.roles:
        if role.name == name:
            return role
    raise ResumeError(f"unknown role: {name}")


def _paragraph_score(text: str, job_text: str, keywords: tuple[str, ...]) -> int:
    score = 0
    for keyword in keywords:
        if keyword and keyword in text and keyword in job_text:
            score += 3
        elif keyword and keyword in text:
            score += 1
    return score


def reorder_resume_copy(
    base_docx: Path,
    output_docx: Path,
    groups,
    job_text: str,
    keywords: tuple[str, ...],
) -> tuple[str, ...]:
    if not base_docx.exists():
        raise ResumeError(f"base resume does not exist: {base_docx}")
    base_hash = _file_hash(base_docx)
    document = Document(str(base_docx))
    evidence_order: list[str] = []

    for group in groups:
        paragraphs = document.paragraphs
        if any(index < 0 or index >= len(paragraphs) for index in group.paragraph_indices):
            raise ResumeError(f"resume group contains an invalid paragraph index: {group.paragraph_indices}")
        entries = []
        for position, (index, evidence_id) in enumerate(zip(group.paragraph_indices, group.evidence_ids)):
            paragraph = paragraphs[index]
            entries.append(
                (
                    _paragraph_score(paragraph.text, job_text, keywords),
                    position,
                    evidence_id,
                    paragraph._p,
                )
            )
        ordered = sorted(entries, key=lambda item: (-item[0], item[1]))
        parent = entries[0][3].getparent()
        insertion_index = min(parent.index(item[3]) for item in entries)
        for item in entries:
            parent.remove(item[3])
        for offset, item in enumerate(ordered):
            parent.insert(insertion_index + offset, item[3])
            evidence_order.append(item[2])

    output_docx.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_docx))
    if _file_hash(base_docx) != base_hash:
        raise ResumeError("base resume changed during generation")
    return tuple(evidence_order)


def convert_to_pdf(docx_path: Path, output_dir: Path, soffice_command: str) -> Path:
    command = shutil.which(soffice_command) if "/" not in soffice_command else soffice_command
    if not command or not Path(command).exists():
        raise ResumeError(f"LibreOffice command not found: {soffice_command}")
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [command, "--headless", "--convert-to", "pdf", "--outdir", str(output_dir), str(docx_path)],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    pdf_path = output_dir / f"{docx_path.stem}.pdf"
    if result.returncode != 0 or not pdf_path.exists() or pdf_path.stat().st_size == 0:
        detail = (result.stderr or result.stdout or "no converter output").strip()
        raise ResumeError(f"DOCX to PDF conversion failed: {detail}")
    return pdf_path


def verify_pdf(pdf_path: Path) -> dict[str, object]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise ResumeError("pdfplumber is required for PDF verification") from exc
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        extracted_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        page_sizes = [[round(page.width, 2), round(page.height, 2)] for page in pdf.pages]
    if page_count < 1:
        raise ResumeError("generated PDF has no pages")
    if not extracted_text.strip():
        raise ResumeError("generated PDF contains no extractable text")
    return {
        "page_count": page_count,
        "page_sizes_points": page_sizes,
        "extractable_text": True,
        "visual_review_required": True,
    }


def render_pdf_preview(pdf_path: Path, output_dir: Path) -> tuple[list[str], str]:
    command = shutil.which("pdftoppm")
    if not command:
        return [], "pdftoppm command not found"
    preview_dir = output_dir / "qa"
    preview_dir.mkdir(parents=True, exist_ok=True)
    prefix = preview_dir / "resume"
    result = subprocess.run(
        [command, "-png", "-r", "150", str(pdf_path), str(prefix)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    paths = sorted(str(path) for path in preview_dir.glob("resume-*.png"))
    if result.returncode != 0 or not paths:
        detail = (result.stderr or result.stdout or "preview was not produced").strip()
        return [], detail
    return paths, ""


def render_template(value: str, config: AgentConfig, job: JobRecord) -> tuple[str, list[str]]:
    replacements = {
        "最快到岗时间": config.identity.availability,
        "可实习时间": config.identity.availability,
        "姓名": config.identity.name,
        "学校": config.identity.school,
        "专业": config.identity.major,
        "年级": config.identity.grade,
        "公司": job.company,
        "岗位": job.title,
        "职位": job.title,
    }
    result = value
    unresolved: list[str] = []
    for token, replacement in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if token in result:
            if replacement:
                result = result.replace(token, replacement)
            else:
                unresolved.append(token)
    result = result.strip(" \t。；;")
    return result, unresolved


def attachment_filename(config: AgentConfig, job: JobRecord) -> tuple[str, list[str]]:
    if job.attachment_naming_rule:
        rendered, unresolved = render_template(job.attachment_naming_rule, config, job)
        rendered = re.sub(r"^(?:格式|例如|示例)\s*[：:]\s*", "", rendered)
        rendered = _safe_component(Path(rendered).stem, 120) + ".pdf"
        if not unresolved:
            return rendered, []
    else:
        unresolved = []
    fallback = "_".join(
        _safe_component(part)
        for part in (config.identity.name or "姓名待补", job.company or "公司待补", job.title or "岗位待补")
    )
    return f"{fallback}.pdf", unresolved


def build_resume_package(
    config: AgentConfig,
    job: JobRecord,
    match: MatchResult,
) -> ResumePackage:
    role = _role(config, match.role_name)
    draft_dir = config.drafts_dir / job.job_id
    attachment_dir = draft_dir / "attachments"
    attachment_dir.mkdir(parents=True, exist_ok=True)

    stem = "_".join(
        _safe_component(part)
        for part in (
            config.identity.name or "姓名待补",
            job.company or "公司待补",
            job.title or "岗位待补",
            date.today().isoformat(),
            "v01",
        )
    )
    docx_path = draft_dir / f"{stem}.docx"
    evidence_order = reorder_resume_copy(
        role.base_docx,
        docx_path,
        role.groups,
        job.searchable_text,
        role.keywords,
    )
    generated_pdf = convert_to_pdf(docx_path, draft_dir, config.soffice_command)
    qa = verify_pdf(generated_pdf)
    preview_paths, preview_error = render_pdf_preview(generated_pdf, draft_dir)
    qa.update(
        {
            "base_resume": str(role.base_docx),
            "base_sha256": _file_hash(role.base_docx),
            "generated_docx_sha256": _file_hash(docx_path),
            "generated_pdf_sha256": _file_hash(generated_pdf),
            "preview_paths": preview_paths,
            "preview_render_error": preview_error,
            "visual_review_status": "待人工检查",
        }
    )

    delivery_name, unresolved = attachment_filename(config, job)
    attachment_path = attachment_dir / delivery_name
    shutil.copy2(generated_pdf, attachment_path)
    qa["attachment_naming_unresolved"] = unresolved
    qa_path = draft_dir / "resume-qa.json"
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")

    edit_plan = {
        "job_id": job.job_id,
        "role": role.name,
        "allowed_operations": ["reorder", "compress", "rewrite", "emphasize"],
        "v1_automatic_operations": ["role_template_selection", "evidence_linked_reordering", "attachment_renaming"],
        "evidence_order": evidence_order,
        "matching_evidence": match.matching_evidence,
        "resume_focus": match.resume_focus,
        "must_have_gaps": match.must_have_gaps,
        "unknowns": match.unknowns,
        "requires_user_review": True,
    }
    edit_plan_path = draft_dir / "resume-edit-plan.json"
    edit_plan_path.write_text(json.dumps(edit_plan, ensure_ascii=False, indent=2), encoding="utf-8")

    return ResumePackage(
        draft_dir=draft_dir,
        docx_path=docx_path,
        pdf_path=generated_pdf,
        attachment_path=attachment_path,
        edit_plan_path=edit_plan_path,
        qa_path=qa_path,
        selected_role=role.name,
        evidence_order=evidence_order,
    )
