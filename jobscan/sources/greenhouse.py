"""Greenhouse public job-board API. Documented, no auth, no scraping."""
from __future__ import annotations

import logging

from ..models import Job
from ..util import session, strip_html, parse_date

log = logging.getLogger("jobscan.greenhouse")
API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def probe(token: str) -> tuple[bool, str]:
    s = session()
    try:
        r = s.get(API.format(token=token), timeout=20)
        if r.status_code == 404:
            return False, "404 (board token wrong)"
        r.raise_for_status()
        return True, f"{len(r.json().get('jobs', []))} jobs"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:80]


def fetch(cfg: dict) -> list[Job]:
    out: list[Job] = []
    s = session()
    for entry in cfg.get("boards", []):
        token = entry["token"] if isinstance(entry, dict) else str(entry)
        display = entry.get("name", token) if isinstance(entry, dict) else token
        try:
            r = s.get(API.format(token=token), timeout=25)
            if r.status_code == 404:
                log.warning("greenhouse: board '%s' not found (404) - remove or fix token", token)
                continue
            r.raise_for_status()
            for j in r.json().get("jobs", []):
                loc = (j.get("location") or {}).get("name", "")
                out.append(Job(
                    source="greenhouse",
                    company=display,
                    title=j.get("title", ""),
                    url=j.get("absolute_url", ""),
                    location=loc,
                    description=strip_html(j.get("content", "")),
                    posted_at=parse_date(j.get("updated_at") or j.get("created_at")),
                    extra={"gh_id": j.get("id")},
                ))
        except Exception as e:  # noqa: BLE001
            log.warning("greenhouse %s failed: %s", token, e)
    return out
