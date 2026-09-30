"""Exercise the Claude stage without an API key.

The LLM path cannot run in CI (or on a machine with no key), so it is the one
stage that ships untested. These tests stub the client and check the parts that
would silently break on first real contact: request shape, response parsing,
field mapping, batch index alignment, and failure fallback.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobscan import config as cfgmod, llm  # noqa: E402
from jobscan.models import Job  # noqa: E402


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


class StubMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Resp(reply)


class StubClient:
    def __init__(self, replies):
        self.messages = StubMessages(replies)


def _jobs(n):
    return [Job(source="workday", company="Co%d" % i, title="Data Analyst %d" % i,
                url="https://x/%d" % i, location="Boston, MA",
                description="Python SQL regression " * 40)
            for i in range(n)]


def _cfg():
    c = cfgmod.load(ROOT / "config.yaml")
    return c


def test_happy_path():
    jobs = _jobs(3)
    for j in jobs:
        j.rule_score = 50
    payload = json.dumps([
        {"i": 0, "score": 88, "verdict": "strong", "match": "factor modelling",
         "gap": "no SQL in production", "sponsorship": "likely_ok", "reason": "方向高度吻合"},
        {"i": 1, "score": 42, "verdict": "marginal", "match": "python",
         "gap": "wants 5 yrs", "sponsorship": "unclear", "reason": "资历不够"},
        {"i": 2, "score": 12, "verdict": "skip", "match": "none",
         "gap": "unrelated domain", "sponsorship": "likely_blocked", "reason": "领域不符"},
    ], ensure_ascii=False)
    client = StubClient([payload])
    llm._client = lambda: client  # noqa: SLF001

    out = llm.rescore(jobs, "PROFILE TEXT", _cfg())
    assert [j.llm_score for j in out] == [88, 42, 12], [j.llm_score for j in out]
    assert out[0].llm_verdict == "strong"
    assert out[0].sponsorship_risk == "likely_ok"
    assert out[2].llm_reason == "领域不符"
    # final_score must prefer the LLM verdict over the rule score
    assert out[0].final_score == 88 and out[2].final_score == 12
    print("  OK  happy path: scores, verdicts, reasons, final_score override")


def test_structured_output_shape():
    """The API-enforced schema returns {"results": [...]}, not a bare array."""
    jobs = _jobs(2)
    payload = json.dumps({"results": [
        {"i": 0, "score": 77, "verdict": "good", "match": "factor work",
         "gap": "no SQL", "sponsorship": "likely_ok", "reason": "契合"},
        {"i": 1, "score": 21, "verdict": "skip", "match": "none",
         "gap": "wrong domain", "sponsorship": "unclear", "reason": "不匹配"},
    ]}, ensure_ascii=False)
    llm._client = lambda: StubClient([payload])  # noqa: SLF001
    out = llm.rescore(jobs, "P", _cfg())
    assert [j.llm_score for j in out] == [77, 21], [j.llm_score for j in out]
    assert out[0].llm_reason == "契合"
    print("  OK  parses the schema's {\"results\": [...]} envelope")


def test_truncated_response_is_survivable():
    """max_tokens truncation must warn and fall back, never crash."""
    jobs = _jobs(2)
    for j in jobs:
        j.rule_score = 51
    truncated = '{"results": [{"i": 0, "score": 80, "verdi'
    llm._client = lambda: StubClient([_Resp(truncated, "max_tokens")])  # noqa: SLF001

    class _C:
        def __init__(self, r):
            self.messages = StubMessages([r])
    llm._client = lambda: _C(truncated)  # noqa: SLF001
    out = llm.rescore(jobs, "P", _cfg())
    assert all(j.final_score == 51 for j in out), [j.final_score for j in out]
    print("  OK  truncated JSON: warns, keeps rule scores, no crash")


def test_request_shape():
    """Catch an SDK signature drift before it costs a real API call."""
    jobs = _jobs(2)
    client = StubClient([json.dumps([{"i": 0, "score": 60}, {"i": 1, "score": 60}])])
    llm._client = lambda: client  # noqa: SLF001
    llm.rescore(jobs, "PROFILE", _cfg())

    kw = client.messages.calls[0]
    assert set(kw) >= {"model", "max_tokens", "system", "messages"}, kw.keys()
    assert kw["model"], "model id must not be empty"
    assert kw["max_tokens"] >= 8000, \
        "max_tokens must leave room for adaptive thinking, got %s" % kw["max_tokens"]
    oc = kw.get("output_config") or {}
    assert oc.get("format", {}).get("type") == "json_schema", \
        "structured output must be requested, got %s" % oc
    assert kw["messages"][0]["role"] == "user"
    body = kw["messages"][0]["content"]
    assert "PROFILE" in body, "candidate profile must reach the prompt"
    assert "Data Analyst 0" in body, "job titles must reach the prompt"
    # JD text must be truncated, not sent whole
    jd_chars = int(_cfg().get_path("llm.jd_chars", 3500))
    sent = json.loads(body.split("-------------------\n", 1)[1])
    assert all(len(x["jd"]) <= jd_chars for x in sent), "jd_chars cap not applied"
    print("  OK  request shape: model/system/messages, profile+titles present, JD capped")


def test_markdown_fenced_reply():
    jobs = _jobs(1)
    fenced = "```json\n" + json.dumps([{"i": 0, "score": 71, "verdict": "good"}]) + "\n```"
    llm._client = lambda: StubClient([fenced])  # noqa: SLF001
    out = llm.rescore(jobs, "P", _cfg())
    assert out[0].llm_score == 71, out[0].llm_score
    print("  OK  parses ```json fenced replies")


def test_prose_wrapped_reply():
    jobs = _jobs(1)
    chatty = 'Here you go:\n[{"i": 0, "score": 65}]\nHope that helps!'
    llm._client = lambda: StubClient([chatty])  # noqa: SLF001
    out = llm.rescore(jobs, "P", _cfg())
    assert out[0].llm_score == 65
    print("  OK  recovers JSON from prose-wrapped replies")


def test_batching_and_index_alignment():
    """8 jobs at batch_size 3 must map back to the right jobs, not shift."""
    jobs = _jobs(8)
    cfg = _cfg()
    cfg["llm"] = dict(cfg["llm"])
    cfg["llm"]["batch_size"] = 3
    replies = [
        json.dumps([{"i": 0, "score": 10}, {"i": 1, "score": 11}, {"i": 2, "score": 12}]),
        json.dumps([{"i": 0, "score": 20}, {"i": 1, "score": 21}, {"i": 2, "score": 22}]),
        json.dumps([{"i": 0, "score": 30}, {"i": 1, "score": 31}]),
    ]
    llm._client = lambda: StubClient(replies)  # noqa: SLF001
    out = llm.rescore(jobs, "P", cfg)
    assert [j.llm_score for j in out] == [10, 11, 12, 20, 21, 22, 30, 31], \
        [j.llm_score for j in out]
    print("  OK  batching: 8 jobs / size 3 -> 3 calls, indices aligned")


def test_api_failure_falls_back():
    jobs = _jobs(2)
    for j in jobs:
        j.rule_score = 55
    llm._client = lambda: StubClient([RuntimeError("529 overloaded")])  # noqa: SLF001
    out = llm.rescore(jobs, "P", _cfg())
    assert all(j.llm_score is None for j in out), "must not invent scores on failure"
    assert all(j.final_score == 55 for j in out), "must fall back to rule score"
    print("  OK  API failure: keeps rule scores, does not crash")


def test_malformed_reply_falls_back():
    jobs = _jobs(2)
    for j in jobs:
        j.rule_score = 44
    llm._client = lambda: StubClient(["not json at all"])  # noqa: SLF001
    out = llm.rescore(jobs, "P", _cfg())
    assert all(j.final_score == 44 for j in out)
    print("  OK  malformed reply: falls back cleanly")


def test_no_key_is_a_noop():
    jobs = _jobs(2)
    for j in jobs:
        j.rule_score = 33
    llm._client = lambda: None  # noqa: SLF001
    out = llm.rescore(jobs, "P", _cfg())
    assert all(j.final_score == 33 for j in out)
    print("  OK  missing key: no-op, rule scores preserved")


def test_score_clamped():
    jobs = _jobs(2)
    llm._client = lambda: StubClient(  # noqa: SLF001
        [json.dumps([{"i": 0, "score": 999}, {"i": 1, "score": -50}])])
    out = llm.rescore(jobs, "P", _cfg())
    assert out[0].llm_score == 100 and out[1].llm_score == 0
    print("  OK  out-of-range scores clamped to 0-100")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print("  FAIL %s: %s" % (t.__name__, e))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("  ERROR %s: %r" % (t.__name__, e))
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    raise SystemExit(1 if failed else 0)
