from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

from .util import job_id


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


@dataclass
class Job:
    source: str
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""
    posted_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # filled in by scoring
    rule_score: int = 0
    rule_notes: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    llm_score: int | None = None
    llm_verdict: str = ""
    llm_reason: str = ""
    llm_match: str = ""
    llm_gap: str = ""
    sponsorship_risk: str = ""

    def __post_init__(self) -> None:
        # Aggregators return titles with embedded newlines and doubled spaces,
        # which breaks both display and (company, title) dedupe.
        self.title = _clean(self.title)
        self.company = _clean(self.company)
        self.location = _clean(self.location)

    @property
    def id(self) -> str:
        return job_id(self.source, self.company, self.title, self.url)

    @property
    def final_score(self) -> int:
        """LLM score wins when present; rules are the fallback and the pre-filter."""
        return self.llm_score if self.llm_score is not None else self.rule_score

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("extra", None)
        d["id"] = self.id
        d["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        d["rule_notes"] = " | ".join(self.rule_notes)
        d["blockers"] = " | ".join(self.blockers)
        d["final_score"] = self.final_score
        return d
