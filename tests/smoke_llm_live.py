"""One real API call, two jobs. Run this before any full run after changing
the key, the model id, or the prompt - it costs a fraction of a cent and tells
you whether the Claude stage works at all.

    python tests/smoke_llm_live.py
"""
from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobscan import config as cfgmod, llm  # noqa: E402
from jobscan.models import Job  # noqa: E402

GOOD = (
    "Build and validate quantitative models for mortgage credit risk. Requires "
    "Python, SQL, regression analysis, and a master's degree in finance, statistics "
    "or economics. 0-2 years experience. Familiarity with CAPM, factor models and "
    "time series analysis preferred. Visa sponsorship available."
)
BAD = (
    "Lead data science for our EV charging network. Analyze charging station "
    "utilization, energy pricing, and grid load forecasting. Python, SQL, machine "
    "learning required. Automotive or energy sector background strongly preferred. "
    "8+ years of experience."
)


def main() -> int:
    cfg = cfgmod.load(ROOT / "config.yaml")
    profile = cfgmod.load_profile(ROOT / "profile.md")
    if not profile.strip():
        print("profile.md is empty - the model has nothing to match against")
        return 1

    jobs = [
        Job(source="smoke", company="Freddie Mac",
            title="Quantitative Analytics Professional",
            url="https://example.invalid/1", location="Boston, MA", description=GOOD),
        Job(source="smoke", company="Ford Motor Company",
            title="Data Scientist, Charging and Energy Services",
            url="https://example.invalid/2", location="Dearborn, MI", description=BAD),
    ]
    for j in jobs:
        j.rule_score = 60

    print("model:", cfg.get_path("llm.model"))
    print("calling the API ...\n")
    llm.rescore(jobs, profile, cfg)

    for j in jobs:
        print("%s - %s" % (j.company, j.title))
        print("   score       : %s" % j.llm_score)
        print("   verdict     : %s" % j.llm_verdict)
        print("   match       : %s" % j.llm_match)
        print("   gap         : %s" % j.llm_gap)
        print("   sponsorship : %s" % j.sponsorship_risk)
        print("   reason      : %s" % j.llm_reason)
        print()

    scored = [j for j in jobs if j.llm_score is not None]
    if len(scored) != len(jobs):
        print("SMOKE TEST: FAIL - the API call did not produce scores")
        return 1
    # The whole point of this stage: it must separate a real match from a
    # keyword-stuffed one that the rule engine scored identically.
    if jobs[0].llm_score <= jobs[1].llm_score:
        print("SMOKE TEST: WARN - the finance role did not outrank the EV role "
              "(%s vs %s). The stage works but the prompt may need tuning."
              % (jobs[0].llm_score, jobs[1].llm_score))
        return 0
    print("SMOKE TEST: PASS - finance role %s beat the EV role %s"
          % (jobs[0].llm_score, jobs[1].llm_score))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
