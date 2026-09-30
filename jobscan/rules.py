"""Cheap local scoring. Runs on every posting to cut the LLM's workload down.

Score is 0-100. Anything with a hard blocker is forced to 0 so it never reaches
the report or the API - for an international candidate, a "US citizens only"
posting is not a low-quality match, it is a non-match.
"""
from __future__ import annotations

import re

from .models import Job
from .util import days_old

# --- hard blockers: patterns that make a posting un-applicable, not just weak ---
CITIZENSHIP = [
    r"u\.?s\.?\s+citizen(?:ship)?\s+(?:is\s+)?(?:required|only)",
    r"must\s+be\s+a\s+u\.?s\.?\s+citizen",
    r"active\s+(?:ts/sci|top\s+secret|secret)\s+clearance",
    r"security\s+clearance\s+(?:is\s+)?required",
    r"ability\s+to\s+obtain\s+.{0,30}clearance",
]
NO_SPONSOR = [
    r"(?:will|can)\s+not\s+(?:be\s+able\s+to\s+)?sponsor",
    r"(?:unable|not\s+able)\s+to\s+(?:provide|offer)\s+.{0,20}sponsorship",
    r"no\s+(?:visa\s+)?sponsorship\s+(?:is\s+)?(?:available|provided|offered)",
    r"without\s+the\s+need\s+for\s+.{0,30}sponsorship",
    r"does\s+not\s+(?:provide|offer)\s+.{0,20}sponsorship",
    r"not\s+eligible\s+for\s+.{0,20}sponsorship",
]
# Positive sponsorship signal - worth surfacing even though it is rare in JD text.
WILL_SPONSOR = [r"will\s+sponsor", r"sponsorship\s+(?:is\s+)?available", r"visa\s+sponsorship\s+provided"]

# --- title tiers ---
TITLE_TIERS: list[tuple[int, list[str]]] = [
    (38, [r"quantitative\s+(?:analyst|analytics|strateg)", r"quantitative\s+research\s+(?:analyst|associate)",
          r"factor\s+research", r"portfolio\s+analyst", r"investment\s+analytics",
          r"quantitative\s+risk", r"model\s+(?:risk|validation|development)"]),
    (30, [r"data\s+scientist", r"data\s+analyst", r"risk\s+analyst", r"credit\s+risk",
          r"equity\s+research", r"research\s+associate", r"financial\s+analyst",
          r"analytics\s+(?:analyst|associate|consultant)", r"machine\s+learning\s+engineer"]),
    (20, [r"business\s+analyst", r"strategy\s+analyst", r"investment\s+(?:analyst|associate)",
          r"pricing\s+analyst", r"treasury\s+analyst", r"ai\s+engineer", r"applied\s+scientist"]),
    (10, [r"associate\b", r"\banalyst\b", r"consultant"]),
]
# Seniority / specialization that rules you out today.
TITLE_PENALTY: list[tuple[int, str]] = [
    (-60, r"\b(?:senior|sr\.?|staff|principal|lead|director|head\s+of|vp|avp|svp|evp|vice\s+president|manager|mgr|executive)\b"),
    (-60, r"\b(?:iii|iv|v)\b"),
    (-25, r"\bii\b"),
    (-70, r"\bintern(?:ship)?\b"),          # you want full-time; flip in config if not
    (-45, r"c\+\+|low[- ]latency|hft|firmware|embedded"),
    (-40, r"\b(?:sales|recruit|marketing|teller|branch|customer\s+service|nurse|driver)\b"),
    # "Quantitative Researcher" (as opposed to Quantitative *Analyst*) is the
    # buy-side PhD-track title. Reachable only with a maths/CS doctorate.
    (-45, r"quantitative\s+researcher|\bquant\s+researcher\b|research\s+scientist"),
    (-30, r"\bportfolio\s+manager\b|\btrader\b|\bstructurer\b"),
    # Domain mismatches that still hit the generic analytics keywords.
    (-50, r"epidemiolog|clinical|biostat|genomic|pharmacovigilance|health\s+econom|"
          r"actuar|geospatial|supply\s+chain|hr\s+analytic|people\s+analytic|"
          r"marketing\s+analytic|revenue\s+cycle"),
]

# Clearance signalled in the title alone - Guidehouse/consulting postings do this
# constantly and the JD body sometimes never spells it out.
CLEARED_TITLE = re.compile(
    r"\bcleared\b|\bts/sci\b|\btop\s+secret\b|\bsecret\s+clearance\b|\bpolygraph\b|"
    r"\bpublic\s+trust\b|\bfedramp\b|\bmilitary\b|\bintelligence\s+community\b|"
    r"\bweapons?\b|\bordnance\b|\bwarfare\b|\bdefense\b|\bdod\b|\bwarfighter\b|"
    r"\bjourneyman\b|\bcombatant\b|\bmunitions?\b", re.I)

# STEM-doctorate signals in the JD body. Individually weak, jointly decisive.
PHD_REQUIRED = re.compile(
    r"ph\.?d\.?\s*(?:degree\s*)?(?:is\s+)?(?:required|mandatory)|"
    r"(?:requires?|must\s+have)\s+(?:an?\s+)?ph\.?d|doctorate\s+required", re.I)
PHD_PREFERRED = re.compile(r"ph\.?d\.?\s*(?:degree)?\s*(?:strongly\s+)?preferred|ph\.?d\.?\s+or\s+equivalent", re.I)
ELITE_STEM = re.compile(
    r"(?:advanced|graduate)\s+degree\s+in\s+(?:mathematics|physics|computer\s+science|"
    r"electrical\s+engineering|statistics|operations\s+research)", re.I)

# --- JD keyword weights ---
KEYWORDS: list[tuple[int, str]] = [
    (8, r"\bfactor\s+(?:model|research|investing)"),
    (8, r"\bbacktest"),
    (7, r"\bpython\b"),
    (7, r"\bsql\b"),
    (6, r"\bportfolio\s+(?:construction|management|optimi)"),
    (6, r"\bmodel\s+validation\b"),
    (6, r"\bmachine\s+learning\b"),
    (5, r"\bderivativ|\boptions?\s+pricing|\bstructured\s+product"),
    (5, r"\bregression\b|\beconometric"),
    (5, r"\bfrm\b|\bcfa\b"),
    (5, r"\bllm\b|\blarge\s+language\s+model|\bgenerative\s+ai\b|\bnlp\b"),
    (4, r"\bpandas\b|\bnumpy\b|\bstatsmodels\b|\bpytorch\b"),
    (4, r"\btime\s+series\b"),
    (4, r"\bequit(?:y|ies)\b|\bfixed\s+income\b"),
    (4, r"\bmaster'?s?\s+degree\b|\bms\b\s+in|\bm\.s\.\b"),
    (3, r"\bvar\b|\bstress\s+test|\bcapital\s+market"),
    (3, r"\bbloomberg\b|\bfactset\b|\bwind\b|\brefinitiv\b"),
    (3, r"\bmatlab\b|\bstata\b|\br\b\s+programming"),
    (-8, r"\b(?:8|9|10|12|15)\+?\s*years"),
    (-5, r"\bc\+\+\b"),
]
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*years?\s+(?:of\s+)?(?:relevant\s+|professional\s+|industry\s+)?experience", re.I)


def _search_any(patterns: list[str], text: str) -> str | None:
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(0)
    return None


# Non-US postings are dead ends for someone who needs US work authorization.
# A blacklist of foreign cities leaks badly (Hangzhou, Ahmedabad, ...), so the
# real test is a US *whitelist*; this list only settles the ambiguous cases
# first, e.g. "Ontario, CA" which would otherwise match California.
NON_US = re.compile(
    r"\b(?:canada|ontario|toronto|vancouver|montreal|quebec|"
    r"united\s+kingdom|england|scotland|ireland|dublin|"
    r"india|china|japan|singapore|hong\s+kong|taiwan|korea|"
    r"australia|new\s+zealand|germany|france|spain|italy|netherlands|"
    r"switzerland|sweden|poland|romania|portugal|belgium|"
    r"mexico|brazil|argentina|chile|colombia|"
    r"philippines|malaysia|indonesia|vietnam|thailand|"
    r"israel|uae|dubai|saudi|egypt|south\s+africa|nigeria|turkey|"
    # Global finance hubs, named without a country often enough to matter.
    r"london|dublin|edinburgh|frankfurt|paris|amsterdam|zurich|geneva|luxembourg|"
    r"milan|madrid|stockholm|warsaw|bucharest|lisbon|brussels|"
    r"mumbai|bengaluru|bangalore|hyderabad|chennai|pune|gurgaon|noida|ahmedabad|"
    r"shanghai|beijing|shenzhen|hangzhou|guangzhou|chengdu|"
    r"tokyo|osaka|seoul|taipei|sydney|melbourne|auckland|"
    r"toronto|calgary|ottawa|mexico\s+city|sao\s+paulo|bogota|"
    r"manila|jakarta|bangkok|kuala\s+lumpur|tel\s+aviv|cairo|johannesburg)\b", re.I)

# Two-letter USPS codes, full state names, and the obvious country markers.
# Matched against the location string only, never the JD body.
US_SIGNAL = re.compile(
    # Bare "US" matters: aggregators emit "Remote, US" and plain "US" constantly,
    # and without it those read as "no US marker found" and get blocked.
    r"\b(?:US|USA|U\.S\.A?\.?|United\s+States|"
    r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|"
    r"MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC|PR|"
    r"Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|"
    r"Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|"
    r"Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|"
    r"Nevada|New\s+Hampshire|New\s+Jersey|New\s+Mexico|New\s+York|North\s+Carolina|"
    r"North\s+Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode\s+Island|South\s+Carolina|"
    r"South\s+Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West\s+Virginia|"
    r"Wisconsin|Wyoming)\b", re.I)

# Workday collapses multi-site postings to "5 Locations" - unscoreable, not bad.
VAGUE_LOC = re.compile(r"^\s*\d+\s+locations?\s*$", re.I)


DEFAULT_LOC_POINTS = {
    "preferred": 5,     # DC metro - a mild tiebreak, not a gate
    "acceptable": 3,
    "us_other": 2,      # anywhere else in the US is fine
    "remote_us": 3,
    "non_us": -45,      # a work-authorization dead end, not a preference
    "unconfirmed": -35, # no US marker at all - assume foreign
    "unknown": 0,       # nothing to go on (Workday's "5 Locations")
}


def _location_score(job: Job, cfg: dict) -> tuple[int, str]:
    pts = dict(DEFAULT_LOC_POINTS)
    pts.update(cfg.get("points") or {})
    # Workday's detail endpoint reports the country outright - trust it over any
    # amount of string matching on the location.
    country = str(job.extra.get("country") or "").strip()
    if country and not re.search(r"united\s+states|^usa?$", country, re.I):
        return pts["non_us"], "non-US (%s)" % country[:24]

    loc = (job.location or "").strip()
    if not loc or VAGUE_LOC.match(loc):
        return pts["unknown"], ""
    low = loc.lower()
    # A US target city anywhere in a multi-location string is a hit, even if the
    # string also lists foreign offices.
    for pref in cfg.get("preferred", []):
        if pref.lower() in low:
            return pts["preferred"], "preferred loc"
    # Explicit foreign markers first - "Ontario, CA" must not read as California,
    # and "Remote - India" must not match the acceptable list's "Remote".
    if NON_US.search(low):
        return pts["non_us"], "non-US location"
    for ok in cfg.get("acceptable", []):
        if ok.lower() in low:
            return pts["acceptable"], "acceptable loc"
    if US_SIGNAL.search(loc):
        if re.search(r"\bremote\b", low):
            return pts["remote_us"], "remote (US)"
        if cfg.get("strict", False):
            return -30, "outside target geography"
        return pts["us_other"], "elsewhere in US"
    # No state, no country marker, no known city. Hangzhou and Ahmedabad land
    # here - a city blacklist can never be complete, so absence of a US marker
    # is the test that actually holds.
    return pts["unconfirmed"], "location not confirmed US"


def score(job: Job, cfg: dict) -> Job:
    """Populate rule_score / rule_notes / blockers on the job, in place."""
    sc = cfg.get("scoring", {}) or {}
    title = (job.title or "").lower()
    body = (job.description or "")
    haystack = title + "\n" + body.lower()
    notes: list[str] = []
    blockers: list[str] = []
    total = 0

    # 1. hard blockers
    if sc.get("block_citizenship_required", True):
        hit = _search_any(CITIZENSHIP, haystack)
        if hit:
            blockers.append("citizenship/clearance: %r" % hit[:60])
        elif CLEARED_TITLE.search(title):
            blockers.append("clearance implied by title")
        else:
            # Defense/intel primes: nearly every seat needs a clearance, and the
            # aggregator JD text often never says so.
            co_low = (job.company or "").lower()
            for name in (sc.get("block_employers") or []):
                if str(name).lower() in co_low:
                    blockers.append("cleared-work employer (%s)" % name)
                    break
    if sc.get("block_no_sponsorship", True):
        hit = _search_any(NO_SPONSOR, haystack)
        if hit:
            blockers.append("explicit no-sponsorship: %r" % hit[:60])
    if _search_any(WILL_SPONSOR, haystack):
        total += 10
        notes.append("+10 mentions sponsorship available")

    # 2. title tier (best match wins, tiers are not cumulative)
    for pts, pats in TITLE_TIERS:
        if _search_any(pats, title):
            total += pts
            notes.append("+%d title tier" % pts)
            break
    else:
        notes.append("+0 title not in target families")

    for pts, pat in TITLE_PENALTY:
        if re.search(pat, title, re.I):
            total += pts
            notes.append("%d title: %s" % (pts, pat[:34]))

    # 3. years-of-experience gate
    yrs = [int(m) for m in YEARS.findall(body)]
    if yrs:
        lo = min(yrs)
        if lo >= 7:
            total -= 40
            notes.append("-40 wants %d+ yrs" % lo)
        elif lo >= 5:
            total -= 22
            notes.append("-22 wants %d+ yrs" % lo)
        elif lo >= 3:
            total -= 8
            notes.append("-8 wants %d+ yrs" % lo)
        else:
            total += 6
            notes.append("+6 entry level (%d yrs)" % lo)

    # 3b. doctorate gate - a finance master's does not clear these
    if PHD_REQUIRED.search(body):
        total -= 45
        notes.append("-45 PhD required")
    elif PHD_PREFERRED.search(body):
        total -= 18
        notes.append("-18 PhD preferred")
    if ELITE_STEM.search(body):
        total -= 15
        notes.append("-15 wants advanced STEM degree (math/physics/CS)")

    # 3c. employers you have explicitly deprioritised in config
    co = (job.company or "").lower()
    for name in (sc.get("deprioritize_employers") or []):
        if str(name).lower() in co:
            total -= int(sc.get("deprioritize_penalty", 35))
            notes.append("-%d deprioritised employer" % int(sc.get("deprioritize_penalty", 35)))
            break

    # 4. JD keywords, capped so a keyword-stuffed JD cannot dominate
    kw = 0
    hits: list[str] = []
    for pts, pat in KEYWORDS:
        if re.search(pat, haystack, re.I):
            kw += pts
            if pts > 0:
                hits.append(pat.replace("\\b", "").replace("\\s+", " ")[:18])
    kw = max(-25, min(kw, int(sc.get("keyword_cap", 40))))
    total += kw
    if hits:
        notes.append("+%d keywords: %s" % (kw, ", ".join(hits[:8])))

    # 5. location
    loc_cfg = cfg.get("locations", {}) or {}
    lpts, lnote = _location_score(job, loc_cfg)
    total += lpts
    if lnote:
        notes.append("%+d %s" % (lpts, lnote))
    # A score penalty is not enough here: the LLM score overrides the rule score
    # outright, so a strong-on-the-merits foreign posting (Morningstar London)
    # would climb straight back to the top. Work authorization is a hard gate,
    # so it has to block rather than deduct.
    if loc_cfg.get("block_non_us", True) and lnote.startswith("non-US"):
        blockers.append("non-US posting (%s)" % (job.location or "?")[:40])

    # 6. freshness - the whole point is catching postings early
    age = days_old(job.posted_at)
    if age is not None:
        if age <= 2:
            total += 12
            notes.append("+12 posted <2d ago")
        elif age <= 7:
            total += 6
            notes.append("+6 posted <7d ago")
        elif age > int(sc.get("stale_days", 45)):
            total -= 15
            notes.append("-15 stale (%dd)" % age)

    job.rule_notes = notes
    job.blockers = blockers
    job.rule_score = 0 if blockers else max(0, min(100, total))
    return job


def score_all(jobs: list[Job], cfg: dict) -> list[Job]:
    return [score(j, cfg) for j in jobs]
