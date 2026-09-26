import json
import re
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path

from reportlab.lib.colors import black
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer

from ai.job_scorer import fetch_job_description, jd_for_resume
from ai.llm import extract_json, generate
from ai.resume_builder import (
    _contact_xml,
    _safe_filename,
    _skill_tokens,
    _split_dates,
    _xml,
    parse_master_resume,
)
from config import COVER_LETTERS_OUTPUT_DIR, MASTER_RESUME_PATH, USER_PROFILE

COVER_SYSTEM = (
    "You write a 3-paragraph cover letter from the fact pack only. "
    "NEVER invent employers, titles, dates, skills, or metrics. Return ONLY JSON."
)

COVER_PROMPT = """ROLE: {title} at {company}
CANDIDATE: {identity}
{summary_line}SKILLS: {skills}
HIGHLIGHTS:
{highlights}

Write 3 body paragraphs for this role. Each at most 4 sentences (~90 words).
Use only the candidate facts given. No greeting or closing.

Return ONLY: {{"p":["para1","para2","para3"]}}

JOB:
{job_description}
"""

_JD_CHARS = 1000
_MAX_SKILLS = 12
_MAX_HIGHLIGHTS = 8
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_MASTER_RESUME_PDF = MASTER_RESUME_PATH.with_suffix(".pdf")
_MARGIN = 65
_INDIA_PLACE = re.compile(
    r"\b("
    r"india|bharat|"
    r"andhra pradesh|arunachal pradesh|assam|bihar|chhattisgarh|goa|gujarat|"
    r"haryana|himachal pradesh|jharkhand|karnataka|kerala|madhya pradesh|"
    r"maharashtra|manipur|meghalaya|mizoram|nagaland|odisha|orissa|punjab|"
    r"rajasthan|sikkim|tamil nadu|telangana|tripura|uttar pradesh|uttarakhand|"
    r"west bengal|delhi|new delhi|ncr|"
    r"bengaluru|bangalore|mumbai|bombay|hyderabad|chennai|madras|pune|"
    r"kolkata|calcutta|ahmedabad|jaipur|lucknow|kanpur|nagpur|indore|"
    r"bhopal|patna|coimbatore|kochi|cochin|thiruvananthapuram|trivandrum|"
    r"visakhapatnam|mysore|mysuru|noida|gurugram|gurgaon|ghaziabad|"
    r"chandigarh|navi mumbai|thane"
    r")\b",
    re.I,
)


def _identity(template: dict) -> str:
    name = (template.get("name") or USER_PROFILE.get("name") or "").strip()
    years = USER_PROFILE.get("experience_years", "")
    title = ""
    experience = template.get("experience") or []
    if experience:
        left, _ = _split_dates(str(experience[0].get("header") or ""))
        title = left.split("|")[0].strip() if "|" in left else left.strip()
    parts = [name]
    if years != "":
        parts.append(f"{years}y")
    if title:
        parts.append(title)
    return ", ".join(part for part in parts if part)


def _matched_skills(template: dict, job_description: str, limit: int = _MAX_SKILLS) -> str:
    jd_tokens = _skill_tokens(job_description)
    matched: list[str] = []
    rest: list[str] = []
    for group in template.get("skill_groups") or []:
        for item in group.get("items") or []:
            text = str(item).strip()
            if not text:
                continue
            if _skill_tokens(text) & jd_tokens:
                matched.append(text)
            else:
                rest.append(text)
    picked = matched[:limit]
    if len(picked) < 6:
        for item in rest:
            if len(picked) >= min(8, limit):
                break
            picked.append(item)
    return ", ".join(picked[:limit])


def _rank_highlights(template: dict, job_description: str, limit: int = _MAX_HIGHLIGHTS) -> list[str]:
    jd_tokens = _skill_tokens(job_description)
    scored: list[tuple[float, int, str, str]] = []
    index = 0
    for job in template.get("experience") or []:
        header = str(job.get("header") or "").strip()
        header_bonus = 0.25 * len(_skill_tokens(header) & jd_tokens)
        for bullet in job.get("bullets") or []:
            text = str(bullet).strip()
            if not text:
                continue
            overlap = len(_skill_tokens(text) & jd_tokens) + header_bonus
            scored.append((overlap, index, header, text))
            index += 1
    scored.sort(key=lambda row: (-row[0], row[1]))
    lines = []
    for _, _, header, bullet in scored[:limit]:
        prefix = f"{header}: " if header else ""
        lines.append(f"{prefix}{bullet}")
    return lines


def _project_highlight(template: dict, job_description: str) -> str:
    jd_tokens = _skill_tokens(job_description)
    best = ""
    best_score = 0
    for project in template.get("projects") or []:
        header = str(project.get("header") or "").strip()
        header_tokens = _skill_tokens(header)
        for bullet in project.get("bullets") or []:
            text = str(bullet).strip()
            if not text:
                continue
            score = len((header_tokens | _skill_tokens(text)) & jd_tokens)
            if score > best_score:
                best_score = score
                best = f"{header}: {text}" if header else text
    return best if best_score > 0 else ""


def _fact_pack(template: dict, job: dict, job_description: str) -> dict[str, str]:
    highlights = _rank_highlights(template, job_description)
    project = _project_highlight(template, job_description)
    if project:
        highlights = highlights[: _MAX_HIGHLIGHTS - 1] + [project]
    summary = str(template.get("summary") or "").strip()
    return {
        "title": str(job.get("title") or "Unknown").strip() or "Unknown",
        "company": str(job.get("company") or "Unknown").strip() or "Unknown",
        "identity": _identity(template),
        "summary_line": f"SUMMARY: {summary}\n" if summary else "",
        "skills": _matched_skills(template, job_description),
        "highlights": "\n".join(f"- {line}" for line in highlights) or "- (none)",
        "job_description": job_description,
    }


def _normalize_paragraphs(payload) -> list[str]:
    raw = payload.get("p") if isinstance(payload, dict) else None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("Cover letter response missing paragraphs")
    paragraphs = [str(item).strip() for item in raw if str(item).strip()]
    if not paragraphs:
        raise ValueError("Cover letter was empty")
    return paragraphs[:3]


def _trim_last_sentence(paragraphs: list[str]) -> bool:
    for index in range(len(paragraphs) - 1, -1, -1):
        text = (paragraphs[index] or "").strip()
        if not text:
            paragraphs.pop(index)
            return True
        parts = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]
        if len(parts) > 1:
            paragraphs[index] = " ".join(parts[:-1]).strip()
            return True
        paragraphs.pop(index)
        return True
    return False


def _letter_styles(font_size: float, leading: float, space_after: float) -> dict[str, ParagraphStyle]:
    return {
        "name": ParagraphStyle(
            "CoverName",
            fontName="Times-Bold",
            fontSize=14,
            leading=16,
            alignment=TA_CENTER,
            textColor=black,
            spaceAfter=1,
        ),
        "contact": ParagraphStyle(
            "CoverContact",
            fontName="Times-Roman",
            fontSize=9,
            leading=11,
            alignment=TA_CENTER,
            textColor=black,
            spaceAfter=10,
        ),
        "date": ParagraphStyle(
            "CoverDate",
            fontName="Times-Roman",
            fontSize=font_size,
            leading=leading,
            alignment=TA_RIGHT,
            textColor=black,
            spaceAfter=14,
        ),
        "recipient": ParagraphStyle(
            "CoverRecipient",
            fontName="Times-Roman",
            fontSize=font_size,
            leading=leading,
            alignment=TA_LEFT,
            textColor=black,
            spaceAfter=2,
        ),
        "greeting": ParagraphStyle(
            "CoverGreeting",
            fontName="Times-Roman",
            fontSize=font_size,
            leading=leading,
            alignment=TA_LEFT,
            textColor=black,
            spaceBefore=12,
            spaceAfter=12,
        ),
        "body": ParagraphStyle(
            "CoverBody",
            fontName="Times-Roman",
            fontSize=font_size,
            leading=leading,
            alignment=TA_JUSTIFY,
            textColor=black,
            spaceAfter=space_after,
        ),
        "closing": ParagraphStyle(
            "CoverClosing",
            fontName="Times-Roman",
            fontSize=font_size,
            leading=leading,
            alignment=TA_LEFT,
            textColor=black,
            spaceBefore=6,
            spaceAfter=0,
        ),
        "signature": ParagraphStyle(
            "CoverSignature",
            fontName="Times-Bold",
            fontSize=font_size,
            leading=leading,
            alignment=TA_LEFT,
            textColor=black,
        ),
    }


def _job_is_outside_india(job: dict) -> bool:
    location = str(job.get("location") or "").strip()
    if not location:
        return False
    return not bool(_INDIA_PLACE.search(location))


def _contact_line_for_job(contact: str, job: dict) -> str:
    text = (contact or "").strip()
    if not text or not _job_is_outside_india(job):
        return text
    parts = [part.strip() for part in text.split("|") if part.strip()]
    if not parts:
        return text
    first = parts[0]
    if re.search(r"\bindia\b", first, re.I):
        return " | ".join(parts)
    parts[0] = f"{first}, India"
    return " | ".join(parts)


def _format_date(date_str: str) -> str:
    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        parsed = datetime.now()
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.strftime('%Y')}"


def _letter_story(
    template: dict,
    job: dict,
    paragraphs: list[str],
    date_str: str,
    font_size: float,
    leading: float,
    space_after: float,
) -> list:
    styles = _letter_styles(font_size, leading, space_after)
    links = template.get("links") or {}
    name = str(template.get("name") or "").strip()
    contact = _contact_line_for_job(str(template.get("contact_line") or "").strip(), job)
    company = str(job.get("company") or "").strip()
    title = str(job.get("title") or "").strip()
    story = []
    if name:
        story.append(Paragraph(_xml(name), styles["name"]))
    if contact:
        story.append(Paragraph(_contact_xml(contact, links), styles["contact"]))
    story.append(Paragraph(_xml(_format_date(date_str)), styles["date"]))
    if company:
        story.append(Paragraph(_xml(company), styles["recipient"]))
    if title:
        story.append(Paragraph(_xml(title), styles["recipient"]))
    story.append(Paragraph(_xml("Dear Hiring Manager,"), styles["greeting"]))
    for paragraph in paragraphs:
        story.append(Paragraph(_xml(paragraph), styles["body"]))
    story.append(Paragraph(_xml("Sincerely,"), styles["closing"]))
    story.append(Spacer(1, 16))
    if name:
        story.append(Paragraph(_xml(name), styles["signature"]))
    return story


def _render_pdf_bytes(
    template: dict,
    job: dict,
    paragraphs: list[str],
    date_str: str,
    font_size: float,
    leading: float,
    space_after: float,
) -> bytes:
    buf = BytesIO()
    name = str(template.get("name") or "").strip()
    page_w, page_h = letter
    frame = Frame(
        _MARGIN,
        _MARGIN,
        page_w - (2 * _MARGIN),
        page_h - (2 * _MARGIN),
        id="cover",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        showBoundary=0,
    )
    doc = BaseDocTemplate(
        buf,
        pagesize=letter,
        title=f"Cover Letter — {job.get('company', '')} {job.get('title', '')}".strip(),
        author=name,
    )
    doc.addPageTemplates([PageTemplate(id="onepage", frames=[frame])])
    doc.build(_letter_story(template, job, paragraphs, date_str, font_size, leading, space_after))
    return buf.getvalue()


def _pdf_page_count(data: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(BytesIO(data)).pages)


def write_cover_letter_pdf(
    template: dict,
    job: dict,
    paragraphs: list[str],
    path: Path,
    date_str: str,
) -> None:
    body = [item.strip() for item in paragraphs if str(item).strip()]
    if not body:
        raise ValueError("Cover letter has no paragraphs")

    variants = ((11, 14, 10), (10.5, 13, 8), (10, 12, 6))
    pdf_bytes = b""
    for font_size, leading, space_after in variants:
        pdf_bytes = _render_pdf_bytes(
            template, job, body, date_str, font_size, leading, space_after
        )
        if _pdf_page_count(pdf_bytes) <= 1:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pdf_bytes)
            return

    while _pdf_page_count(pdf_bytes) > 1:
        if not _trim_last_sentence(body):
            break
        if not body:
            break
        pdf_bytes = _render_pdf_bytes(template, job, body, date_str, 10, 12, 6)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes)
    if _pdf_page_count(pdf_bytes) > 1:
        raise RuntimeError("Cover letter could not be fitted to one page")


def _cover_stem(job: dict, date_str: str, unique_id: str | None = None) -> str:
    company = _safe_filename(job.get("company", "Unknown"))
    title = _safe_filename(job.get("title", "Unknown"))
    if unique_id:
        return f"{company}_{title}_{date_str}_{unique_id}"
    return f"{company}_{title}_{date_str}"


def _master_mtime() -> float:
    times = []
    for path in (MASTER_RESUME_PATH, _MASTER_RESUME_PDF, Path(__file__)):
        try:
            times.append(path.stat().st_mtime)
        except OSError:
            continue
    return max(times) if times else 0


def _cached_cover_pdf(job: dict, date_str: str) -> Path | None:
    pdf_path = COVER_LETTERS_OUTPUT_DIR / f"{_cover_stem(job, date_str)}.pdf"
    if not pdf_path.exists():
        return None
    if pdf_path.stat().st_mtime <= _master_mtime():
        return None
    return pdf_path


def build_cover_letter(job: dict, *, persist: bool = True, use_cache: bool = True) -> str:
    date_str = datetime.now().strftime("%Y-%m-%d")
    unique_id = None if use_cache else uuid.uuid4().hex[:8]
    if use_cache:
        cached = _cached_cover_pdf(job, date_str)
        if cached is not None:
            if persist and job.get("job_id"):
                from jobs.job_store import save_job_cover_letter

                save_job_cover_letter(job["job_id"], str(cached))
            return str(cached)

    template = parse_master_resume()
    job_description = jd_for_resume(fetch_job_description(job), _JD_CHARS)
    pack = _fact_pack(template, job, job_description)
    response_text = generate(
        COVER_SYSTEM,
        COVER_PROMPT.format(**pack),
        max_tokens=500,
        json_mode=True,
        purpose="resume",
    )
    paragraphs = _normalize_paragraphs(extract_json(response_text))
    COVER_LETTERS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = _cover_stem(job, date_str, unique_id)
    pdf_path = COVER_LETTERS_OUTPUT_DIR / f"{stem}.pdf"
    write_cover_letter_pdf(template, job, paragraphs, pdf_path, date_str)
    meta_path = COVER_LETTERS_OUTPUT_DIR / f"{stem}_meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "job_url": job.get("job_url", ""),
                "company": job.get("company", ""),
                "title": job.get("title", ""),
                "date": date_str,
                "pdf_path": str(pdf_path),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if persist and job.get("job_id"):
        from jobs.job_store import save_job_cover_letter

        save_job_cover_letter(job["job_id"], str(pdf_path))

    return str(pdf_path)
