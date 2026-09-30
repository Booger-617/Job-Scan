"""Lever public postings API. Documented, no auth."""
from __future__ import annotations

import logging

from ..models import Job
from ..util import session, strip_html, parse_date

log = logging.getLogger("jobscan.lever")
API = "https://api.lever.co/v0/postings/{slug}?mode=json"


def probe(slug: str) -> tuple[bool, str]:
    s = session()
    try:
        r = s.get(API.format(slug=slug), timeout=20)
        if r.status_code in (404, 400):
            return False, f"{r.status_code} (slug wrong)"
        r.raise_for_status()
        return True, f"{len(r.json())} jobs"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:80]


def fetch(cfg: dict) -> list[Job]:
    out: list[Job] = []
    s = session()
    for entry in cfg.get("companies", []):
        slug = entry["slug"] if isinstance(entry, dict) else str(entry)
        display = entry.get("name", slug) if isinstance(entry, dict) else slug
        try:
            r = s.get(API.format(slug=slug), timeout=25)
            if r.status_code in (400, 404):
                log.warning("lever: slug '%s' not found - remove or fix", slug)
                continue
            r.raise_for_status()
            for j in r.json():
                cats = j.get("categories") or {}
                body = strip_html(j.get("descriptionPlain") or j.get("description", ""))
                for lst in (j.get("lists") or []):
                    body += "\n" + strip_html(lst.get("text", "")) + "\n" + strip_html(lst.get("content", ""))
                out.append(Job(
                    source="lever",
                    company=display,
                    title=j.get("text", ""),
                    url=j.get("hostedUrl", ""),
                    location=cats.get("location", "") or "",
                    description=body,
                    posted_at=parse_date(j.get("createdAt")),
                    extra={"team": cats.get("team", ""), "commitment": cats.get("commitment", "")},
                ))
        except Exception as e:  # noqa: BLE001
            log.warning("lever %s failed: %s", slug, e)
    return out
