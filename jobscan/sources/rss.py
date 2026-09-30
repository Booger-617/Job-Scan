"""Generic RSS/Atom reader - point it at LinkedIn/Handshake/company job feeds."""
from __future__ import annotations

import logging

import feedparser

from ..models import Job
from ..util import strip_html, parse_date

log = logging.getLogger("jobscan.rss")


def probe(url: str) -> tuple[bool, str]:
    try:
        d = feedparser.parse(url)
        if getattr(d, "bozo", 0) and not d.entries:
            return False, str(getattr(d, "bozo_exception", "unparseable"))[:80]
        return True, "%d entries" % len(d.entries)
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:80]


def fetch(cfg: dict) -> list[Job]:
    out: list[Job] = []
    for feed in cfg.get("feeds", []):
        url = feed["url"] if isinstance(feed, dict) else str(feed)
        label = feed.get("name", "RSS") if isinstance(feed, dict) else "RSS"
        try:
            d = feedparser.parse(url)
            for e in d.entries:
                raw_title = e.get("title", "")
                # Common feed convention: "Job Title - Company - Location"
                parts = [p.strip() for p in raw_title.split(" - ")]
                title = parts[0] if parts else raw_title
                company = parts[1] if len(parts) >= 2 else label
                out.append(Job(
                    source="rss",
                    company=company,
                    title=title,
                    url=e.get("link", ""),
                    location=parts[2] if len(parts) >= 3 else "",
                    description=strip_html(e.get("summary", "") or e.get("description", "")),
                    posted_at=parse_date(e.get("published") or e.get("updated")),
                ))
        except Exception as ex:  # noqa: BLE001
            log.warning("rss %s failed: %s", url, ex)
    return out
