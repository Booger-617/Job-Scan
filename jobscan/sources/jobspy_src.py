"""Indeed / LinkedIn / Glassdoor via the `python-jobspy` package (optional).

Not installed by default:  pip install python-jobspy

This one scrapes rather than using a public API, so it is the flakiest source -
LinkedIn rate-limits aggressively. Failures here are logged and ignored so a
blocked scrape never takes down the whole run.
"""
from __future__ import annotations

import logging

from ..models import Job
from ..util import strip_html, parse_date

log = logging.getLogger("jobscan.jobspy")


def available() -> bool:
    try:
        import jobspy  # noqa: F401
        return True
    except ImportError:
        return False


def probe(_cfg) -> tuple[bool, str]:
    if available():
        return True, "python-jobspy installed"
    return False, "not installed - run: pip install python-jobspy"


def _s(row, key) -> str:
    val = row.get(key)
    if val is None:
        return ""
    text = str(val)
    return "" if text in ("nan", "None", "NaT") else text


def fetch(cfg: dict) -> list[Job]:
    if not available():
        log.warning("jobspy not installed; skipping (pip install python-jobspy)")
        return []
    from jobspy import scrape_jobs

    out: list[Job] = []
    sites = cfg.get("sites", ["indeed"])
    hours_old = int(cfg.get("hours_old", 168))
    wanted = int(cfg.get("results_wanted", 40))
    for term in cfg.get("search_terms", []):
        for loc in cfg.get("locations", ["Washington, DC"]):
            try:
                df = scrape_jobs(
                    site_name=sites,
                    search_term=term,
                    location=loc,
                    results_wanted=wanted,
                    hours_old=hours_old,
                    country_indeed=cfg.get("country_indeed", "USA"),
                    description_format="markdown",
                )
            except Exception as e:  # noqa: BLE001
                log.warning("jobspy '%s' @ %s failed: %s", term, loc, e)
                continue
            if df is None or len(df) == 0:
                continue
            for _, row in df.iterrows():
                url = _s(row, "job_url")
                if not url:
                    continue
                site = _s(row, "site") or "web"
                out.append(Job(
                    source="jobspy:" + site,
                    company=_s(row, "company") or "?",
                    title=_s(row, "title"),
                    url=url,
                    location=_s(row, "location"),
                    description=strip_html(_s(row, "description"))[:12000],
                    posted_at=parse_date(row.get("date_posted")),
                    extra={"min_amount": _s(row, "min_amount"), "max_amount": _s(row, "max_amount")},
                ))
    return out
