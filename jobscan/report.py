from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TPL_DIR = Path(__file__).resolve().parent / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TPL_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def render_html(new_jobs: list[dict], carryover: list[dict], meta: dict, out_path: Path) -> Path:
    tpl = _env().get_template("report.html.j2")
    html = tpl.render(new_jobs=new_jobs, carryover=carryover, meta=meta)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


CSV_COLS = ["first_seen", "final_score", "llm_verdict", "company", "title", "location",
            "source", "sponsor_risk", "llm_match", "llm_gap", "llm_reason", "status", "url", "id"]


def write_csv(rows: list[dict], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_COLS})
    return out_path


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d")
