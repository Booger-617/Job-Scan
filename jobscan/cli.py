from __future__ import annotations

import argparse
import logging
import re
import sys
import webbrowser
from pathlib import Path

from . import config as cfgmod
from . import llm, report, rules
from .models import Job
from .sources import REGISTRY, greenhouse, lever, workday, rss, jobspy_src
from .store import Store

ROOT = cfgmod.ROOT
log = logging.getLogger("jobscan")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def _collect(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    for name, fn in REGISTRY.items():
        scfg = (cfg.get("sources", {}) or {}).get(name) or {}
        if not scfg.get("enabled", False):
            log.info("source %-10s disabled", name)
            continue
        log.info("source %-10s fetching...", name)
        try:
            got = fn(scfg)
        except Exception as e:  # noqa: BLE001
            log.error("source %s crashed: %s", name, e)
            continue
        log.info("source %-10s -> %d postings", name, len(got))
        jobs.extend(got)
    return jobs


def _dedupe(jobs: list[Job]) -> list[Job]:
    seen: dict[str, Job] = {}
    for j in jobs:
        if not j.url or not j.title:
            continue
        key = j.id
        # Same posting from two sources: keep the one with the richer JD text.
        if key not in seen or len(j.description) > len(seen[key].description):
            seen[key] = j

    # Employers post one role once per city, which floods the report with
    # near-identical rows. Since location is only a tiebreak, collapse them.
    merged: dict[tuple[str, str], Job] = {}
    for j in seen.values():
        key = (j.company.strip().lower(), re.sub(r"\s+", " ", j.title.strip().lower()))
        keep = merged.get(key)
        if keep is None:
            merged[key] = j
            continue
        keep.extra.setdefault("also_at", []).append(j.location)
        if len(j.description) > len(keep.description):
            j.extra["also_at"] = keep.extra.get("also_at", [])
            j.extra["also_at"].append(keep.location)
            merged[key] = j
    for j in merged.values():
        extra_locs = [x for x in dict.fromkeys(j.extra.get("also_at", [])) if x and x != j.location]
        if extra_locs:
            j.location = "%s (+%d more)" % (j.location, len(extra_locs))
    return list(merged.values())


def cmd_run(args) -> int:
    cfg = cfgmod.load(args.config)
    profile = cfgmod.load_profile(args.profile)
    store = Store(ROOT / cfg.get_path("output.db", "data/jobs.db"))

    raw = _collect(cfg)
    jobs = _dedupe(raw)
    log.info("collected %d postings (%d after dedupe)", len(raw), len(jobs))

    jobs = rules.score_all(jobs, cfg)
    blocked = sum(1 for j in jobs if j.blockers)
    min_rule = int(cfg.get_path("llm.min_rule_score", 35))
    max_llm = int(getattr(args, "max_llm", 0) or
                  cfg.get_path("llm.max_jobs_per_run", 40))

    survivors = [j for j in jobs if not j.blockers and j.rule_score >= min_rule]
    survivors.sort(key=lambda j: j.rule_score, reverse=True)
    log.info("%d blocked (visa/clearance), %d passed rule gate (>=%d)",
             blocked, len(survivors), min_rule)

    # Postings persist for weeks, so most of what survives the rule gate on any
    # given day was already judged on an earlier one. Re-sending it would put
    # ~80% of the API bill into re-deriving answers already in the database.
    already = set() if args.force_llm else store.llm_scored_ids()
    pending = [j for j in survivors if j.id not in already]
    to_llm = pending[:max_llm]
    skipped = len(survivors) - len(pending)

    if args.no_llm:
        log.info("--no-llm: skipping semantic stage")
    elif not to_llm:
        log.info("LLM: nothing new to score (%d already judged)", skipped)
    else:
        log.info("LLM: scoring %d new posting(s); %d already judged, %d over the "
                 "per-run cap", len(to_llm), skipped, max(0, len(pending) - max_llm))
        llm.rescore(to_llm, profile, cfg)
    llm_scored = sum(1 for j in to_llm if j.llm_score is not None)

    known = store.known_ids()
    new_ids = []
    for j in survivors:
        if store.upsert(j):
            new_ids.append(j.id)

    min_report = int(cfg.get_path("output.min_report_score", 55))
    carry_days = int(cfg.get_path("output.carryover_days", 10))

    rows = [dict(r) for r in store.recent(days=carry_days, min_score=min_report, limit=400)]
    new_rows = [r for r in rows if r["id"] in set(new_ids)]
    carry_rows = [r for r in rows
                  if r["id"] not in set(new_ids) and r["status"] == "new"][:40]

    st = store.stats()
    date = report.stamp()
    csv_path = ROOT / cfg.get_path("output.csv_path", "reports/jobs.csv")
    html_path = ROOT / cfg.get_path("output.html_dir", "reports") / ("%s.html" % date)

    meta = {
        "date": date, "fetched": len(raw), "after_rules": len(survivors),
        "llm_scored": llm_scored, "llm_on": llm_scored > 0, "blocked": blocked,
        "total_tracked": st["total"], "carry_days": carry_days,
        "db": str(ROOT / cfg.get_path("output.db", "data/jobs.db")),
        "csv": str(csv_path),
    }

    if cfg.get_path("output.csv", True):
        all_rows = [dict(r) for r in store.recent(days=3650, min_score=0, limit=100000)]
        report.write_csv(all_rows, csv_path)
    report.render_html(new_rows, carry_rows, meta, html_path)

    print("\n%s" % ("=" * 62))
    print("  new today: %d   |   carryover: %d   |   blocked: %d"
          % (len(new_rows), len(carry_rows), blocked))
    print("  report: %s" % html_path)
    print("%s\n" % ("=" * 62))
    for r in new_rows[:12]:
        print("  %3d  %-34.34s %-40.40s" % (r["final_score"], r["company"], r["title"]))

    if cfg.get_path("output.open_browser", True) and not args.no_open:
        webbrowser.open(html_path.as_uri())
    store.close()
    return 0


def cmd_report(args) -> int:
    """Re-render the HTML from the store - no fetching, no API calls.

    Use it after marking postings applied, or to reopen today's report.
    """
    cfg = cfgmod.load(args.config)
    store = Store(ROOT / cfg.get_path("output.db", "data/jobs.db"))
    min_report = int(cfg.get_path("output.min_report_score", 48))
    carry_days = int(cfg.get_path("output.carryover_days", 10))

    rows = [dict(r) for r in store.recent(days=carry_days, min_score=min_report, limit=400)]
    date = report.stamp()
    new_rows = [r for r in rows if (r["first_seen"] or "")[:10] == date]
    carry_rows = [r for r in rows
                  if (r["first_seen"] or "")[:10] != date and r["status"] == "new"][:40]

    st = store.stats()
    csv_path = ROOT / cfg.get_path("output.csv_path", "reports/jobs.csv")
    html_path = ROOT / cfg.get_path("output.html_dir", "reports") / ("%s.html" % date)
    meta = {
        "date": date, "fetched": 0, "after_rules": len(rows),
        "llm_scored": sum(1 for r in rows if r.get("llm_score") is not None),
        "llm_on": True, "blocked": 0, "total_tracked": st["total"],
        "carry_days": carry_days,
        "db": str(ROOT / cfg.get_path("output.db", "data/jobs.db")),
        "csv": str(csv_path),
    }
    report.render_html(new_rows, carry_rows, meta, html_path)
    print("regenerated %s" % html_path)
    print("  %d shown  |  %d new today  |  %d carryover  |  %d applied so far"
          % (len(rows), len(new_rows), len(carry_rows), st["applied"]))
    if not args.no_open:
        webbrowser.open(html_path.as_uri())
    store.close()
    return 0


def cmd_verify(args) -> int:
    """Probe every configured source so broken tokens/URLs surface loudly."""
    cfg = cfgmod.load(args.config)
    src = cfg.get("sources", {}) or {}
    bad: list[str] = []

    def line(ok: bool, label: str, msg: str) -> None:
        print("  %s  %-40.40s %s" % ("OK  " if ok else "FAIL", label, msg))
        if not ok:
            bad.append(label)

    print("\n-- greenhouse --")
    for e in (src.get("greenhouse", {}) or {}).get("boards", []):
        tok = e["token"] if isinstance(e, dict) else e
        line(*( (lambda r: (r[0], "greenhouse:" + tok, r[1]))(greenhouse.probe(tok)) ))

    print("-- lever --")
    for e in (src.get("lever", {}) or {}).get("companies", []):
        slug = e["slug"] if isinstance(e, dict) else e
        ok, msg = lever.probe(slug)
        line(ok, "lever:" + slug, msg)

    print("-- workday --")
    for t in (src.get("workday", {}) or {}).get("tenants", []):
        ok, msg = workday.probe(t.get("url", ""))
        line(ok, "workday:" + t.get("name", "?"), msg)

    print("-- rss --")
    for f in (src.get("rss", {}) or {}).get("feeds", []):
        url = f["url"] if isinstance(f, dict) else f
        ok, msg = rss.probe(url)
        line(ok, "rss:" + url[:34], msg)

    print("-- jobspy --")
    ok, msg = jobspy_src.probe(src.get("jobspy", {}))
    line(ok, "jobspy", msg)

    import os
    print("-- llm --")
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    line(has_key, "ANTHROPIC_API_KEY",
         "set" if has_key else "not set - LLM stage will be skipped")

    print("\n%d failing entr%s. Remove or fix them in config.yaml.\n"
          % (len(bad), "y" if len(bad) == 1 else "ies"))
    return 0


def cmd_mark(args, status: str) -> int:
    cfg = cfgmod.load(args.config)
    store = Store(ROOT / cfg.get_path("output.db", "data/jobs.db"))
    ids = args.job_id if isinstance(args.job_id, list) else [args.job_id]
    note = getattr(args, "note", "") or ""
    total, missing = 0, []
    for jid in ids:
        n = store.set_status(jid, status, note)
        total += n
        if not n:
            missing.append(jid)
    print("marked %d posting(s) as %s" % (total, status))
    if missing:
        print("no match for: %s" % ", ".join(missing))
    store.close()
    return 0 if total else 1


def cmd_list(args) -> int:
    cfg = cfgmod.load(args.config)
    store = Store(ROOT / cfg.get_path("output.db", "data/jobs.db"))
    rows = store.recent(days=args.days, min_score=args.min_score, limit=args.limit)
    print("%-9s %-5s %-8s %-26.26s %-44.44s %s"
          % ("id", "score", "status", "company", "title", "location"))
    for r in rows:
        print("%-9s %-5d %-8s %-26.26s %-44.44s %s"
              % (r["id"][:8], r["final_score"], r["status"], r["company"], r["title"],
                 (r["location"] or "")[:24]))
    print("\n%d rows. stats: %s" % (len(rows), store.stats()))
    store.close()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="jobscan", description="Daily job matcher")
    p.add_argument("--config", default=None)
    p.add_argument("--profile", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="fetch, score, and write today's report")
    r.add_argument("--no-llm", action="store_true", help="rules only, no API calls")
    r.add_argument("--no-open", action="store_true", help="do not open the browser")
    r.add_argument("--max-llm", type=int, default=0,
                   help="override llm.max_jobs_per_run for this run "
                        "(e.g. to clear a backlog in one go)")
    r.add_argument("--force-llm", action="store_true",
                   help="re-score everything, including postings already judged "
                        "(use after editing profile.md or the prompt)")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("verify", help="probe every configured source")
    v.set_defaults(func=cmd_verify)

    rp = sub.add_parser("report", help="re-render the HTML from the store, no fetching")
    rp.add_argument("--no-open", action="store_true", help="do not open the browser")
    rp.set_defaults(func=cmd_report)

    ls = sub.add_parser("list", help="list tracked postings")
    ls.add_argument("--days", type=int, default=14)
    ls.add_argument("--min-score", type=int, default=55)
    ls.add_argument("--limit", type=int, default=60)
    ls.set_defaults(func=cmd_list)

    for name, status in (("applied", "applied"), ("skip", "skipped"), ("star", "starred")):
        m = sub.add_parser(name, help="mark one or more postings as %s" % status)
        m.add_argument("job_id", nargs="+", help="one or more ids (first 8 chars is enough)")
        m.add_argument("--note", default="")
        m.set_defaults(func=lambda a, s=status: cmd_mark(a, s))

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
