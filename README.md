# jobs_for_me

## Daily remote software job tracker

The scheduled GitHub Actions workflow checks public job-board APIs twice daily at
08:00 and 18:00 India Standard Time (02:30 and 12:30 UTC). GitHub may start
scheduled runs late; the times are not guaranteed to be exact.

The script looks for Rust-tagged or Rust-titled jobs and remote software
internships / early-career roles. It records each listing only once in
`jobs/daily-tracker.md`, preserves seen listing IDs in `jobs/.seen_jobs.json`,
and commits and pushes only those two tracker files. It does not apply to jobs
or edit the resume. The workflow needs Actions enabled and permission for the
repository `GITHUB_TOKEN` to write contents; branch protection may prevent its
push.

Listings come from the public APIs for [Arbeitnow](https://www.arbeitnow.com/),
[Remote OK](https://remoteok.com/), and [Himalayas](https://himalayas.app/).
Original listing links and source names are retained to respect attribution.
Remote eligibility and application status must be checked on the employer's
listing.

Run locally with `python scripts/discover_jobs.py`. Run the test suite with
`python -m unittest discover -s tests`.

## Tailored resumes

Role-specific resume versions and their fit notes are saved under `jobs/`.
Always verify role eligibility requirements; a tailored resume does not imply
eligibility.
