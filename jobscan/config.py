from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


class Config(dict):
    """Thin dict wrapper with dotted lookup so call sites stay readable."""

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def load(path: str | Path | None = None) -> Config:
    p = Path(path) if path else ROOT / "config.yaml"
    if not p.exists():
        raise SystemExit(f"config not found: {p}")
    with open(p, "r", encoding="utf-8") as fh:
        return Config(yaml.safe_load(fh) or {})


def load_profile(path: str | Path | None = None) -> str:
    p = Path(path) if path else ROOT / "profile.md"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")
