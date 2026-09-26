import copy
import logging
import re

from ai.llm import extract_json, generate
from config import EXPERIENCE_YEARS, RESUME_TARGET_ATS_SCORE

logger = logging.getLogger(__name__)

_MAX_JD_SKILLS = 18
_MAX_EXTRA_KEYWORDS = 8
_MAX_LINE_CHARS = 180
_MAX_SKILL_WORDS = 3
_MAX_SKILL_CHARS = 32

EXTRACT_SYSTEM = (
    "Extract concrete tools, languages, frameworks, cloud products, and protocols. "
    "Return JSON only."
)

EXTRACT_PROMPT = """Extract up to {limit} hard skills from this job.

STRICT RULES:
- Each item is a real tool or language, 1-3 words (Kubernetes, Terraform, AWS KMS, Docker).
- Never copy section headings, job titles, seniority (SMTS, Staff, L5), sentences, or quoted text.
- Never use the company name or role name as a skill.
- Prefer concrete tools over generics: IaC → Terraform; multi-cloud → AWS; cryptography/PKI/secrets → HashiCorp Vault, AWS KMS, mTLS.
- keywords: short domain phrases only (microservices, distributed systems), max 3 words.

Return ONLY:
{{"skills":["Kubernetes","Terraform"],"keywords":["microservices"]}}

JOB TITLE: {job_title}
COMPANY: {company}

JOB:
{job_description}
"""

_STOP = {
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
    "this",
    "that",
    "will",
    "have",
    "has",
    "using",
    "used",
    "use",
    "including",
    "include",
    "such",
    "other",
    "related",
    "strong",
    "excellent",
    "required",
    "preferred",
    "experience",
    "years",
    "year",
    "ability",
    "knowledge",
    "understanding",
    "working",
    "work",
    "role",
    "job",
    "position",
    "team",
    "plus",
    "etc",
    "primary",
    "responsibilities",
    "responsibility",
    "qualifications",
    "requirements",
    "services",
    "scalable",
    "writing",
    "principles",
}

_SOFT = {
    "communication",
    "communications",
    "teamwork",
    "collaboration",
    "collaborative",
    "leadership",
    "problem-solving",
    "problem solving",
    "interpersonal",
    "stakeholder",
    "stakeholders",
    "self-motivated",
    "self motivated",
    "proactive",
    "detail-oriented",
    "detail oriented",
    "time management",
    "adaptability",
    "ownership",
    "mentoring",
    "presentation",
    "written",
    "verbal",
    "passionate",
    "driven",
    "motivated",
    "organized",
    "organisation",
    "organization",
    "fast-paced",
    "fast paced",
    "cross-functional",
    "cross functional",
    "independent",
    "creative",
    "creativity",
    "critical thinking",
}

_HEADING_LIKE = {
    "responsibilities",
    "responsibility",
    "qualifications",
    "requirements",
    "requirement",
    "duties",
    "duty",
    "overview",
    "description",
    "benefits",
    "about",
    "primary",
    "preferred",
    "minimum",
    "must-have",
    "nice-to-have",
    "what",
    "youll",
    "you'll",
}

_SENIORITY = {
    "smts",
    "mts",
    "staff",
    "principal",
    "distinguished",
    "senior",
    "junior",
    "intern",
    "ic3",
    "ic4",
    "ic5",
    "ic6",
    "l3",
    "l4",
    "l5",
    "l6",
    "l7",
    "manager",
    "director",
    "head",
    "lead",
}

_DOMAIN_OK = {
    "microservices",
    "distributed systems",
    "ci cd",
    "machine learning",
    "pki",
    "x.509",
    "mtls",
    "oauth",
    "jwt",
}

_GENERIC_SKIP = {
    "iac",
    "infrastructure as code",
    "multi cloud",
    "multicloud",
    "multi-cloud",
    "cryptography",
    "crypto",
    "cryptographic primitives",
    "zero trust",
    "zero-trust",
    "zero trust identity concepts",
    "secret access patterns",
    "secure secrets management",
    "software engineering",
    "computer science",
    "engineering",
    "best practices",
    "tier 0",
    "tier0",
}

_VERB_FRAGMENTS = re.compile(
    r"\b(writing|applying|including|building|focus(?:es|ing)?|"
    r"specializ(?:e|es|ing)|developing|creating|managing|owning|"
    r"working|using)\b",
    re.I,
)

_ALIASES = {
    "k8s": "kubernetes",
    "k8": "kubernetes",
    "js": "javascript",
    "ts": "typescript",
    "nodejs": "node.js",
    "node": "node.js",
    "node js": "node.js",
    "golang": "go",
    "postgres": "postgresql",
    "postgress": "postgresql",
    "mongo": "mongodb",
    "gcp": "google cloud",
    "google cloud platform": "google cloud",
    "amazon web services": "aws",
    "reactjs": "react",
    "react.js": "react",
    "nextjs": "next.js",
    "vuejs": "vue",
    "vue.js": "vue",
    "ci/cd": "ci cd",
    "cicd": "ci cd",
    "ci-cd": "ci cd",
    "ml": "machine learning",
    "rest": "rest",
    "restful": "rest",
    "rest apis": "rest",
    "rest api": "rest",
    "dotnet": ".net",
    "c sharp": "c#",
    "csharp": "c#",
    "c plus plus": "c++",
    "cpp": "c++",
    "github actions": "github actions",
    "gh actions": "github actions",
    "tf": "terraform",
    "py": "python",
    "mssql": "sql server",
    "ms sql": "sql server",
    "amazon s3": "s3",
    "elastic search": "elasticsearch",
    "hashicorp vault": "vault",
    "vault": "vault",
    "aws kms": "kms",
    "kms": "kms",
    "mtls": "mtls",
    "m tls": "mtls",
    "x.509": "x.509",
    "x509": "x.509",
}

_CATEGORIES = {
    "Languages": {
        "python",
        "java",
        "javascript",
        "typescript",
        "go",
        "rust",
        "kotlin",
        "swift",
        "ruby",
        "php",
        "scala",
        "c++",
        "c#",
        "sql",
        "html",
        "css",
        "r",
        "bash",
        "shell",
        "perl",
        "objective-c",
    },
    "Frameworks": {
        "react",
        "angular",
        "vue",
        "next.js",
        "django",
        "flask",
        "fastapi",
        "spring",
        "rails",
        "express",
        "node.js",
        ".net",
        "laravel",
        "svelte",
        "nestjs",
        "bootstrap",
    },
    "Cloud": {
        "aws",
        "azure",
        "gcp",
        "google cloud",
        "docker",
        "kubernetes",
        "terraform",
        "ansible",
        "helm",
        "lambda",
        "ec2",
        "s3",
        "eks",
        "ecs",
        "cloudformation",
        "pulumi",
        "vault",
        "kms",
        "mtls",
    },
    "Databases": {
        "postgresql",
        "mysql",
        "mongodb",
        "redis",
        "elasticsearch",
        "dynamodb",
        "cassandra",
        "oracle",
        "sql server",
        "snowflake",
        "bigquery",
        "redshift",
        "sqlite",
    },
}

_KNOWN_SKILLS = [
    "Python",
    "Java",
    "JavaScript",
    "TypeScript",
    "Go",
    "Rust",
    "Kotlin",
    "Swift",
    "C++",
    "C#",
    "Ruby",
    "PHP",
    "Scala",
    "SQL",
    "HTML",
    "CSS",
    "React",
    "Angular",
    "Vue",
    "Next.js",
    "Node.js",
    "Django",
    "Flask",
    "FastAPI",
    "Spring",
    "Spring Boot",
    "Rails",
    "Express",
    ".NET",
    "AWS",
    "Azure",
    "GCP",
    "Google Cloud",
    "Docker",
    "Kubernetes",
    "Terraform",
    "Ansible",
    "Jenkins",
    "Git",
    "GitHub",
    "GitLab",
    "CI/CD",
    "PostgreSQL",
    "MySQL",
    "MongoDB",
    "Redis",
    "Elasticsearch",
    "Kafka",
    "GraphQL",
    "REST",
    "gRPC",
    "Linux",
    "Spark",
    "Hadoop",
    "Pandas",
    "NumPy",
    "PyTorch",
    "TensorFlow",
    "Airflow",
    "Snowflake",
    "Databricks",
    "BigQuery",
    "Redshift",
    "S3",
    "Lambda",
    "EC2",
    "ECS",
    "EKS",
    "CloudFormation",
    "Prometheus",
    "Grafana",
    "Datadog",
    "Helm",
    "Nginx",
    "RabbitMQ",
    "Celery",
    "React Native",
    "Android",
    "iOS",
    "Bash",
    "JUnit",
    "PyTest",
    "Playwright",
    "Cypress",
    "Redux",
    "ArgoCD",
    "GitHub Actions",
    "CircleCI",
    "Okta",
    "OAuth",
    "SAML",
    "JWT",
    "Cassandra",
    "DynamoDB",
    "SQL Server",
    "HashiCorp Vault",
    "AWS KMS",
    "mTLS",
    "PKI",
    "X.509",
    "Vault",
    "KMS",
]

_CONCRETE_HINTS = [
    (
        re.compile(r"\b(iac|infrastructure as code|terraform)\b", re.I),
        ["Terraform"],
    ),
    (
        re.compile(r"\b(kubernetes|k8s|orchestration)\b", re.I),
        ["Kubernetes"],
    ),
    (
        re.compile(r"\b(docker|containers?)\b", re.I),
        ["Docker"],
    ),
    (
        re.compile(r"\b(multi[- ]?cloud|amazon web services|\baws\b)\b", re.I),
        ["AWS"],
    ),
    (re.compile(r"\b(gcp|google cloud)\b", re.I), ["GCP"]),
    (re.compile(r"\bazure\b", re.I), ["Azure"]),
    (
        re.compile(
            r"\b(vault|secrets?|kms|pki|x\.?509|cryptograph|m\s*tls|mtls|zero[- ]trust)\b",
            re.I,
        ),
        ["HashiCorp Vault", "AWS KMS", "mTLS", "PKI", "X.509"],
    ),
]

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.+#]*", re.I)
_PREFIX_RE = re.compile(
    r"^(?:experience (?:with|in)|proficiency (?:with|in)|knowledge of|"
    r"familiarity with|hands[- ]on (?:with|in)|working (?:with|in)|using)\s+",
    re.I,
)
_YEARS_RE = re.compile(r"\s*\(\d+\+?\s*years?\)\s*$", re.I)
_YEARS_IN_PROSE = re.compile(r"(\d+)\s*\+?\s*(?:years?|yrs)\b", re.I)
_KNOWN_CANON = {_ALIASES.get(item.lower(), item.lower()) for item in _KNOWN_SKILLS}
for _members in _CATEGORIES.values():
    _KNOWN_CANON.update(_members)


def _canonical(text: str) -> str:
    value = re.sub(r"[\s_/,-]+", " ", (text or "").lower()).strip()
    value = value.replace("(", " ").replace(")", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return _ALIASES.get(value, value)


def _tokens(text: str) -> set[str]:
    words = _TOKEN_RE.findall(_canonical(text))
    tokens = set()
    for word in words:
        folded = _ALIASES.get(word, word)
        if folded not in _STOP and len(folded) > 1:
            tokens.add(folded)
    return tokens


def _clean_skill(text: str) -> str:
    value = (text or "").strip().strip("-•*\"'")
    value = _PREFIX_RE.sub("", value)
    value = _YEARS_RE.sub("", value)
    value = value.strip(" .;:")
    if len(value) > _MAX_SKILL_CHARS:
        value = value[:_MAX_SKILL_CHARS].rsplit(" ", 1)[0].strip()
    return value


def _is_soft(text: str) -> bool:
    return _canonical(text) in _SOFT or (text or "").strip().lower() in _SOFT


def _word_count(text: str) -> int:
    return len([part for part in re.split(r"\s+", text.strip()) if part])


def _company_exclusions(company: str) -> set[str]:
    raw = re.sub(r"[\s,/]+", " ", (company or "").strip().lower())
    if not raw:
        return set()
    names = {raw}
    first = raw.split()[0]
    if first not in {"amazon", "google", "microsoft", "meta", "oracle", "the"}:
        names.add(first)
    return names


def _is_plausible_skill(text: str, job_title: str = "", company: str = "") -> bool:
    cleaned = _clean_skill(text)
    if not cleaned or _is_soft(cleaned) or cleaned.lower() in _STOP:
        return False
    if _word_count(cleaned) > _MAX_SKILL_WORDS or len(cleaned) > _MAX_SKILL_CHARS:
        return False
    if any(mark in cleaned for mark in ('"', "“", "”", "'")):
        return False
    if _VERB_FRAGMENTS.search(cleaned):
        return False
    canon = _canonical(cleaned)
    if not canon or canon in _GENERIC_SKIP or canon in _HEADING_LIKE:
        return False
    tokens = _tokens(cleaned)
    if tokens & _HEADING_LIKE or tokens & _SENIORITY:
        return False
    company_names = _company_exclusions(company)
    if cleaned.lower() in company_names or canon in company_names:
        return False
    title_tokens = _tokens(job_title)
    if tokens and title_tokens and tokens <= title_tokens and not (tokens & _KNOWN_CANON):
        return False
    if canon in _KNOWN_CANON or canon in _DOMAIN_OK:
        return True
    if any(ch in cleaned for ch in "+#"):
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9.+#-]{1,20}", cleaned) and cleaned[:1].isupper():
        return True
    return False


def _unique_terms(
    items: list[str],
    limit: int,
    job_title: str = "",
    company: str = "",
) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        text = _clean_skill(str(raw))
        if not _is_plausible_skill(text, job_title, company):
            continue
        key = _canonical(text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _skill_in_text(skill: str, haystack: str, haystack_tokens: set[str] | None = None) -> bool:
    canon = _canonical(skill)
    if not canon:
        return False
    blob = haystack if haystack[:1] == " " else f" {haystack} "
    if f" {canon} " in blob:
        return True
    tokens = _tokens(skill)
    if not tokens:
        return False
    pool = haystack_tokens if haystack_tokens is not None else _tokens(haystack)
    return tokens <= pool


def _normalize_haystack(text: str) -> tuple[str, set[str]]:
    canon = _canonical(text)
    return f" {canon} ", _tokens(canon)


def coverage(terms: list[str], resume_text: str) -> tuple[float, list[str], list[str]]:
    if not terms:
        return 1.0, [], []
    haystack, hay_tokens = _normalize_haystack(resume_text)
    matched: list[str] = []
    missing: list[str] = []
    for term in terms:
        if _skill_in_text(term, haystack, hay_tokens):
            matched.append(term)
        else:
            missing.append(term)
    return len(matched) / len(terms), matched, missing


def _title_terms(job_title: str) -> list[str]:
    terms = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9.+#]{1,}", job_title or ""):
        low = word.lower()
        if low in _STOP or low in _SENIORITY or _is_soft(word):
            continue
        if low in {"software", "engineering", "engineer"}:
            continue
        terms.append(word)
    return _unique_terms(terms, 6)


def assemble_resume_text(data: dict) -> str:
    parts: list[str] = [str(data.get("summary") or "")]
    parts.extend(str(line) for line in (data.get("skills") or []) if line)
    for block in list(data.get("experience") or []) + list(data.get("projects") or []):
        parts.append(str(block.get("header") or ""))
        parts.append(str(block.get("subheader") or ""))
        parts.extend(str(item) for item in (block.get("bullets") or []) if item)
    parts.extend(str(item) for item in (data.get("education") or []) if item)
    parts.extend(str(item) for item in (data.get("certifications") or []) if item)
    return "\n".join(part for part in parts if str(part).strip())


def score_resume(
    resume_text: str,
    jd_skills: list[str],
    extra_keywords: list[str],
    job_title: str = "",
) -> dict:
    skill_cov, matched_skills, missing_skills = coverage(jd_skills, resume_text)
    extra_cov, matched_keywords, missing_keywords = coverage(extra_keywords, resume_text)
    title_cov, _, _ = coverage(_title_terms(job_title), resume_text)
    score = int(
        round(100 * (0.75 * skill_cov + 0.15 * extra_cov + 0.10 * title_cov))
    )
    return {
        "score": max(0, min(100, score)),
        "skill_coverage": skill_cov,
        "keyword_coverage": extra_cov,
        "title_coverage": title_cov,
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "matched_keywords": matched_keywords,
        "missing_keywords": missing_keywords,
    }


def score_resume_data(
    data: dict,
    jd_skills: list[str],
    extra_keywords: list[str],
    job_title: str = "",
) -> dict:
    return score_resume(assemble_resume_text(data), jd_skills, extra_keywords, job_title)


def _scan_known(text: str, job_title: str = "", company: str = "") -> list[str]:
    blob = f" {_canonical(text)} "
    found: list[str] = []
    for skill in _KNOWN_SKILLS:
        if not _is_plausible_skill(skill, job_title, company):
            continue
        canon = _canonical(skill)
        pattern = rf"(?<![a-z0-9]){re.escape(canon)}(?![a-z0-9])"
        if re.search(pattern, blob):
            found.append(skill)
    return found


def _expand_concrete(text: str) -> list[str]:
    found: list[str] = []
    for pattern, tools in _CONCRETE_HINTS:
        if pattern.search(text or ""):
            found.extend(tools)
    return found


def _llm_extract(job_description: str, job_title: str, company: str) -> tuple[list[str], list[str]]:
    if not (job_description or "").strip():
        return [], []
    prompt = EXTRACT_PROMPT.format(
        limit=_MAX_JD_SKILLS,
        job_title=job_title or "Unknown",
        company=company or "Unknown",
        job_description=job_description.strip()[:2500],
    )
    try:
        response = generate(
            EXTRACT_SYSTEM,
            prompt,
            max_tokens=400,
            json_mode=True,
            purpose="score",
        )
        data = extract_json(response)
    except Exception as exc:
        logger.warning("JD skill extraction failed; using heuristic: %s", exc)
        return [], []
    skills = data.get("skills") if isinstance(data, dict) else None
    keywords = data.get("keywords") if isinstance(data, dict) else None
    if not isinstance(skills, list):
        skills = []
    if not isinstance(keywords, list):
        keywords = []
    return [str(item) for item in skills], [str(item) for item in keywords]


def extract_jd_terms(
    job_description: str,
    job_title: str = "",
    company: str = "",
) -> tuple[list[str], list[str]]:
    llm_skills, llm_keywords = _llm_extract(job_description, job_title, company)
    source = f"{job_title}\n{job_description}"
    heuristic = _scan_known(source, job_title, company) + _expand_concrete(source)
    skills = _unique_terms(
        llm_skills + heuristic,
        _MAX_JD_SKILLS,
        job_title,
        company,
    )
    skill_keys = {_canonical(item) for item in skills}
    extra = _unique_terms(
        [item for item in llm_keywords if _canonical(item) not in skill_keys],
        _MAX_EXTRA_KEYWORDS,
        job_title,
        company,
    )
    return skills, extra


def _item_matches_jd(item: str, jd_keys: set[str], jd_tokens: set[str]) -> bool:
    key = _canonical(item)
    if key in jd_keys:
        return True
    tokens = _tokens(item)
    return bool(tokens and tokens & jd_tokens)


def missing_from_groups(groups: list[dict], terms: list[str]) -> list[str]:
    existing = []
    for group in groups:
        existing.extend(group.get("items") or [])
    blob, tokens = _normalize_haystack(" ".join(str(item) for item in existing))
    return [term for term in terms if not _skill_in_text(term, blob, tokens)]


def _best_group(groups: list[dict], skill: str) -> dict:
    tokens = _tokens(skill)
    canon = _canonical(skill)
    best = None
    best_score = 0
    for group in groups:
        name = (group.get("name") or "").strip()
        score = 0
        name_low = name.lower()
        for category, members in _CATEGORIES.items():
            if category.lower() in name_low and (canon in members or tokens & members):
                score += 6
        if _tokens(name) & tokens:
            score += 3
        for item in group.get("items") or []:
            if _tokens(item) & tokens:
                score += 1
                break
        if score > best_score:
            best_score = score
            best = group
    if best is not None:
        return best
    for group in groups:
        name = (group.get("name") or "").strip().lower()
        if name in {"tools", "core", "technical", "other", "skills"}:
            return group
    if groups:
        last = groups[-1]
        if not (last.get("name") or "").strip():
            return last
    tools = {"name": "Tools", "items": []}
    groups.append(tools)
    return tools


def inject_skills(
    groups: list[dict],
    to_add: list[str],
    job_title: str = "",
    company: str = "",
) -> tuple[list[dict], list[str]]:
    added: list[str] = []
    for skill in to_add:
        text = _clean_skill(skill)
        if not _is_plausible_skill(text, job_title, company):
            continue
        if not missing_from_groups(groups, [text]):
            continue
        group = _best_group(groups, text)
        items = list(group.get("items") or [])
        items.append(text)
        group["items"] = items
        added.append(text)
    return groups, added


def format_skill_lines(groups: list[dict], jd_skills: list[str]) -> list[str]:
    jd_keys = {_canonical(item) for item in jd_skills}
    jd_tokens: set[str] = set()
    for item in jd_skills:
        jd_tokens |= _tokens(item)

    ranked = []
    for index, group in enumerate(groups):
        matched = []
        rest = []
        for item in group.get("items") or []:
            text = str(item).strip()
            if not text:
                continue
            if _item_matches_jd(text, jd_keys, jd_tokens):
                matched.append(text)
            else:
                rest.append(text)
        score = len(matched)
        if _item_matches_jd(group.get("name") or "", jd_keys, jd_tokens):
            score += 1
        ranked.append((score, index, group, matched, rest))
    ranked.sort(key=lambda row: (-row[0], row[1]))

    lines = []
    for _, _, group, matched, rest in ranked:
        name = (group.get("name") or "").strip()
        prefix = f"{name}: " if name else ""
        items = list(matched)
        used = len(prefix) + len(", ".join(items))
        for item in rest:
            extra = (2 if items else 0) + len(item)
            if used + extra > _MAX_LINE_CHARS:
                continue
            items.append(item)
            used += extra
        if not items:
            continue
        lines.append(f"{prefix}{', '.join(items)}")
    return lines


def prepare_skills(
    groups: list[dict],
    job_description: str,
    job_title: str = "",
    company: str = "",
) -> dict:
    jd_skills, extra_keywords = extract_jd_terms(job_description, job_title, company)
    prepared = copy.deepcopy(groups)
    added: list[str] = []

    prepared, added_skills = inject_skills(
        prepared,
        missing_from_groups(prepared, jd_skills),
        job_title,
        company,
    )
    added.extend(added_skills)

    lines = format_skill_lines(prepared, jd_skills)
    preview = "\n".join(lines)
    preview_score = score_resume(preview, jd_skills, extra_keywords, job_title)
    if preview_score["score"] < RESUME_TARGET_ATS_SCORE:
        still = missing_from_groups(
            prepared, extra_keywords + preview_score["missing_skills"]
        )
        prepared, added_extra = inject_skills(prepared, still, job_title, company)
        added.extend(added_extra)
        lines = format_skill_lines(prepared, jd_skills + extra_keywords)

    return {
        "groups": prepared,
        "skill_lines": lines,
        "jd_skills": jd_skills,
        "extra_keywords": extra_keywords,
        "added_skills": added,
    }


def boost_resume_skills(
    data: dict,
    groups: list[dict],
    jd_skills: list[str],
    extra_keywords: list[str],
    job_title: str,
    already_added: list[str],
    min_score: int,
    company: str = "",
) -> tuple[dict, dict, list[str]]:
    result = score_resume_data(data, jd_skills, extra_keywords, job_title)
    if result["score"] >= min_score:
        return data, result, already_added

    to_add = [
        item
        for item in result["missing_skills"] + result["missing_keywords"]
        if _is_plausible_skill(item, job_title, company)
    ]
    groups, added = inject_skills(groups, to_add, job_title, company)
    already_added = list(already_added) + added
    data["skills"] = format_skill_lines(groups, jd_skills + extra_keywords)
    result = score_resume_data(data, jd_skills, extra_keywords, job_title)
    return data, result, already_added


def jd_min_years(job_description: str, job_title: str = "") -> int | None:
    from jobs.experience_filter import _extract_year_requirements

    combined = f"{job_title} {job_description}".strip()
    min_years, _ = _extract_year_requirements(combined)
    return min_years


def align_summary_years(summary: str, job_description: str, job_title: str = "") -> str:
    required = jd_min_years(job_description, job_title)
    if required is None:
        return summary
    target = max(required, EXPERIENCE_YEARS)

    def _replace(match: re.Match) -> str:
        stated = int(match.group(1))
        if stated >= target:
            return match.group(0)
        return f"{target}+ years"

    updated, count = _YEARS_IN_PROSE.subn(_replace, summary or "", count=1)
    return updated if count else summary


def scrub_jd_prose(text: str, job_title: str = "", company: str = "") -> str:
    value = text or ""
    value = re.sub(
        r"(?i)\bfocuses on primary responsibilities(?:\s+including)?\b[:,]?\s*",
        "",
        value,
    )
    value = re.sub(r"(?i)\bprimary responsibilities\b[:,]?\s*", "", value)
    value = re.sub(r"(?i)\bspecializing in [^.]{0,120}", "", value)
    value = re.sub(r"(?i)applying [^.]{0,50} best practices", "", value)
    if job_title:
        escaped = re.escape(job_title.strip())
        if escaped:
            value = re.sub(
                rf"(?i){escaped}(?:\s+(?:principles|best practices))?",
                "",
                value,
            )
        for token in re.findall(r"\b(?:SMTS|MTS|IC\d|L\d)\b", job_title):
            value = re.sub(
                rf"(?i)\b{re.escape(token)}\b(?:\s+(?:principles|best practices))?",
                "",
                value,
            )
    for name in _company_exclusions(company):
        if len(name) < 3:
            continue
        value = re.sub(rf"(?i)\b{re.escape(name)}\b", "", value)
    value = re.sub(r"(?i)\b(?:and|or)\s*([,.;])", r"\1", value)
    value = re.sub(r"(?:,\s*){2,}", ", ", value)
    value = re.sub(r"\s{2,}", " ", value)
    value = re.sub(r"\s+([,.;])", r"\1", value)
    value = re.sub(r",\s*\.", ".", value)
    value = re.sub(r"\.\s+[A-Za-z][A-Za-z-]{0,18}\.(?=\s+[A-Z]|$)", ".", value)
    return value.strip(" ,;:-")


def shorten_text(text: str, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    if ". " in cut:
        return cut.rsplit(". ", 1)[0].strip() + "."
    if " " in cut:
        return cut.rsplit(" ", 1)[0].strip()
    return cut
