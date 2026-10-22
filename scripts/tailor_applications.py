#!/usr/bin/env python3
"""Tailor application packages for newly discovered jobs using a local Ollama model.

For each unprocessed job in jobs/.seen_jobs.json this script creates
jobs/NN-company-role/ containing a fit score, the job description, a tailored
resume (LaTeX + PDF), a cover letter (LaTeX + PDF), and screening notes.
Processed jobs are recorded in jobs/.applications.json and summarised in
jobs/applications.md. If Ollama is unreachable the script exits cleanly so the
GitHub Actions tracker workflow is never broken.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = ROOT / "jobs"
STATE_PATH = JOBS_DIR / ".seen_jobs.json"
APPLICATIONS_PATH = JOBS_DIR / ".applications.json"
INDEX_PATH = JOBS_DIR / "applications.md"
RESUME_TEMPLATE = ROOT / "templates" / "resume-template.tex"
COVER_TEMPLATE = ROOT / "templates" / "cover-letter-template.tex"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("OLLAMA_MODEL", "phi3:mini")
MAX_JOBS_PER_RUN = int(os.environ.get("MAX_JOBS_PER_RUN", "3"))
MAX_DESC_CHARS = 3000
SCORE_THRESHOLD = int(os.environ.get("SCORE_THRESHOLD", "40"))
REQUEST_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "600"))

PROFILE = (
    "Madhav Jadav, Delhi, India. 2026 B.Tech-M.Tech Integrated CSE (Cyber Security) graduate, "
    "National Forensic Sciences University Delhi (CGPA 7.5/10). 18 months project-based backend "
    "engineering. Skills: Rust, Go, Python, Java, C++, C#, Node.js, SQL; REST APIs, microservices, "
    "gRPC, GraphQL; PostgreSQL, MongoDB, Redis, Kafka; Docker, Kubernetes, Helm, GitHub Actions CI/CD, "
    "AWS EC2. Key projects: RhinoChain custom blockchain in Rust (Actix-web, SHA-256, Proof-of-Work) "
    "for digital asset custody logging; authentication microservice 10K RPS JWT+MFA+RBAC on Kubernetes "
    "99.99% uptime; FastAPI+Redis classifier API <12ms latency at 5K QPS; Kafka streaming pipeline. "
    "10+ hackathon/CTF wins (First Prize SRM Verge 2025, Second Prize RedFox CyberStorm CTF 2025). "
    "Open to relocation; target roles: Rust/blockchain/backend software engineering and internships."
)

LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape_latex(text: str) -> str:
    return "".join(LATEX_SPECIALS.get(ch, ch) for ch in text)


def ollama_generate(prompt: str, *, system: str = "", num_predict: int = 900,
                    json_mode: bool = False, temperature: float = 0.3) -> str:
    payload: dict = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": 8192, "num_predict": num_predict, "temperature": temperature},
    }
    if system:
        payload["system"] = system
    if json_mode:
        payload["format"] = "json"
    last_error: Exception | None = None
    for _ in range(2):
        request = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.load(response).get("response", "").strip()
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError, OSError) as error:
            last_error = error
    raise last_error if last_error else RuntimeError("Ollama request failed")


def ollama_available() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as response:
            models = json.load(response).get("models", [])
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False
    return any(m.get("name", "").startswith(MODEL.split(":")[0]) for m in models)


def extract_json(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


KEYWORDS = [
    "rust", "go", "golang", "python", "java", "c++", "c#", "javascript", "typescript", "node.js",
    "solidity", "evm", "solana", "smart contract", "web3", "blockchain", "cryptocurrency",
    "rest api", "graphql", "grpc", "microservices", "distributed systems", "system design",
    "postgresql", "mysql", "mongodb", "redis", "kafka", "rabbitmq", "docker", "kubernetes",
    "terraform", "aws", "gcp", "azure", "ci/cd", "linux", "git", "react", "sql",
    "data structures", "algorithms", "low latency", "trading", "fintech", "cryptography",
    "internship", "junior", "graduate", "entry-level", "entry level",
]
SENIORITY_FLAGS = re.compile(r"\b(senior|staff|principal|lead|architect|manager|director|head of)\b", re.I)


def extract_keywords(job: dict[str, str]) -> tuple[list[str], list[str]]:
    """Deterministic keyword extraction: must-have from title, nice-to-have from description."""
    haystack = f"{job.get('title', '')} {job.get('description', '')}".lower()
    found = sorted({keyword for keyword in KEYWORDS if keyword in haystack})
    title_lower = job.get("title", "").lower()
    must = [keyword for keyword in found if keyword in title_lower]
    nice = [keyword for keyword in found if keyword not in must]
    return must, nice


def analyse_job(job: dict[str, str]) -> dict:
    system = (
        "You are a precise technical recruiter. Respond with ONLY one compact JSON object, no markdown: "
        '{"score": <int 0-100>, "verdict": "<apply|maybe|skip>", "reason": "<one sentence>", '
        '"headline": "<resume headline under 90 chars>", "summary_close": "<one sentence under 35 words>", '
        '"why_company": "<2 sentences>"}'
    )
    description = job.get("description", "")[:1500]
    prompt = (
        f"Candidate: {PROFILE}\n\n"
        f"Job: {job['title']} at {job['company']} ({job['location']}). Description: {description}\n\n"
        "Score fit on this scale: 70-100 strong match (junior/graduate/intern Rust, backend, blockchain "
        "roles the candidate could plausibly get); 40-69 partial match (seniority gap or partial overlap, "
        "still worth a tailored application); 0-39 poor match (wrong domain or staff/principal level). "
        "Fill every field with real content from the job description. JSON only."
    )
    analysis: dict | None = None
    for attempt, temperature in enumerate((0.2, 0.0, 0.0)):
        try:
            raw = ollama_generate(prompt, system=system, num_predict=400, json_mode=True, temperature=temperature)
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError, OSError) as error:
            print(f"  Ollama analysis attempt {attempt + 1} failed: {error}", file=sys.stderr)
            continue
        parsed = extract_json(raw)
        if parsed is None:
            continue
        try:
            parsed["score"] = max(0, min(100, int(parsed.get("score"))))
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed.get("verdict"), str) or parsed["verdict"].strip().lower() not in ("apply", "maybe", "skip"):
            continue
        if not all(isinstance(parsed.get(key), str) and len(parsed[key].strip()) > 10
                   for key in ("reason", "headline", "summary_close", "why_company")):
            continue
        analysis = parsed
        break
    if analysis is None:
        # Deterministic fallback: heuristic score so runs still produce usable packages.
        title_lower = job["title"].lower()
        must, nice = extract_keywords(job)
        score = 45 if ("rust" in title_lower or "intern" in title_lower or "graduate" in title_lower or "junior" in title_lower) else 35
        if SENIORITY_FLAGS.search(job["title"]):
            score = min(score, 30)
        analysis = {
            "score": score,
            "verdict": "apply" if score >= 60 else "maybe" if score >= 40 else "skip",
            "reason": "Automated LLM analysis unavailable; heuristic score from title keywords. Review manually.",
            "headline": f"{job['title']} (Rust, Python, Go)",
            "summary_close": f"Seeking the {job['title']} role at {job['company']}.",
            "why_company": f"Interested in the {job['title']} role at {job['company']}.",
        }
        analysis["must_keywords"], analysis["nice_keywords"] = must, nice
    verdict = str(analysis["verdict"]).strip().lower()
    if verdict not in ("apply", "maybe", "skip"):
        verdict = "apply" if analysis["score"] >= 60 else "maybe" if analysis["score"] >= 40 else "skip"
    analysis["verdict"] = verdict
    must, nice = extract_keywords(job)
    analysis.setdefault("must_keywords", must)
    analysis.setdefault("nice_keywords", nice)
    reasons = [analysis["reason"]]
    if SENIORITY_FLAGS.search(job["title"]):
        reasons.append("Seniority flag: role targets senior/staff-level candidates; early-career candidate.")
    analysis["reasons"] = reasons
    analysis["red_flags"] = ["Needs sponsorship outside India (candidate is Delhi-based)."]
    return analysis


def cover_letter_paragraphs(job: dict[str, str], analysis: dict) -> list[str]:
    prompt = (
        f"Candidate profile:\n{PROFILE}\n\n"
        f"Job: {job['title']} at {job['company']} ({job['location']}).\n"
        f"Description: {job.get('description', '')[:1200]}\n\n"
        "Write a professional cover letter body as EXACTLY 3 paragraphs of plain text "
        "(no markdown, no bullet points, no salutation or sign-off, no placeholders):\n"
        "Paragraph 1: who I am and which role I am applying for.\n"
        "Paragraph 2: my most relevant projects/skills matched to this job's requirements.\n"
        "Paragraph 3: why this company specifically and my availability.\n"
        "Separate paragraphs with a blank line."
    )
    for temperature in (0.5, 0.3):
        try:
            raw = ollama_generate(prompt, num_predict=700, temperature=temperature)
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError, OSError) as error:
            print(f"  Cover letter generation failed: {error}", file=sys.stderr)
            break
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip() and len(p.strip()) > 40]
        if len(paragraphs) >= 3:
            return paragraphs[:3]
    why = analysis["why_company"]
    return [
        f"I am applying for the {job['title']} role at {job['company']}. I am a 2026 STEM graduate "
        f"with 18 months of project-based backend engineering experience in Rust, Go, and Python.",
        f"My background matches this role: {why}",
        f"I work across the full software development lifecycle with automated testing and CI/CD, and I "
        f"collaborate well with cross-functional stakeholders. I am available immediately and open to remote work.",
    ]


def compile_pdf(tex_path: Path) -> bool:
    for _ in range(2):
        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
            cwd=tex_path.parent, capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            print(f"  pdflatex failed for {tex_path.name}: {result.stdout[-400:]}", file=sys.stderr)
            return False
    pdf = tex_path.with_suffix(".pdf")
    for suffix in (".aux", ".log", ".out"):
        tex_path.with_suffix(suffix).unlink(missing_ok=True)
    return pdf.exists()


def render_score_md(job: dict[str, str], analysis: dict) -> str:
    lines = [
        f"# Score — {job['title']} @ {job['company']}",
        f"**Fit: {analysis['score']}/100 — verdict: {analysis['verdict']}**",
        "",
        "| Criterion | Notes |",
        "|---|---|",
    ]
    for reason in analysis["reasons"]:
        lines.append(f"| Reason | {reason} |")
    for flag in analysis["red_flags"]:
        lines.append(f"| Red flag | {flag} |")
    lines += [
        "",
        f"**Must-have keywords:** {', '.join(analysis['must_keywords']) or '—'}",
        f"**Nice-to-have keywords:** {', '.join(analysis['nice_keywords']) or '—'}",
        "",
        f"**Why this fits:** {analysis['why_company']}",
        "",
        f"*Scored by local Ollama model `{MODEL}` on {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}. "
        "Verify eligibility and details on the original listing before applying.*",
        "",
    ]
    return "\n".join(lines)


def render_job_description_md(job: dict[str, str]) -> str:
    return (
        f"# {job['title']}\n\n"
        f"- **Company:** {job['company']}\n"
        f"- **Location:** {job['location']}\n"
        f"- **Source:** {job['source']}\n"
        f"- **URL:** {job['url']}\n"
        f"- **Published:** {job.get('published', 'unknown')}\n\n"
        f"## Description\n\n{job.get('description', '(no description captured — see URL)')}\n"
    )


def render_screening_md(job: dict[str, str], analysis: dict) -> str:
    return (
        f"# Screening answers — {job['company']}\n\n"
        f"- **Right to work:** India; may require sponsorship abroad — answer truthfully per listing.\n"
        f"- **Notice period:** Available immediately.\n"
        f"- **Remote readiness:** Yes — 18 months of remote project-based engineering; reliable setup.\n"
        f"- **Relocation:** Open to relocating.\n\n"
        f"## Why {job['company']}?\n\n{analysis['why_company']}\n\n"
        f"## Application checklist\n\n"
        f"- [ ] Upload tailored resume PDF\n"
        f"- [ ] Upload cover letter PDF\n"
        f"- [ ] Confirm salary/eligibility on the original listing: {job['url']}\n"
    )


def render_how_i_did_it(job: dict[str, str], analysis: dict, tailored: bool) -> str:
    return (
        f"# How this package was generated\n\n"
        f"- Source listing captured by `scripts/discover_jobs.py` from {job['source']}.\n"
        f"- Fit score, keywords, headline, and summary written by local Ollama model `{MODEL}`.\n"
        f"- Resume built from `templates/resume-template.tex` with a role-specific headline and summary"
        f"{'.' if tailored else ' — score below threshold, so the base resume was used.'}\n"
        f"- Cover letter paragraphs generated by `{MODEL}` and inserted into "
        f"`templates/cover-letter-template.tex`, compiled with pdflatex.\n"
        f"- Score: {analysis['score']}/100 ({analysis['verdict']}). Reasons: "
        f"{'; '.join(analysis['reasons'])}\n"
    )


def build_resume(job: dict[str, str], analysis: dict, folder: Path) -> tuple[Path | None, bool]:
    content = RESUME_TEMPLATE.read_text(encoding="utf-8")
    tailored = True
    headline = escape_latex(analysis["headline"].replace("\n", " ").strip())
    summary_close = escape_latex(analysis["summary_close"].replace("\n", " ").strip())
    filled = (
        content.replace("@@COMPANY@@", escape_latex(job["company"]))
        .replace("@@ROLE@@", escape_latex(job["title"]))
        .replace("@@HEADLINE@@", headline)
        .replace("@@SUMMARY_CLOSE@@", summary_close)
    )
    if "@@" in filled:
        print("  Resume template placeholders missing; falling back to base resume.", file=sys.stderr)
        tailored = False
        filled = content
    tex_path = folder / f"resume-{slugify(job['company'])}.tex"
    tex_path.write_text(filled, encoding="utf-8")
    if not compile_pdf(tex_path):
        tex_path.unlink(missing_ok=True)
        tex_path.with_suffix(".pdf").unlink(missing_ok=True)
        shutil.copy(ROOT / "resume.tex", tex_path)
        compile_pdf(tex_path)
        return tex_path.with_suffix(".pdf"), False
    return tex_path.with_suffix(".pdf"), tailored


def build_cover_letter(job: dict[str, str], analysis: dict, folder: Path) -> Path | None:
    paragraphs = cover_letter_paragraphs(job, analysis)
    today = dt.date.today().strftime("%-d %B %Y") if os.name != "nt" else dt.date.today().strftime("%d %B %Y").lstrip("0")
    content = COVER_TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "@@DATE@@": today,
        "@@COMPANY@@": escape_latex(job["company"]),
        "@@LOCATION@@": escape_latex(job["location"] or "Remote"),
        "@@ROLE@@": escape_latex(job["title"]),
    }
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)
    for index, paragraph in enumerate(paragraphs, start=1):
        content = content.replace(f"@@P{index}@@", escape_latex(paragraph))
    if "@@P" in content:
        print("  Cover letter placeholders missing; skipping PDF.", file=sys.stderr)
        return None
    tex_path = folder / "cover-letter.tex"
    tex_path.write_text(content, encoding="utf-8")
    return tex_path.with_suffix(".pdf") if compile_pdf(tex_path) else None


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:40] or "company"


def next_folder_number() -> int:
    numbers = [0]
    for entry in JOBS_DIR.iterdir():
        match = re.match(r"(\d+)-", entry.name)
        if entry.is_dir() and match:
            numbers.append(int(match.group(1)))
    return max(numbers) + 1


def select_candidates(state: dict, applications: dict) -> list[dict[str, str]]:
    candidates = [
        job for key, job in state.get("jobs", {}).items()
        if key not in applications and job.get("title") and job.get("company")
    ]
    candidates.sort(key=lambda job: job.get("first_found", ""), reverse=True)
    return candidates[:MAX_JOBS_PER_RUN]


def render_index(applications: dict) -> str:
    rows = sorted(applications.items(), key=lambda item: item[1].get("score", 0), reverse=True)
    lines = [
        "# Tailored applications index",
        "",
        "Generated by `scripts/tailor_applications.py` (local Ollama scoring). "
        f"Full packages (tailored resume + cover letter) are generated only when the score reaches {SCORE_THRESHOLD}; "
        "lower scores get a score card only.",
        "",
        "| Score | Verdict | Role | Company | Source | Package |",
        "|---|---|---|---|---|---|",
    ]
    for key, entry in rows:
        folder = entry.get("folder", "")
        folder_link = f"[{folder}](<{folder}/>)" if folder else "—"
        role = str(entry.get("title", "")).replace("|", "\\|")
        company = str(entry.get("company", "")).replace("|", "\\|")
        lines.append(
            f"| {entry.get('score', '—')} | {entry.get('verdict', '—')} | {role} | {company} | "
            f"{entry.get('source', '—')} | {folder_link} |"
        )
    lines.append("")
    return "\n".join(lines)


def process_job(job: dict[str, str], folder_number: int) -> dict:
    folder_name = f"{folder_number:02d}-{slugify(job['company'])}-{slugify(job['title'])[:30]}"
    folder = JOBS_DIR / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Processing {folder_name}: {job['title']} @ {job['company']}")
    analysis = analyse_job(job)
    (folder / "job_description.md").write_text(render_job_description_md(job), encoding="utf-8")
    (folder / "score.md").write_text(render_score_md(job, analysis), encoding="utf-8")
    (folder / "screening-answers.md").write_text(render_screening_md(job, analysis), encoding="utf-8")
    (folder / "how-i-did-it.md").write_text(render_how_i_did_it(job, analysis, analysis["score"] >= SCORE_THRESHOLD), encoding="utf-8")
    if analysis["score"] >= SCORE_THRESHOLD:
        resume_pdf, _ = build_resume(job, analysis, folder)
        if resume_pdf is None:
            print("  Resume build failed; continuing.", file=sys.stderr)
        cover_pdf = build_cover_letter(job, analysis, folder)
        if cover_pdf is None:
            print("  Cover letter build failed; continuing.", file=sys.stderr)
    else:
        print(f"  Score {analysis['score']} below threshold {SCORE_THRESHOLD}; score card only.")
    return {
        "folder": folder_name,
        "score": analysis["score"],
        "verdict": analysis["verdict"],
        "title": job["title"],
        "company": job["company"],
        "source": job["source"],
        "url": job["url"],
        "processed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


def main() -> int:
    if not ollama_available():
        print(f"Ollama or model '{MODEL}' not reachable at {OLLAMA_URL}; skipping tailoring.")
        return 0
    if not RESUME_TEMPLATE.exists() or not COVER_TEMPLATE.exists():
        print("Templates missing; cannot tailor.", file=sys.stderr)
        return 1
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {"jobs": {}}
    applications = json.loads(APPLICATIONS_PATH.read_text(encoding="utf-8")) if APPLICATIONS_PATH.exists() else {}
    candidates = select_candidates(state, applications)
    if not candidates:
        print("No new jobs to tailor.")
        return 0
    folder_number = next_folder_number()
    for job in candidates:
        key = job["key"]
        try:
            applications[key] = process_job(job, folder_number)
        except Exception as error:  # keep the run alive; one bad job must not kill the batch
            print(f"  Failed to process {key}: {error}", file=sys.stderr)
            applications[key] = {
                "folder": "", "score": 0, "verdict": "error", "title": job.get("title", ""),
                "company": job.get("company", ""), "source": job.get("source", ""),
                "url": job.get("url", ""),
                "processed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            }
        folder_number += 1
    APPLICATIONS_PATH.write_text(json.dumps(applications, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    INDEX_PATH.write_text(render_index(applications), encoding="utf-8")
    print(f"Tailoring complete: {len(candidates)} job package(s) generated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
