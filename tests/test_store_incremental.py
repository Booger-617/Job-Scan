"""Guard the incremental behaviour that keeps the API bill flat.

Postings persist for weeks. Two things must hold on every re-run:
  1. a posting Claude already judged is not sent again, and
  2. re-storing it does not blank the verdict it already has.
Both were broken before; these tests pin them down.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobscan.models import Job  # noqa: E402
from jobscan.store import Store  # noqa: E402


def _job(**kw):
    base = dict(source="workday", company="Vanguard", title="Model Validation",
                url="https://x/1", location="Malvern, PA",
                description="python sql regression")
    base.update(kw)
    return Job(**base)


def _store():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Store(tmp.name)


def test_llm_fields_survive_a_rescoreless_rerun():
    """Day 2 re-run with no LLM call must not wipe day 1's verdict."""
    st = _store()
    day1 = _job()
    day1.rule_score = 60
    day1.llm_score = 82
    day1.llm_verdict = "strong"
    day1.llm_reason = "方向高度吻合"
    day1.llm_match = "factor modelling"
    day1.llm_gap = "no production SQL"
    day1.sponsorship_risk = "likely_ok"
    assert st.upsert(day1) is True

    # Day 2: same posting, rules only, every llm_* field blank.
    day2 = _job()
    day2.rule_score = 58
    assert st.upsert(day2) is False

    row = st.db.execute("SELECT * FROM jobs WHERE id=?", (day2.id,)).fetchone()
    assert row["llm_score"] == 82, row["llm_score"]
    assert row["llm_verdict"] == "strong", row["llm_verdict"]
    assert row["llm_reason"] == "方向高度吻合", row["llm_reason"]
    assert row["llm_match"] == "factor modelling"
    assert row["llm_gap"] == "no production SQL"
    assert row["sponsor_risk"] == "likely_ok"
    # final_score must stay on the LLM verdict, not fall back to the rule score
    assert row["final_score"] == 82, row["final_score"]
    # rule fields SHOULD refresh
    assert row["rule_score"] == 58
    st.close()
    print("  OK  day-2 re-run preserves llm verdict, reason, and final_score")


def test_new_llm_score_overwrites():
    st = _store()
    a = _job()
    a.llm_score, a.llm_verdict, a.llm_reason = 40, "marginal", "旧理由"
    st.upsert(a)
    b = _job()
    b.rule_score = 50
    b.llm_score, b.llm_verdict, b.llm_reason = 88, "strong", "新理由"
    st.upsert(b)
    row = st.db.execute("SELECT * FROM jobs WHERE id=?", (b.id,)).fetchone()
    assert (row["llm_score"], row["llm_verdict"], row["llm_reason"]) == (88, "strong", "新理由")
    assert row["final_score"] == 88
    st.close()
    print("  OK  a genuine re-score does overwrite the old verdict")


def test_final_score_falls_back_to_rule_when_never_scored():
    st = _store()
    a = _job()
    a.rule_score = 61
    st.upsert(a)
    b = _job()
    b.rule_score = 55
    st.upsert(b)
    row = st.db.execute("SELECT * FROM jobs WHERE id=?", (b.id,)).fetchone()
    assert row["llm_score"] is None
    assert row["final_score"] == 55, row["final_score"]
    st.close()
    print("  OK  never-scored posting keeps final_score == rule_score")


def test_llm_scored_ids_drives_the_skip():
    st = _store()
    scored = _job(url="https://x/scored")
    scored.llm_score = 70
    st.upsert(scored)
    unscored = _job(url="https://x/unscored", title="Data Analyst")
    st.upsert(unscored)

    ids = st.llm_scored_ids()
    assert scored.id in ids and unscored.id not in ids, ids

    # what cmd_run does with it
    survivors = [scored, unscored]
    pending = [j for j in survivors if j.id not in ids]
    assert pending == [unscored]
    st.close()
    print("  OK  llm_scored_ids() excludes judged postings from the next batch")


def test_status_and_notes_never_clobbered():
    st = _store()
    a = _job()
    a.rule_score = 60
    st.upsert(a)
    st.set_status(a.id[:8], "applied", "referred by X")
    b = _job()
    b.rule_score = 62
    st.upsert(b)
    row = st.db.execute("SELECT * FROM jobs WHERE id=?", (b.id,)).fetchone()
    assert row["status"] == "applied", row["status"]
    assert row["notes"] == "referred by X", row["notes"]
    st.close()
    print("  OK  re-run preserves applied status and notes")


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
