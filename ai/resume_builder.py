import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import black
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageTemplate, Paragraph, Table, TableStyle

from ai.ats_optimizer import (
    align_summary_years,
    boost_resume_skills,
    jd_min_years,
    prepare_skills,
    scrub_jd_prose,
    shorten_text,
)
from ai.job_scorer import fetch_job_description, jd_for_resume
from ai.llm import extract_json, generate
from config import (
    EXPERIENCE_YEARS,
    MASTER_RESUME_PATH,
    RESUME_MIN_ATS_SCORE,
    RESUMES_OUTPUT_DIR,
)

ATS_META_VERSION = 2

RESUME_SYSTEM = (
    "You tailor a resume for one job. Rewrite the summary and existing bullets only. "
    "NEVER invent employers, titles, dates, education, or metrics. "
    "Do NOT copy job titles, section headings, or JD fragments. "
    "Do NOT list skills as 'specializing in X principles'. "
    "Do NOT bolt new tools onto unrelated bullets. "
    "The Skills section already lists tools; keep prose as true accomplishments. "
    "Keep the summary to 2 short sentences and each bullet to one concise line. "
    "Return ONLY JSON."
)

RESUME_SYSTEM_NO_SUMMARY = (
    "You tailor a resume for one job. Rewrite existing bullets only. "
    "Do not write, invent, or return a summary. "
    "NEVER invent employers, titles, dates, education, or metrics. "
    "Do NOT copy job titles, section headings, or JD fragments. "
    "Do NOT list skills as 'specializing in X principles'. "
    "Do NOT bolt new tools onto unrelated bullets. "
    "The Skills section already lists tools; keep prose as true accomplishments. "
    "Keep each bullet to one concise line. "
    "Return ONLY JSON."
)

RESUME_PROMPT = """CURRENT SUMMARY:
{summary}

SKILLS (already on the resume; do not dump this list into the summary or bullets):
{skills}

EXPERIENCE BULLETS (rewrite in place; keep the same ids):
{bullets}

Rewrite the summary (2 short sentences from true facts) and each bullet so they stay truthful.
Mirror the job's language only when the original bullet already describes that kind of work.
If the job requires {min_years}+ years, the summary must say {min_years}+ years (never a lower number).
Do not add, drop, or reorder ids. Do not invent metrics, employers, or security architecture.

Return ONLY:
{{"summary":"<2 sentences>","bullets":{{"e0b0":"<rewritten>"}}}}

JOB TITLE: {job_title}
COMPANY: {company_name}

JOB:
{job_description}
"""

RESUME_PROMPT_NO_SUMMARY = """SKILLS (already on the resume; do not dump this list into the bullets):
{skills}

EXPERIENCE BULLETS (rewrite in place; keep the same ids):
{bullets}

Rewrite each bullet so it stays truthful. Do not write a summary.
Mirror the job's language only when the original bullet already describes that kind of work.
Do not add, drop, or reorder ids. Do not invent metrics, employers, or security architecture.

Return ONLY:
{{"bullets":{{"e0b0":"<rewritten>"}}}}

JOB TITLE: {job_title}
COMPANY: {company_name}

JOB:
{job_description}
"""

MASTER_RESUME_PDF = MASTER_RESUME_PATH.with_suffix(".pdf")

_HEADING = re.compile(
    r"^(SUMMARY|SKILLS|EXPERIENCE|PERSONAL PROJECTS?|PROJECTS?|EDUCATION|CERTIFICATIONS)\s*$",
    re.I,
)
_BULLET = re.compile(r"^[\u2022•\-\*]\s+")
_DATE_TAIL = re.compile(
    r"^(.*?)\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}"
    r"\s*[–\-]\s*(?:Present|[A-Za-z]{3,9}\.?\s+\d{4}))\s*$",
    re.I,
)
_VISIBLE_URL = re.compile(
    r"(https?://[^\s|<]+|(?:github|gitlab|linkedin|leetcode)\.com/[^\s|<]+)",
    re.I,
)
_URL_IN_TEXT = re.compile(
    r"(https?://[^\s|]+|(?:www\.)?(?:github|gitlab|linkedin|leetcode)\.com/[^\s|]+)",
    re.I,
)
_SKILL_STOP = {
    "the",
    "and",
    "or",
    "of",
    "for",
    "with",
    "to",
    "in",
    "on",
    "a",
    "an",
    "as",
    "by",
    "at",
    "from",
    "your",
    "you",
    "our",
    "we",
    "is",
    "are",
    "be",
}


def _read_master_resume() -> str:
    if not MASTER_RESUME_PATH.exists():
        raise FileNotFoundError(f"Master resume not found at {MASTER_RESUME_PATH}")
    return MASTER_RESUME_PATH.read_text(encoding="utf-8").strip()


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text)
    cleaned = re.sub(r"[\s]+", "_", cleaned.strip())
    return cleaned[:80] or "unknown"


def _string_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(item).strip() for item in value if str(item).strip()]


def _is_heading(line: str) -> bool:
    return bool(_HEADING.match(line.strip()))


def _is_bullet(line: str) -> bool:
    return bool(_BULLET.match(line.strip()))


def _bullet_text(line: str) -> str:
    return _BULLET.sub("", line.strip()).strip()


def _split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"_header": []}
    current = "_header"
    for raw in text.splitlines():
        line = raw.rstrip()
        if _is_heading(line):
            current = _HEADING.match(line.strip()).group(1).upper()
            if current.startswith("PERSONAL PROJECT") or current.startswith("PROJECT"):
                current = "PROJECTS"
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _parse_skill_groups(lines: list[str]) -> list[dict]:
    groups = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if ":" in text:
            name, rest = text.split(":", 1)
            if 0 < len(name.strip()) <= 40:
                items = [item.strip() for item in rest.split(",") if item.strip()]
                groups.append({"name": name.strip(), "items": items or [rest.strip()]})
                continue
        items = [item.strip() for item in text.split(",") if item.strip()]
        groups.append({"name": "", "items": items or [text]})
    return groups


def _parse_jobs(lines: list[str]) -> list[dict]:
    jobs: list[dict] = []
    current: dict | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _is_bullet(stripped):
            if current is None:
                current = {"header": "", "subheader": "", "bullets": []}
                jobs.append(current)
            current["bullets"].append(_bullet_text(stripped))
            continue
        if current is not None and not current["bullets"] and not current["subheader"]:
            current["subheader"] = stripped
            continue
        current = {"header": stripped, "subheader": "", "bullets": []}
        jobs.append(current)

    return [job for job in jobs if job.get("header") or job.get("bullets")]


def _pdf_uris(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        from pypdf import PdfReader
    except ImportError:
        return []
    uris: list[str] = []
    reader = PdfReader(str(path))
    for page in reader.pages:
        for annot in page.get("/Annots") or []:
            obj = annot.get_object()
            action = obj.get("/A")
            if action is None:
                continue
            action = action.get_object() if hasattr(action, "get_object") else action
            uri = str(action.get("/URI") or "").strip()
            if uri and uri not in uris:
                uris.append(uri)
    return uris


def _normalize_url(uri: str) -> str:
    text = (uri or "").strip()
    if not text:
        return ""
    text = re.sub(r"^https?://", "", text, flags=re.I)
    text = re.sub(r"^www\.", "", text, flags=re.I)
    text = text.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    if "/" in text:
        host, path = text.split("/", 1)
        return f"{host.lower()}/{path}"
    return text.lower()


def _url_host_path(uri: str) -> tuple[str, list[str]]:
    norm = _normalize_url(uri)
    if not norm:
        return "", []
    parts = [part for part in norm.split("/") if part]
    host = parts[0].lower() if parts else ""
    return host, parts[1:]


def _is_github_host(host: str) -> bool:
    return host == "github.com" or host.endswith(".github.com")


def _contact_label_for(uri: str) -> str | None:
    host, path = _url_host_path(uri)
    if "linkedin.com" in host:
        return "LinkedIn"
    if "leetcode.com" in host:
        return "LeetCode"
    if _is_github_host(host) and len(path) <= 1:
        return "GitHub"
    return None


def _looks_like_url(text: str) -> bool:
    value = (text or "").strip()
    if re.match(r"https?://", value, re.I):
        return True
    if re.match(r"(github|gitlab|linkedin|leetcode)\.com/", value, re.I):
        return True
    return bool(re.match(r"(www\.)?[a-z0-9.-]+\.[a-z]{2,}/.+$", value, re.I))


def _href_for(visible: str, links: dict[str, str] | None = None) -> str:
    value = (visible or "").strip()
    if not value:
        return ""
    mapping = links or {}
    if value in mapping:
        return mapping[value]
    norm = _normalize_url(value)
    if norm and norm in mapping:
        return mapping[norm]
    if _looks_like_url(value):
        return value if re.match(r"https?://", value, re.I) else f"https://{value}"
    return ""


def _collect_text_uris(text: str | None) -> list[str]:
    uris: list[str] = []
    if not text:
        return uris
    for match in _URL_IN_TEXT.finditer(text):
        raw = match.group(0).rstrip(".,);")
        uri = raw if re.match(r"https?://", raw, re.I) else f"https://{raw}"
        if uri not in uris:
            uris.append(uri)
    return uris


def load_resume_links(pdf_path: Path | None = None, text: str | None = None) -> dict[str, str]:
    uris: list[str] = []
    for uri in _pdf_uris(pdf_path or MASTER_RESUME_PDF):
        if uri not in uris:
            uris.append(uri)
    for uri in _collect_text_uris(text):
        if uri not in uris:
            uris.append(uri)

    links: dict[str, str] = {}
    leftover: list[str] = []
    for uri in uris:
        label = _contact_label_for(uri)
        if label:
            links.setdefault(label, uri)
        elif not _is_github_host(_url_host_path(uri)[0]):
            leftover.append(uri)
        display = _normalize_url(uri)
        if display:
            links.setdefault(display, uri)
    if leftover:
        links.setdefault("Portfolio", leftover[0])
    return links


def parse_master_resume(text: str | None = None) -> dict:
    raw = (text if text is not None else _read_master_resume()).strip()
    sections = _split_sections(raw)
    header_lines = [line.strip() for line in sections.get("_header", []) if line.strip()]
    name = header_lines[0] if header_lines else ""
    contact = header_lines[1] if len(header_lines) > 1 else " | ".join(header_lines[1:])

    summary = " ".join(
        line.strip() for line in sections.get("SUMMARY", []) if line.strip()
    )
    skill_groups = _parse_skill_groups(sections.get("SKILLS", []))
    experience = _parse_jobs(sections.get("EXPERIENCE", []))
    projects = _parse_jobs(sections.get("PROJECTS", []))
    education = [line.strip() for line in sections.get("EDUCATION", []) if line.strip()]
    cert_lines = [line.strip() for line in sections.get("CERTIFICATIONS", []) if line.strip()]
    certifications: list[str] = []
    for line in cert_lines:
        certifications.extend(part.strip() for part in line.split("|") if part.strip())

    return {
        "name": name,
        "contact_line": contact,
        "summary": summary,
        "skill_groups": skill_groups,
        "experience": experience,
        "projects": projects,
        "education": education,
        "certifications": certifications,
        "links": load_resume_links(text=raw),
    }


def _skill_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9.+#]{1,}", (text or "").lower())
    return {word for word in words if word not in _SKILL_STOP}


def rank_skill_lines(groups: list[dict], job_description: str) -> list[str]:
    jd_tokens = _skill_tokens(job_description)
    ranked = []
    for index, group in enumerate(groups):
        matched = []
        rest = []
        for item in group.get("items") or []:
            if _skill_tokens(item) & jd_tokens:
                matched.append(item)
            else:
                rest.append(item)
        score = len(matched)
        if _skill_tokens(group.get("name") or "") & jd_tokens:
            score += 1
        ranked.append((score, index, group, matched + rest))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    lines = []
    for _, _, group, items in ranked:
        joined = ", ".join(items)
        name = (group.get("name") or "").strip()
        lines.append(f"{name}: {joined}" if name else joined)
    return lines


def _bullet_prompt_block(experience: list[dict]) -> str:
    lines = []
    for job_index, job in enumerate(experience):
        header = (job.get("header") or "").strip()
        if header:
            lines.append(f"e{job_index} {header}")
        for bullet_index, bullet in enumerate(job.get("bullets") or []):
            lines.append(f"e{job_index}b{bullet_index}: {bullet}")
    return "\n".join(lines)


def _merge_patch(template: dict, patch: dict, job_title: str = "", company: str = "") -> dict:
    template_summary = str(template.get("summary") or "").strip()
    if template_summary:
        summary = str(patch.get("summary") or "").strip() or template_summary
        summary = scrub_jd_prose(summary, job_title, company)
    else:
        summary = ""
    raw_bullets = patch.get("bullets") if isinstance(patch, dict) else None
    if not isinstance(raw_bullets, dict):
        raw_bullets = {}

    experience = []
    for job_index, job in enumerate(template["experience"]):
        merged = []
        for bullet_index, original in enumerate(job.get("bullets") or []):
            key = f"e{job_index}b{bullet_index}"
            rewritten = str(raw_bullets.get(key) or "").strip()
            text = rewritten or original
            merged.append(scrub_jd_prose(text, job_title, company) or original)
        experience.append(
            {
                "header": job.get("header") or "",
                "subheader": job.get("subheader") or "",
                "bullets": merged,
            }
        )

    return {
        "name": template["name"],
        "contact_line": template["contact_line"],
        "summary": summary,
        "skills": template["skills"],
        "experience": experience,
        "projects": template["projects"],
        "education": template["education"],
        "certifications": template["certifications"],
        "links": template.get("links") or {},
    }


def _styles(compact: bool = False) -> dict[str, ParagraphStyle]:
    shrink = 0.6 if compact else 0
    return {
        "name": ParagraphStyle(
            "ResumeName",
            fontName="Times-Bold",
            fontSize=14 - shrink,
            leading=16 - shrink,
            alignment=TA_CENTER,
            textColor=black,
            spaceAfter=1,
        ),
        "contact": ParagraphStyle(
            "ResumeContact",
            fontName="Times-Roman",
            fontSize=9 - shrink * 0.4,
            leading=11 - shrink,
            alignment=TA_CENTER,
            textColor=black,
            spaceAfter=1,
        ),
        "heading": ParagraphStyle(
            "ResumeHeading",
            fontName="Times-Bold",
            fontSize=10.5 - shrink,
            leading=12 - shrink,
            alignment=TA_LEFT,
            textColor=black,
            spaceBefore=0 if compact else 1,
            spaceAfter=0,
        ),
        "body": ParagraphStyle(
            "ResumeBody",
            fontName="Times-Roman",
            fontSize=10 - shrink,
            leading=11.5 - shrink,
            alignment=TA_LEFT,
            textColor=black,
            spaceAfter=1 if not compact else 0,
        ),
        "skill": ParagraphStyle(
            "ResumeSkill",
            fontName="Times-Roman",
            fontSize=10 - shrink,
            leading=11.5 - shrink,
            alignment=TA_LEFT,
            textColor=black,
            spaceAfter=0,
        ),
        "job": ParagraphStyle(
            "ResumeJob",
            fontName="Times-Bold",
            fontSize=10.5 - shrink,
            leading=12 - shrink,
            alignment=TA_LEFT,
            textColor=black,
        ),
        "dates": ParagraphStyle(
            "ResumeDates",
            fontName="Times-Bold",
            fontSize=10.5 - shrink,
            leading=12 - shrink,
            alignment=TA_RIGHT,
            textColor=black,
        ),
        "url": ParagraphStyle(
            "ResumeUrl",
            fontName="Times-Italic",
            fontSize=9 - shrink * 0.4,
            leading=11 - shrink,
            alignment=TA_RIGHT,
            textColor=black,
        ),
        "sub": ParagraphStyle(
            "ResumeSub",
            fontName="Times-Italic",
            fontSize=9.5 - shrink,
            leading=11.5 - shrink,
            alignment=TA_LEFT,
            textColor=black,
            spaceAfter=0,
        ),
        "bullet": ParagraphStyle(
            "ResumeBullet",
            fontName="Times-Roman",
            fontSize=10 - shrink,
            leading=11.5 - shrink,
            leftIndent=16,
            bulletIndent=6,
            bulletFontName="Times-Roman",
            bulletFontSize=10 - shrink,
            alignment=TA_LEFT,
            textColor=black,
            spaceAfter=0,
        ),
    }


def _xml(text: str) -> str:
    return escape(text or "")


def _hyperlink(label: str, url: str) -> str:
    href = escape(url, {'"': "&quot;"})
    return f'<link href="{href}" color="#0563C1"><u>{_xml(label)}</u></link>'


def _contact_xml(contact: str, links: dict[str, str]) -> str:
    chunks = []
    for part in [item.strip() for item in (contact or "").split("|")]:
        url = _href_for(part, links)
        chunks.append(_hyperlink(part, url) if url else _xml(part))
    return " &nbsp;|&nbsp; ".join(chunks)


def _linkify(text: str, links: dict[str, str] | None = None) -> str:
    raw = text or ""
    parts: list[str] = []
    last = 0
    for match in _VISIBLE_URL.finditer(raw):
        parts.append(_xml(raw[last : match.start()]))
        visible = match.group(0).rstrip(".,);")
        href = _href_for(visible, links)
        parts.append(_hyperlink(visible, href) if href else _xml(visible))
        last = match.end()
    parts.append(_xml(raw[last:]))
    return "".join(parts)


def _split_dates(header: str) -> tuple[str, str]:
    match = _DATE_TAIL.match((header or "").strip())
    if not match:
        return (header or "").strip(), ""
    return match.group(1).strip(), match.group(2).strip()


def _project_parts(header: str) -> tuple[str, str]:
    text = (header or "").strip()
    if "|" in text:
        left, right = [part.strip() for part in text.rsplit("|", 1)]
        return left, right
    return text, ""


def _right_column_width(data: dict, content_width: float) -> float:
    samples: list[tuple[str, float, str]] = []
    for job in data.get("experience") or []:
        _, dates = _split_dates(str(job.get("header") or ""))
        if dates:
            samples.append(("Times-Bold", 10.5, dates))
    for project in data.get("projects") or []:
        _, right = _project_parts(str(project.get("header") or ""))
        if right:
            samples.append(("Times-Italic", 9, right))
    measured = [stringWidth(text, font, size) for font, size, text in samples]
    needed = (max(measured) + 8) if measured else 1.7 * inch
    return min(needed, 3.2 * inch, content_width * 0.55)


def _header_table(
    left_xml: str,
    right_xml: str,
    left_style: ParagraphStyle,
    right_style: ParagraphStyle,
    width: float,
    right_width: float,
):
    left = Paragraph(left_xml, left_style)
    if not right_xml:
        return left
    table = Table(
        [[left, Paragraph(right_xml, right_style)]],
        colWidths=[width - right_width, right_width],
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


class _HeadingRule(Flowable):
    def __init__(self, width: float, stroke: float = 0.75):
        super().__init__()
        self.width = width
        self.height = 3
        self._stroke = stroke

    def draw(self):
        self.canv.setStrokeColor(black)
        self.canv.setLineWidth(self._stroke)
        self.canv.line(0, 1, self.width, 1)


_SIDE_MARGIN = 43
_VERTICAL_INSET = 18
_FILL_SLACK_MIN = 6
_FILL_UNUSED = 4
_SECTION_EXTRA = 14
_SECTION_EXTRA_MAX = 22
_BLOCK_EXTRA = 8
_LINE_EXTRA = 1.5


def _mark(flowable, role: str):
    flowable._fill_role = role
    return flowable


def _occupied_height(story, width: float) -> float:
    """Height the frame will consume, matching reportlab's overlapping spacers."""
    total = 0.0
    prev_after = 0.0
    at_top = True
    for flowable in story:
        _, height = flowable.wrap(width, 100000)
        gap = 0.0 if at_top else max(flowable.getSpaceBefore(), prev_after)
        total += gap + height
        prev_after = flowable.getSpaceAfter()
        at_top = False
    return total + prev_after


def _role_indexes(story, role: str) -> list[int]:
    return [
        index
        for index, flowable in enumerate(story)
        if getattr(flowable, "_fill_role", None) == role
    ]


def _spread_slack(story, budget: float) -> None:
    """Push leftover page space into section, role, and line gaps."""
    if budget <= 0 or not story:
        return
    befores = [flowable.getSpaceBefore() for flowable in story]
    afters = [flowable.getSpaceAfter() for flowable in story]
    base_before = list(befores)
    base_after = list(afters)

    def add_before(role: str, cap: float, budget: float) -> float:
        targets = [index for index in _role_indexes(story, role) if index > 0]
        while budget > 0.05 and targets:
            share = budget / len(targets)
            spent = 0.0
            remaining = []
            for index in targets:
                ceiling = base_before[index] + cap
                current_gap = max(befores[index], afters[index - 1])
                room = ceiling - current_gap
                if room <= 0.05:
                    continue
                add = min(share, room)
                befores[index] = current_gap + add
                spent += add
                if ceiling - befores[index] > 0.05:
                    remaining.append(index)
            if spent <= 0.05:
                break
            budget -= spent
            targets = remaining
        return budget

    def add_after(role: str, cap: float, budget: float) -> float:
        targets = _role_indexes(story, role)
        while budget > 0.05 and targets:
            share = budget / len(targets)
            spent = 0.0
            remaining = []
            for index in targets:
                ceiling = base_after[index] + cap
                if index + 1 < len(story):
                    current_gap = max(afters[index], befores[index + 1])
                    room = ceiling - current_gap
                    if room <= 0.05:
                        continue
                    add = min(share, room)
                    afters[index] = current_gap + add
                else:
                    room = ceiling - afters[index]
                    if room <= 0.05:
                        continue
                    add = min(share, room)
                    afters[index] += add
                spent += add
                if ceiling - afters[index] > 0.05:
                    remaining.append(index)
            if spent <= 0.05:
                break
            budget -= spent
            targets = remaining
        return budget

    def fill_remaining(role: str, budget: float) -> float:
        targets = [index for index in _role_indexes(story, role) if index > 0]
        if budget <= 0.05 or not targets:
            return budget
        share = budget / len(targets)
        for index in targets:
            current_gap = max(befores[index], afters[index - 1])
            befores[index] = current_gap + share
        return 0.0

    budget = add_before("section", _SECTION_EXTRA, budget)
    budget = add_before("block", _BLOCK_EXTRA, budget)
    budget = add_after("line", _LINE_EXTRA, budget)
    budget = add_before("section", _SECTION_EXTRA_MAX, budget)
    budget = fill_remaining("section", budget)
    fill_remaining("block", budget)

    for flowable, before, after in zip(story, befores, afters):
        flowable.spaceBefore = before
        flowable.spaceAfter = after


def write_resume_pdf(data: dict, path: Path, compact: bool = False) -> None:
    styles = _styles(compact=compact)
    margin = _SIDE_MARGIN
    width = letter[0] - (2 * margin)
    right_width = _right_column_width(data, width)
    links = data.get("links") or {}
    story = []

    name = str(data.get("name") or "").strip()
    contact = str(data.get("contact_line") or "").strip()
    if name:
        story.append(Paragraph(_xml(name), styles["name"]))
    if contact:
        story.append(Paragraph(_contact_xml(contact, links), styles["contact"]))

    def add_heading(title: str) -> None:
        story.append(_mark(Paragraph(_xml(title.upper()), styles["heading"]), "section"))
        story.append(_HeadingRule(width))

    summary = str(data.get("summary") or "").strip()
    if summary:
        add_heading("Summary")
        story.append(Paragraph(_xml(summary), styles["body"]))

    skills = _string_list(data.get("skills"))
    if skills:
        add_heading("Skills")
        for line in skills:
            story.append(_mark(Paragraph(_xml(line), styles["skill"]), "line"))

    add_heading("Experience")
    for job in data.get("experience") or []:
        left, dates = _split_dates(str(job.get("header") or "").strip())
        story.append(
            _mark(
                _header_table(_xml(left), _xml(dates), styles["job"], styles["dates"], width, right_width),
                "block",
            )
        )
        subheader = str(job.get("subheader") or "").strip()
        if subheader:
            story.append(Paragraph(_xml(subheader), styles["sub"]))
        for bullet in _string_list(job.get("bullets")):
            story.append(_mark(Paragraph(_xml(bullet), styles["bullet"], bulletText="•"), "line"))

    projects = data.get("projects") or []
    if projects:
        add_heading("Personal Project")
        for project in projects:
            left, right = _project_parts(str(project.get("header") or ""))
            if right:
                header = _header_table(
                    _xml(left),
                    _linkify(right, links),
                    styles["job"],
                    styles["url"],
                    width,
                    right_width,
                )
            else:
                header = Paragraph(_linkify(left, links), styles["job"])
            story.append(_mark(header, "block"))
            subheader = str(project.get("subheader") or "").strip()
            if subheader:
                story.append(Paragraph(_xml(subheader), styles["sub"]))
            for bullet in _string_list(project.get("bullets")):
                story.append(_mark(Paragraph(_xml(bullet), styles["bullet"], bulletText="•"), "line"))

    education = _string_list(data.get("education"))
    if education:
        add_heading("Education")
        for line in education:
            story.append(_mark(Paragraph(_xml(line), styles["skill"]), "line"))

    certifications = _string_list(data.get("certifications"))
    if certifications:
        add_heading("Certifications")
        story.append(_mark(Paragraph(_xml(" | ".join(certifications)), styles["skill"]), "line"))

    path.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = letter
    frame_height = page_h - (2 * _VERTICAL_INSET)
    slack = frame_height - _occupied_height(story, width)
    if slack >= _FILL_SLACK_MIN:
        _spread_slack(story, slack - _FILL_UNUSED)
    frame = Frame(
        margin,
        _VERTICAL_INSET,
        page_w - (2 * margin),
        frame_height,
        id="resume",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        showBoundary=0,
    )
    doc = BaseDocTemplate(
        str(path),
        pagesize=letter,
        title=name or "Resume",
        author=name,
    )
    doc.addPageTemplates([PageTemplate(id="onepage", frames=[frame])])
    doc.build(story)


def _pdf_page_count(path: Path) -> int:
    try:
        from pypdf import PdfReader
    except ImportError:
        return 1
    try:
        return len(PdfReader(str(path)).pages)
    except Exception:
        return 1


def _shorten_jobs(jobs: list[dict], limit: int) -> list[dict]:
    shortened = []
    for job in jobs or []:
        shortened.append(
            {
                "header": job.get("header") or "",
                "subheader": job.get("subheader") or "",
                "bullets": [shorten_text(str(item), limit) for item in (job.get("bullets") or [])],
            }
        )
    return shortened


def _fit_resume_to_one_page(data: dict, path: Path) -> dict:
    import copy

    fitted = data
    write_resume_pdf(fitted, path)
    if _pdf_page_count(path) <= 1:
        return fitted

    fitted = copy.deepcopy(data)
    fitted["summary"] = shorten_text(str(fitted.get("summary") or ""), 380)
    fitted["skills"] = [
        shorten_text(str(line), 160) for line in (fitted.get("skills") or [])
    ]
    write_resume_pdf(fitted, path)
    if _pdf_page_count(path) <= 1:
        return fitted

    fitted["experience"] = _shorten_jobs(fitted.get("experience") or [], 150)
    fitted["projects"] = _shorten_jobs(fitted.get("projects") or [], 140)
    write_resume_pdf(fitted, path)
    if _pdf_page_count(path) <= 1:
        return fitted

    write_resume_pdf(fitted, path, compact=True)
    return fitted


def _resume_stem(job: dict, date_str: str, unique_id: str | None = None) -> str:
    company = _safe_filename(job.get("company", "Unknown"))
    title = _safe_filename(job.get("title", "Unknown"))
    if unique_id:
        return f"{company}_{title}_{date_str}_{unique_id}"
    return f"{company}_{title}_{date_str}"


def _master_mtime() -> float:
    times = []
    for path in (MASTER_RESUME_PATH, MASTER_RESUME_PDF):
        try:
            times.append(path.stat().st_mtime)
        except OSError:
            continue
    return max(times) if times else 0


def resume_meta_path(pdf_path: Path | str) -> Path:
    path = Path(pdf_path)
    return path.with_name(f"{path.stem}_meta.json")


def read_resume_meta(pdf_path: Path | str) -> dict:
    meta_path = resume_meta_path(pdf_path)
    if not meta_path.exists():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _cached_ats_score(meta: dict) -> int | None:
    value = meta.get("ats_score")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cached_resume_pdf(job: dict, date_str: str) -> Path | None:
    pdf_path = RESUMES_OUTPUT_DIR / f"{_resume_stem(job, date_str)}.pdf"
    if not pdf_path.exists():
        return None
    if pdf_path.stat().st_mtime <= _master_mtime():
        return None
    meta = read_resume_meta(pdf_path)
    ats_score = _cached_ats_score(meta)
    if meta.get("ats_version") != ATS_META_VERSION:
        return None
    if ats_score is None or ats_score < RESUME_MIN_ATS_SCORE:
        return None
    return pdf_path


def _write_resume_files(
    data: dict,
    job: dict,
    score_result: dict,
    date_str: str,
    unique_id: str | None = None,
    ats_info: dict | None = None,
) -> Path:
    RESUMES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = _resume_stem(job, date_str, unique_id)
    pdf_path = RESUMES_OUTPUT_DIR / f"{stem}.pdf"
    meta_path = RESUMES_OUTPUT_DIR / f"{stem}_meta.json"
    ats_info = ats_info or {}
    data = _fit_resume_to_one_page(data, pdf_path)
    meta_path.write_text(
        json.dumps(
            {
                "job_url": job.get("job_url", ""),
                "score": score_result.get("score"),
                "company": job.get("company", ""),
                "title": job.get("title", ""),
                "date": date_str,
                "pdf_path": str(pdf_path),
                "ats_score": ats_info.get("score"),
                "ats_version": ATS_META_VERSION,
                "ats_matched_skills": ats_info.get("matched_skills") or [],
                "ats_added_skills": ats_info.get("added_skills") or [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return pdf_path


def _persist_resume(job: dict, pdf_path: Path, ats_score: int | None = None) -> None:
    if not job.get("job_id"):
        return
    from jobs.job_store import save_job_resume

    save_job_resume(job["job_id"], str(pdf_path), ats_score=ats_score)


def build_resume(
    job: dict,
    score_result: dict,
    *,
    persist: bool = True,
    use_cache: bool = True,
) -> str:
    date_str = datetime.now().strftime("%Y-%m-%d")
    unique_id = None if use_cache else uuid.uuid4().hex[:8]
    if use_cache:
        cached = _cached_resume_pdf(job, date_str)
        if cached is not None:
            if persist:
                _persist_resume(job, cached, _cached_ats_score(read_resume_meta(cached)))
            return str(cached)

    template = parse_master_resume()
    raw_description = score_result.get("job_description") or fetch_job_description(job)
    job_description = jd_for_resume(raw_description)
    job_title = job.get("title", "Unknown")
    company_name = job.get("company", "Unknown")
    min_years = jd_min_years(job_description, job_title) or EXPERIENCE_YEARS
    prepared = prepare_skills(
        template["skill_groups"],
        job_description,
        job_title,
        company_name,
    )
    template["skill_groups"] = prepared["groups"]
    template["skills"] = prepared["skill_lines"]

    has_summary = bool(str(template.get("summary") or "").strip())
    prompt_values = {
        "skills": "\n".join(prepared["skill_lines"]),
        "bullets": _bullet_prompt_block(template["experience"]),
        "job_title": job_title,
        "company_name": company_name,
        "job_description": job_description,
    }
    if has_summary:
        prompt = RESUME_PROMPT.format(
            summary=template["summary"],
            min_years=min_years,
            **prompt_values,
        )
        system = RESUME_SYSTEM
    else:
        prompt = RESUME_PROMPT_NO_SUMMARY.format(**prompt_values)
        system = RESUME_SYSTEM_NO_SUMMARY
    response_text = generate(
        system,
        prompt,
        max_tokens=1500,
        json_mode=True,
        purpose="resume",
    )
    patch = extract_json(response_text)
    data = _merge_patch(template, patch, job_title, company_name)
    if has_summary:
        data["summary"] = align_summary_years(
            data.get("summary") or "",
            job_description,
            job_title,
        )
    else:
        data["summary"] = ""
    data, ats_result, added_skills = boost_resume_skills(
        data,
        prepared["groups"],
        prepared["jd_skills"],
        prepared["extra_keywords"],
        job_title,
        prepared["added_skills"],
        RESUME_MIN_ATS_SCORE,
        company_name,
    )
    ats_info = {
        **ats_result,
        "added_skills": added_skills,
    }
    pdf_path = _write_resume_files(
        data,
        job,
        score_result,
        date_str,
        unique_id,
        ats_info=ats_info,
    )

    if persist:
        _persist_resume(job, pdf_path, ats_result.get("score"))

    return str(pdf_path)
