"""Workday CxS search endpoint.

Every Workday customer exposes the same JSON search API that their own careers
page calls:

    POST https://<tenant>.wdN.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs
    {"appliedFacets":{}, "limit":20, "offset":0, "searchText":"quant"}

Tenant/site names differ per employer, so `jobscan verify` exists to tell you
which of the configured URLs actually answer.
"""
from __future__ import annotations

import concurrent.futures as futures
import logging
import re
import time

from ..models import Job
from ..util import session, strip_html, parse_date

log = logging.getLogger("jobscan.workday")
PAGE = 20

# The list endpoint returns no JD body, which would leave every Workday posting
# with a near-zero keyword score. Hydrate the ones whose title is plausibly
# relevant, so they compete fairly with Greenhouse/Lever postings.
INTERESTING = re.compile(
    r"analyst|analytic|quantitat|data\s+scien|research|risk|model|financ|invest|"
    r"portfolio|machine\s+learning|statistic|econom|associate|strateg",
    re.I,
)
SENIOR = re.compile(
    r"\b(senior|sr\.?|principal|staff|lead|director|head|vp|avp|svp|evp|"
    r"vice president|manager|executive)\b", re.I)


def _post(s, url: str, term: str, offset: int):
    return s.post(
        url,
        json={"appliedFacets": {}, "limit": PAGE, "offset": offset, "searchText": term},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=25,
    )


def probe(url: str) -> tuple[bool, str]:
    s = session()
    try:
        r = _post(s, url, "analyst", 0)
        if r.status_code != 200:
            return False, "HTTP %s (tenant/site path likely wrong)" % r.status_code
        return True, "%s jobs match 'analyst'" % r.json().get("total", "?")
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:80]


def _site_root(url: str) -> str:
    """https://x.wd1.myworkdayjobs.com/wday/cxs/x/SITE/jobs -> https://x.wd1.myworkdayjobs.com/SITE"""
    try:
        base, rest = url.split("/wday/cxs/", 1)
        parts = rest.split("/")
        return base + "/" + parts[1] if len(parts) >= 2 else base
    except (ValueError, IndexError):
        return url


def fetch(cfg: dict) -> list[Job]:
    out: list[Job] = []
    s = session()
    terms = cfg.get("search_terms") or [""]
    max_pages = int(cfg.get("max_pages", 3))
    for tenant in cfg.get("tenants", []):
        name = tenant.get("name", "?")
        url = tenant.get("url", "")
        if not url:
            continue
        root = _site_root(url)
        for term in terms:
            for page in range(max_pages):
                try:
                    r = _post(s, url, term, page * PAGE)
                    if r.status_code != 200:
                        log.warning("workday %s: HTTP %s (check tenant url)", name, r.status_code)
                        break
                    postings = r.json().get("jobPostings", [])
                    if not postings:
                        break
                    for j in postings:
                        path = j.get("externalPath", "")
                        bullets = j.get("bulletFields") or []
                        out.append(Job(
                            source="workday",
                            company=name,
                            title=j.get("title", ""),
                            url=root + path if path.startswith("/") else path,
                            location=j.get("locationsText", "") or "",
                            # Filled in by hydrate(); bullets are just the req number.
                            description=strip_html(" ".join(bullets)),
                            posted_at=parse_date(j.get("postedOn", "")),
                            extra={"req": bullets[0] if bullets else "",
                                   "detail": _detail_url(url, path)},
                        ))
                    if len(postings) < PAGE:
                        break
                    time.sleep(0.4)
                except Exception as e:  # noqa: BLE001
                    log.warning("workday %s '%s' failed: %s", name, term, e)
                    break
    return hydrate(out, cfg)


def _detail_url(list_url: str, external_path: str) -> str:
    """The CxS detail endpoint is the list endpoint's parent + the externalPath."""
    if not external_path:
        return ""
    return list_url[: -len("/jobs")] + external_path if list_url.endswith("/jobs") else ""


def _fetch_one(s, job: Job) -> None:
    url = job.extra.get("detail", "")
    if not url:
        return
    try:
        r = s.get(url, headers={"Accept": "application/json"}, timeout=20)
        if r.status_code != 200:
            return
        info = r.json().get("jobPostingInfo", {}) or {}
        body = info.get("jobDescription") or ""
        if body:
            job.description = strip_html(body)[:14000]
        posted = info.get("startDate") or info.get("postedOn")
        if posted:
            job.posted_at = parse_date(posted) or job.posted_at
        if info.get("country"):
            job.extra["country"] = (info["country"] or {}).get("descriptor", "")
        # The list endpoint collapses multi-site postings to "5 Locations", which
        # is unscoreable. The detail endpoint has the real ones.
        locs = [info.get("location") or ""]
        locs += list(info.get("additionalLocations") or [])
        locs = [str(x) for x in locs if x]
        if locs:
            job.location = "; ".join(dict.fromkeys(locs))[:300]
    except Exception:  # noqa: BLE001
        pass  # a missing JD just means the posting scores on title alone


def hydrate(jobs: list[Job], cfg: dict) -> list[Job]:
    """Pull full JD text for the subset of postings worth scoring properly."""
    limit = int(cfg.get("detail_limit", 220))
    if limit <= 0:
        return jobs
    seen: set[str] = set()
    pool: list[Job] = []
    for j in jobs:
        if not j.extra.get("detail") or j.id in seen:
            continue
        if not INTERESTING.search(j.title or "") or SENIOR.search(j.title or ""):
            continue
        seen.add(j.id)
        pool.append(j)

    # Round-robin by employer, otherwise the first tenant in the config eats the
    # whole budget and the rest of the list never gets a JD at all.
    buckets: dict[str, list[Job]] = {}
    for j in pool:
        buckets.setdefault(j.company, []).append(j)
    targets: list[Job] = []
    while len(targets) < limit and any(buckets.values()):
        for co in list(buckets):
            if not buckets[co]:
                del buckets[co]
                continue
            targets.append(buckets[co].pop(0))
            if len(targets) >= limit:
                break
    if not targets:
        return jobs
    log.info("workday: hydrating %d of %d postings with full JD text", len(targets), len(jobs))
    s = session()
    with futures.ThreadPoolExecutor(max_workers=int(cfg.get("detail_workers", 8))) as ex:
        list(ex.map(lambda j: _fetch_one(s, j), targets))
    got = sum(1 for j in targets if len(j.description) > 200)
    log.info("workday: got JD text for %d/%d", got, len(targets))
    return jobs
