#!/usr/bin/env python3
"""Collect remote software internships and Rust-related roles into a durable tracker."""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "jobs" / ".seen_jobs.json"
TRACKER_PATH = ROOT / "jobs" / "daily-tracker.md"
TIMEOUT_SECONDS = 30
MAX_DESCRIPTION_CHARS = 3000
TECH_ROLE = re.compile(r"\b(software|developer|engineer|programmer|quant|data|computer|technology|technical)\b", re.I)
INTERN_OR_EARLY = re.compile(r"\b(intern(?:ship)?|graduate|junior|entry[- ]level|new grad|trainee)\b", re.I)
RUST = re.compile(r"\brust\b", re.I)

SOURCES: dict[str, list[str]] = {
    "Arbeitnow": ["https://www.arbeitnow.com/api/job-board-api"],
    "Remote OK": ["https://remoteok.com/api"],
    "Himalayas": ["https://himalayas.app/jobs/api?limit=100"],
    "Remotive": ["https://remotive.com/api/remote-jobs?category=software-dev&limit=100"],
    "Jobicy": [
        "https://jobicy.com/api/v2/remote-jobs?count=50&tag=rust",
        "https://jobicy.com/api/v2/remote-jobs?count=50&tag=blockchain",
    ],
    "We Work Remotely": [
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    ],
    "Crypto Jobs List": ["https://api.cryptojobslist.com/jobs.rss"],
}


def _request(url: str, accept: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "jobs_for_me job tracker/1.0", "Accept": accept},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} from {url}")
        return response.read()


def fetch_json(url: str) -> Any:
    return json.loads(_request(url, "application/json").decode("utf-8", "replace"))


def fetch_text(url: str) -> str:
    return _request(url, "application/rss+xml, application/xml, text/xml").decode("utf-8", "replace")


def strip_html(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def clean_text(value: Any) -> str:
    if isinstance(value, list):
        value = ", ".join(map(str, value))
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def to_iso(value: Any) -> str:
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat(timespec="seconds")
    if not value:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = dt.datetime.strptime(str(value)[:25].strip(), "%a, %d %b %Y %H:%M:%S")
        except ValueError:
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def make_job(
    source: str,
    title: Any,
    company: Any,
    url: Any,
    location: Any,
    published: Any,
    tags: Any,
    uid: Any,
    *,
    rust_tags_are_specific: bool = True,
    description: Any = "",
    match_text: Any = "",
) -> dict[str, str]:
    title_text = clean_text(title)
    company_text = clean_text(company)
    url_text = clean_text(url)
    if not title_text or not company_text or not url_text:
        return {}
    tag_text = clean_text(", ".join(map(str, tags)) if isinstance(tags, list) else tags)
    rust_related = bool(
        RUST.search(title_text)
        or (rust_tags_are_specific and RUST.search(tag_text))
        or RUST.search(clean_text(match_text))
    )
    early_tech = bool(TECH_ROLE.search(title_text) and INTERN_OR_EARLY.search(title_text))
    if not rust_related and not early_tech:
        return {}
    key = clean_text(uid) or url_text
    return {
        "key": f"{source}:{key}",
        "title": title_text,
        "company": company_text,
        "url": url_text,
        "location": clean_text(location) or "Remote",
        "published": to_iso(published),
        "source": source,
        "match": "Rust-related" if rust_related else "Internship / early-career software",
        "description": strip_html(description)[:MAX_DESCRIPTION_CHARS],
    }


def normalize_arbeitnow(payload: Any) -> list[dict[str, str]]:
    return [
        job
        for item in payload["data"]
        if item.get("remote") is True
        if (job := make_job(
            "Arbeitnow", item.get("title"), item.get("company_name"), item.get("url"),
            item.get("location"), item.get("created_at"), item.get("tags"), item.get("slug"),
            description=item.get("description"),
        ))
    ]


def normalize_remoteok(payload: Any) -> list[dict[str, str]]:
    return [
        job
        for item in payload
        if isinstance(item, dict) and item.get("position")
        if (job := make_job(
            "Remote OK", item.get("position"), item.get("company"),
            item.get("url") or f"https://remoteok.com/l/{item.get('slug', '')}",
            "Remote", item.get("date") or item.get("epoch"), item.get("tags"), item.get("id"),
            description=item.get("description"),
        ))
    ]


def normalize_himalayas(payload: Any) -> list[dict[str, str]]:
    return [
        job
        for item in payload["jobs"]
        if (job := make_job(
            "Himalayas", item.get("title"), item.get("companyName"),
            item.get("applicationLink") or item.get("guid"), item.get("locationRestrictions"),
            item.get("pubDate"), item.get("categories"), item.get("guid"),
        ))
    ]


def normalize_remotive(payload: Any) -> list[dict[str, str]]:
    return [
        job
        for item in payload.get("jobs", [])
        if (job := make_job(
            "Remotive", item.get("title"), item.get("company_name"), item.get("url"),
            item.get("candidate_required_location"), item.get("publication_date"),
            item.get("tags"), item.get("id"),
            description=item.get("description"),
        ))
    ]


def normalize_jobicy(payload: Any) -> list[dict[str, str]]:
    return [
        job
        for item in payload.get("jobs", [])
        if (job := make_job(
            "Jobicy", item.get("jobTitle"), item.get("companyName"), item.get("url"),
            item.get("jobGeo"), item.get("pubDate"), item.get("tags"), item.get("id"),
            description=item.get("jobExcerpt"),
        ))
    ]


def parse_rss(xml_text: str) -> list[dict[str, str]]:
    """Return {title, link, pubDate, description} for each RSS item, namespace-agnostic."""
    root = ET.fromstring(xml_text)
    items: list[dict[str, str]] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "item":
            continue
        fields: dict[str, str] = {}
        for child in element:
            name = child.tag.rsplit("}", 1)[-1]
            if name in ("title", "link", "pubDate", "description", "creator"):
                fields[name] = (child.text or "").strip()
        if fields.get("title") and fields.get("link"):
            items.append(fields)
    return items


def _normalize_rss_items(items: list[dict[str, str]], source: str, split_company: bool) -> list[dict[str, str]]:
    jobs: list[dict[str, str]] = []
    for item in items:
        title = clean_text(item.get("title"))
        company = clean_text(item.get("creator"))
        if not company:
            company = "Unknown"
        if split_company and ": " in title:
            prefix, remainder = title.split(": ", 1)
            if prefix and len(prefix) < 60:
                company, title = prefix, remainder
        jobs.append(make_job(
            source, title, company, item.get("link"), "Remote", item.get("pubDate"),
            [], item.get("link"),
            rust_tags_are_specific=False,
            description=item.get("description"),
            match_text=f"{title} {item.get('description', '')}",
        ))
    return [job for job in jobs if job]


def normalize_wwr(items: list[dict[str, str]]) -> list[dict[str, str]]:
    return _normalize_rss_items(items, "We Work Remotely", split_company=True)


def normalize_cryptojobslist(items: list[dict[str, str]]) -> list[dict[str, str]]:
    return _normalize_rss_items(items, "Crypto Jobs List", split_company=False)


NORMALIZERS = {
    "Arbeitnow": normalize_arbeitnow,
    "Remote OK": normalize_remoteok,
    "Himalayas": normalize_himalayas,
    "Remotive": normalize_remotive,
    "Jobicy": normalize_jobicy,
    "We Work Remotely": normalize_wwr,
    "Crypto Jobs List": normalize_cryptojobslist,
}


def collect_jobs() -> list[dict[str, str]]:
    jobs: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    for source, urls in SOURCES.items():
        for url in urls:
            try:
                if url.endswith(".rss"):
                    results = NORMALIZERS[source](parse_rss(fetch_text(url)))
                else:
                    results = NORMALIZERS[source](fetch_json(url))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError,
                    TypeError, ValueError, ET.ParseError) as error:
                failures.append(f"{source}: {error}")
                continue
            for job in results:
                jobs.setdefault(job["key"], job)
    if not jobs and failures:
        raise RuntimeError("All job sources failed: " + "; ".join(failures))
    if failures:
        print(f"Warning: {len(failures)} source fetch(es) failed: {'; '.join(failures)}", file=sys.stderr)
    return sorted(jobs.values(), key=lambda job: (job["published"], job["title"]), reverse=True)


def render_tracker(state: dict[str, Any]) -> str:
    rows = sorted(
        state["jobs"].values(),
        key=lambda job: (job.get("published", ""), job["title"]),
        reverse=True,
    )
    lines = [
        "# Remote software internships and Rust jobs",
        "",
        f"Last checked: {state['last_checked']}",
        "",
        "This tracker lists remote Rust-tagged roles and software internships / early-career roles.",
        "Sources: [Arbeitnow](https://www.arbeitnow.com/), [Remote OK](https://remoteok.com/), "
        "[Himalayas](https://himalayas.app/), [Remotive](https://remotive.com/), [Jobicy](https://jobicy.com/), "
        "[We Work Remotely](https://weworkremotely.com/), [Crypto Jobs List](https://cryptojobslist.com/).",
        "Listings are linked to their original source; remote and eligibility restrictions may apply.",
        "",
        "| First found (UTC) | Role | Company | Location | Match | Source |",
        "|---|---|---|---|---|---|",
    ]
    if rows:
        for job in rows:
            date = job.get("first_found") or job.get("published") or "Not provided"
            cells = [date, job["title"], job["company"], job["location"], job["match"]]
            escaped = [
                cell.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]").replace("\n", " ")
                for cell in cells
            ]
            listing_url = urllib.parse.quote(job["url"], safe=":/?&=#%+;,~@!$'*-._")
            lines.append(
                f"| {escaped[0]} | [{escaped[1]}](<{listing_url}>) | {escaped[2]} | "
                f"{escaped[3]} | {escaped[4]} | {job['source']} |"
            )
    else:
        lines.append("| — | No matching listings returned | — | — | — | — |")
    lines.append("")
    return "\n".join(lines)


def update_tracker(now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(dt.timezone.utc)
    now = now.astimezone(dt.timezone.utc).replace(microsecond=0)
    discovered = collect_jobs()
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    else:
        state = {"jobs": {}}
    if not isinstance(state.get("jobs"), dict):
        raise ValueError(f"Invalid job history in {STATE_PATH}")
    new_count = 0
    for job in discovered:
        if job["key"] not in state["jobs"]:
            job["first_found"] = now.isoformat()
            state["jobs"][job["key"]] = job
            new_count += 1
        elif not state["jobs"][job["key"]].get("description") and job.get("description"):
            state["jobs"][job["key"]]["description"] = job["description"]
    state["last_checked"] = now.isoformat()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    TRACKER_PATH.write_text(render_tracker(state), encoding="utf-8")
    return new_count


if __name__ == "__main__":
    try:
        count = update_tracker()
    except (RuntimeError, OSError, ValueError) as error:
        print(f"Job discovery failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(f"Job discovery complete: {count} new matching listings.")
