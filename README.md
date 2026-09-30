# jobscan

Daily job matcher. Pulls postings from public ATS APIs, filters them against a
profile you write, re-scores the survivors with Claude, and writes an HTML report.

It exists because job boards optimise for volume and you need the opposite: on a
real run, 4,900 collected postings reduced to roughly 70 worth reading.

Windows + Python 3.10+. Built and tested on Python 3.12.

---

## Before you use this

**Data sources.** Greenhouse, Lever and Workday are queried through the same
public JSON endpoints their own careers pages call. The optional
`python-jobspy` source scrapes Indeed and LinkedIn instead, and those sites'
terms of service restrict automated access. That source is **off unless you
install `python-jobspy` and enable it in `config.yaml`**, and if you do, you are
responsible for complying with the terms of the sites you query. Everything else
works without it.

**Be polite.** The defaults page slowly and cap request volume. Do not raise
`max_pages` or `detail_limit` far beyond the shipped values.

**Your data stays local.** Postings, scores and your application history live in
a SQLite file on your machine. The only thing that leaves is the job description
text and your `profile.md`, sent to the Anthropic API for scoring. Nothing is
uploaded anywhere else.

**No warranty.** Scores are a triage aid, not a hiring prediction. Read the job
description before you apply.

---

## Setup (once)

### 1. Install dependencies

```
pip install -r requirements.txt
```

Optional — adds Indeed and LinkedIn as sources:

```
pip install python-jobspy
```

### 2. Write your profile

Copy `profile.template.md` to `profile.md` and rewrite it for yourself. This is
the single most important file: it is what Claude scores every posting against.

**Include your real gaps.** A profile that only lists strengths produces a report
full of jobs you will not get.

### 3. Set your Anthropic API key

Get one at https://console.anthropic.com/settings/keys

```
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY","sk-ant-your-key-here","User")
```

Open a **new** terminal afterwards — the variable is not visible in the one you
typed it in.

Alternative: copy `.env.example` to `.env` and put the key there. `run.ps1` loads
it, and `.env` is gitignored.

Without a key everything still runs; it just stops after the keyword-rule stage
and says so in the report header.

### 4. Check it works

```
python -m jobscan verify
```

This probes every configured source and tells you which respond. Expect a few
failures — ATS tokens go stale. Fix or delete those entries in `config.yaml`.

Then a real API call for a few cents, which confirms your key and the model id:

```
python tests\smoke_llm_live.py
```

---

## Daily use

```
python -m jobscan run            # fetch, score, report, open in browser
python -m jobscan run --no-llm   # rules only, zero API cost
python -m jobscan report         # re-render from the database, no fetching
```

A full run takes roughly 8-10 minutes, mostly Workday paging. Reports land in
`reports/YYYY-MM-DD.html`; a cumulative tracker goes to `reports/jobs.csv`.

### Marking what you have applied to

Each posting in the report has a checkbox. Ticking it greys the card out and the
tick persists in your browser. To also tell the database (so the posting stops
appearing in the backlog), copy the command from the bar at the bottom of the
page and run it:

```
python -m jobscan applied a1b2c3d4 e5f6a7b8
python -m jobscan skip a1b2c3d4
python -m jobscan list --min-score 65
```

The browser cannot write to a local SQLite file, which is why this is two steps.

### Running it daily without thinking about it

```
.\register_task.ps1                 # every day at 07:30
.\register_task.ps1 -Time 08:15     # custom time
.\register_task.ps1 -Remove         # unregister
```

Runs under your own account and logs to `logs/`. It is skipped on days the
machine is off — Windows does not reliably catch up on missed runs.

---

## Cost

Postings are scored **once, ever** — a posting Claude has already judged is never
re-sent, its verdict is read back from SQLite. Steady-state spend therefore
tracks new postings per day (typically 5-15), not the ~50 that clear the rule gate.

| | first run (~50 postings) | steady state (~12 new/day) |
|---|---|---|
| Sonnet | ~$0.14 | ~$1/month |
| Haiku | ~$0.07 | ~$0.50/month |

Bounded by `llm.min_rule_score` (only postings above it reach the API) and
`llm.max_jobs_per_run` (hard per-run ceiling).

Use `--force-llm` after editing `profile.md` or the prompt — stored verdicts were
produced by the old version and will not refresh on their own.

---

## Configuring it for yourself

Everything personal lives in two files. The Python is generic.

### `profile.md`
Your background. Rewrite completely.

### `config.yaml`

- **`locations`** — the shipped values target the Washington DC metro. Replace
  `preferred` and `acceptable` with your own cities. Location is only a
  tiebreak (+2 to +5); the one hard rule is `block_non_us`, which drops postings
  outside the US because they are a work-authorization dead end. Turn that off
  if you are not US-bound.
- **`scoring.block_employers`** — defense/intel primes whose roles nearly all
  require a clearance. Delete this list if you can hold a clearance.
- **`scoring.deprioritize_employers`** — elite prop/HFT shops. Their postings
  keyword-match beautifully and are unreachable without a PhD or competition
  background, so they get pushed down rather than removed. Adjust to taste.
- **`sources`** — the employer lists. Every entry shipped here was confirmed live
  by `verify`, but boards change. Add your own targets and re-run `verify`.
- **`llm.model`** — swap to a cheaper model if you want.

### `jobscan/rules.py`

The keyword scoring. `TITLE_TIERS`, `TITLE_PENALTY` and `KEYWORDS` are tuned for
entry-level quantitative finance / risk / data roles. If you are targeting
something else, these are the tables to rewrite — they are plain data at the top
of the file.

---

## How scoring works

1. **Collect** — Greenhouse and Lever public JSON APIs, Workday CxS search
   endpoints, optional RSS feeds, optional Indeed/LinkedIn via `python-jobspy`.
2. **Hydrate** — Workday's list endpoint returns no job description, so
   relevant-looking postings get a second request for the full text and real
   locations. Without this, Workday employers are systematically under-scored.
3. **Rules** (`jobscan/rules.py`) — title tiers, keyword weights, years-of-
   experience gate, PhD gate, location, freshness. Hard blockers (citizenship,
   clearance, explicit no-sponsorship, non-US) force the score to 0.
4. **Claude** (`jobscan/llm.py`) — reads `profile.md` and the job description,
   scores 0-100 with a match/gap/reason. Its score overrides the rule score.
   Uses structured outputs so the response is schema-validated JSON.
5. **Report** — HTML + CSV, deduped against a SQLite history in `data/jobs.db`.

## Tests

```
python tests\test_llm_plumbing.py
python tests\test_store_incremental.py
```

The first stubs the Anthropic client and covers request shape, JSON parsing,
batch index alignment, score clamping, and fallback on API failure. The second
pins the incremental behaviour: an already-judged posting is not re-sent, and
re-storing it does not blank the verdict it already has.

## Known limits

- Workday tenant/site slugs are not guessable and do change. If `verify` starts
  failing on one, find the new URL rather than guessing.
- Some employers' new-grad pipelines live on separate campus sites that this does
  not reach. Check those manually.
- `python-jobspy` scrapes rather than using an API. LinkedIn rate-limits it.
  Failures are logged and skipped, never fatal.
- Aggregator results are noisy: Indeed matches loosely, so expect unrelated
  postings to reach the rule stage. That is what the scoring is for.

## License

MIT — see [LICENSE](LICENSE).
