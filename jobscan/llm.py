"""Claude semantic re-screen.

Only jobs that survive the rule filter get here, so the API bill scales with
how well tuned rules.py is, not with how many postings exist.
"""
from __future__ import annotations

import json
import logging
import os

from .models import Job

log = logging.getLogger("jobscan.llm")

SYSTEM = """You screen job postings for one specific candidate. You are blunt and calibrated:
most postings are mediocre matches and should score below 60. Reserve 80+ for postings
the candidate should apply to today.

Return one result per job, with `i` set to that job's input index.

Field rules:
- score: 0-100 overall fit.
- verdict: strong | good | marginal | skip.
- match: at most 15 words, English, the strongest genuine overlap.
- gap: at most 15 words, English, the biggest thing the candidate lacks.
- sponsorship: likely_ok | unclear | likely_blocked.
- reason: at most 45 Chinese characters explaining the score.

Scoring guidance:
- Penalise hard when the posting wants seniority, a PhD, C++/low-latency systems, or
  US citizenship/clearance the candidate cannot get.
- Reward postings where the candidate's actual demonstrated work (factor models,
  CAPM/regression, Python data pipelines, LLM fine-tuning, derivatives exposure,
  FRM) maps onto the day-one responsibilities.
- A prestigious employer does NOT raise the score. Fit does."""

# The API validates the response against this, so a truncated or chatty reply
# stops being a parsing problem. Before this existed, 4 of 7 batches in a real
# run came back unparseable because adaptive thinking ate the max_tokens budget.
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "score": {"type": "integer"},
                    "verdict": {"type": "string",
                                "enum": ["strong", "good", "marginal", "skip"]},
                    "match": {"type": "string"},
                    "gap": {"type": "string"},
                    "sponsorship": {"type": "string",
                                    "enum": ["likely_ok", "unclear", "likely_blocked"]},
                    "reason": {"type": "string"},
                },
                "required": ["i", "score", "verdict", "match", "gap",
                             "sponsorship", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic SDK not installed; skipping LLM stage")
        return None
    return anthropic.Anthropic(api_key=key)


def _payload(jobs: list[Job], jd_chars: int) -> str:
    items = []
    for i, j in enumerate(jobs):
        items.append({
            "i": i,
            "title": j.title,
            "company": j.company,
            "location": j.location,
            "jd": (j.description or "")[:jd_chars],
        })
    return json.dumps(items, ensure_ascii=False)


def _parse(text: str) -> list[dict]:
    """Accept the schema's {"results": [...]} and a bare [...] alike.

    The bare-array branch only matters when structured output is turned off in
    config; it is kept because it is also what a fallback model would emit.
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.lower().startswith("json") else text
        text = text.strip()

    brace, bracket = text.find("{"), text.find("[")
    if brace != -1 and (bracket == -1 or brace < bracket):
        end = text.rfind("}")
        if end != -1:
            obj = json.loads(text[brace:end + 1])
            if isinstance(obj, dict) and isinstance(obj.get("results"), list):
                return obj["results"]

    start, end = bracket, text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON results in response")
    return json.loads(text[start:end + 1])


def rescore(jobs: list[Job], profile: str, cfg: dict) -> list[Job]:
    """Attach llm_* fields. Silently no-ops (leaving rule scores) if unavailable."""
    lc = cfg.get("llm", {}) or {}
    if not lc.get("enabled", True) or not jobs:
        return jobs
    client = _client()
    if client is None:
        log.warning("ANTHROPIC_API_KEY not set - falling back to rule scores only")
        return jobs

    model = lc.get("model", "claude-sonnet-5")
    batch_size = int(lc.get("batch_size", 8))
    jd_chars = int(lc.get("jd_chars", 3500))
    # Adaptive thinking is on by default on current models and its tokens come
    # out of max_tokens, so this has to be generous even though the JSON is small.
    max_tokens = int(lc.get("max_tokens", 16000))
    effort = lc.get("effort", "low")
    structured = bool(lc.get("structured", True))

    extra: dict = {}
    if structured or effort:
        oc: dict = {}
        if structured:
            oc["format"] = {"type": "json_schema", "schema": RESULT_SCHEMA}
        if effort:
            oc["effort"] = effort
        extra["output_config"] = oc

    for start in range(0, len(jobs), batch_size):
        batch = jobs[start:start + batch_size]
        prompt = (
            "CANDIDATE PROFILE\n-----------------\n" + profile.strip() +
            "\n\nJOB POSTINGS (JSON)\n-------------------\n" + _payload(batch, jd_chars)
        )
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                **extra,
            )
            if resp.stop_reason == "max_tokens":
                log.warning("LLM batch %d hit max_tokens (%d) - raise llm.max_tokens",
                            start // batch_size, max_tokens)
            results = _parse("".join(b.text for b in resp.content if b.type == "text"))
        except Exception as e:  # noqa: BLE001
            log.warning("LLM batch %d failed (%s); keeping rule scores", start // batch_size, e)
            continue

        by_index = {int(r.get("i", -1)): r for r in results if isinstance(r, dict)}
        for k, job in enumerate(batch):
            r = by_index.get(k)
            if not r:
                continue
            try:
                job.llm_score = max(0, min(100, int(r.get("score", job.rule_score))))
            except (TypeError, ValueError):
                continue
            job.llm_verdict = str(r.get("verdict", ""))[:20]
            job.llm_reason = str(r.get("reason", ""))[:160]
            job.llm_match = str(r.get("match", ""))[:120]
            job.llm_gap = str(r.get("gap", ""))[:120]
            job.sponsorship_risk = str(r.get("sponsorship", ""))[:20]
        log.info("LLM scored %d/%d", min(start + batch_size, len(jobs)), len(jobs))
    return jobs
