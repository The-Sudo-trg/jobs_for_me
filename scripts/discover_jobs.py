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
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "jobs" / ".seen_jobs.json"
TRACKER_PATH = ROOT / "jobs" / "daily-tracker.md"
TIMEOUT_SECONDS = 30
TECH_ROLE = re.compile(r"\b(software|developer|engineer|programmer|quant|data|computer|technology|technical)\b", re.I)
INTERN_OR_EARLY = re.compile(r"\b(intern(?:ship)?|graduate|junior|entry[- ]level|new grad|trainee)\b", re.I)
RUST = re.compile(r"\brust\b", re.I)

SOURCES = {
    "Arbeitnow": "https://www.arbeitnow.com/api/job-board-api",
    "Remote OK": "https://remoteok.com/api",
    "Himalayas": "https://himalayas.app/jobs/api?limit=100",
}


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "jobs_for_me job tracker/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} from {url}")
        return json.load(response)


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
) -> dict[str, str]:
    title_text = clean_text(title)
    company_text = clean_text(company)
    url_text = clean_text(url)
    if not title_text or not company_text or not url_text:
        return {}
    tag_text = clean_text(", ".join(map(str, tags)) if isinstance(tags, list) else tags)
    rust_related = bool(RUST.search(title_text) or (rust_tags_are_specific and RUST.search(tag_text)))
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
    }


def normalize_arbeitnow(payload: Any) -> list[dict[str, str]]:
    return [
        job
        for item in payload["data"]
        if item.get("remote") is True
        if (job := make_job(
            "Arbeitnow", item.get("title"), item.get("company_name"), item.get("url"),
            item.get("location"), item.get("created_at"), item.get("tags"), item.get("slug"),
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


NORMALIZERS = {
    "Arbeitnow": normalize_arbeitnow,
    "Remote OK": normalize_remoteok,
    "Himalayas": normalize_himalayas,
}


def collect_jobs() -> list[dict[str, str]]:
    jobs: dict[str, dict[str, str]] = {}
    for source, url in SOURCES.items():
        try:
            results = NORMALIZERS[source](fetch_json(url))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise RuntimeError(f"Failed to retrieve or parse {source}: {error}") from error
        for job in results:
            jobs.setdefault(job["key"], job)
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
        "[Himalayas](https://himalayas.app/).",
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
