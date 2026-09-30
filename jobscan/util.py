from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("jobscan")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def session(timeout: int = 25) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
    )
    ad = HTTPAdapter(max_retries=retry, pool_maxsize=16)
    s.mount("https://", ad)
    s.mount("http://", ad)
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/html;q=0.8"})
    s.request_timeout = timeout  # type: ignore[attr-defined]
    return s


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n{3,}")


def strip_html(raw: str | None) -> str:
    """Cheap HTML -> text. JD bodies are only used for keyword/LLM scoring."""
    if not raw:
        return ""
    txt = raw.replace("\r\n", "\n")
    txt = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", txt)
    txt = re.sub(r"(?i)<br\s*/?>", "\n", txt)
    txt = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", txt)
    txt = re.sub(r"(?i)<li[^>]*>", "- ", txt)
    txt = _TAG.sub(" ", txt)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'"), ("&rsquo;", "'"), ("&ndash;", "-")):
        txt = txt.replace(a, b)
    txt = _WS.sub(" ", txt)
    txt = _NL.sub("\n\n", txt)
    return txt.strip()


def job_id(source: str, company: str, title: str, url: str) -> str:
    """Stable id. URL alone is unreliable (tracking params), so hash the tuple."""
    base = "|".join([source.lower().strip(), company.lower().strip(),
                     title.lower().strip(), url.split("?")[0].strip()])
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def parse_date(val) -> datetime | None:
    """Best-effort date parsing across the very different formats each source emits."""
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, (int, float)):
        ts = float(val)
        if ts > 1e11:  # milliseconds
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    s = str(val).strip()

    # Workday emits relative strings: "Posted 3 Days Ago", "Posted Today".
    m = re.search(r"(\d+)\+?\s*day", s, re.I)
    if m:
        return datetime.now(timezone.utc) - timedelta(days=int(m.group(1)))
    if re.search(r"posted\s+today", s, re.I):
        return datetime.now(timezone.utc)
    if re.search(r"posted\s+yesterday", s, re.I):
        return datetime.now(timezone.utc) - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*month", s, re.I)
    if m:
        return datetime.now(timezone.utc) - timedelta(days=30 * int(m.group(1)))

    s2 = s.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%b %d, %Y", "%d %b %Y"):
        try:
            dt = datetime.fromisoformat(s2) if fmt is None else datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def days_old(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
