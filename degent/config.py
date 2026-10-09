from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "research.toml"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG
    if config_path.suffix == ".json":
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        with config_path.open("rb") as stream:
            config = tomllib.load(stream)
    manifest_path = config_path.parent / "manifest.json"
    if config_path.name == "dashboard-config.json" and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("model_call_cap", "extract_document_cap", "semantic_review_call_cap"):
            if key in manifest.get("extraction_budgets", {}):
                config[key] = manifest["extraction_budgets"][key]
    config["_path"] = str(config_path.resolve())
    return config


def periods(config: dict[str, Any]) -> list[dict[str, str]]:
    return list(config.get("periods", []))


def employers(config: dict[str, Any]) -> list[dict[str, Any]]:
    return list(config.get("employers", []))


def observation_in_period(row: dict, period: dict, time_basis: str = "capture_period") -> bool:
    """Current retrievals cannot populate historical reporting windows."""
    if row.get("study_version") == 1:
        if time_basis not in {"capture_period", "publication_observed_today"}:
            raise ValueError("Choose a separate capture or publication-cohort analysis.")
        if period["id"].startswith("CURRENT-"):
            return period["start"] <= (row.get("capture_date") or "") <= period["end"]
        field = "capture_period_id" if time_basis == "capture_period" else "publication_period_id"
        return row.get(field, "UNKNOWN") == period["id"]
    current = row.get("evidence_basis") == "collector_capture" or row.get("source") in {
        "greenhouse",
        "ashby",
        "capital_one",
        "workday",
        "lever",
        "dom",
        "sitemap",
    }
    current_window = period["id"].startswith("CURRENT-")
    if current != current_window and (current or current_window):
        return False
    return period["start"] <= (row.get("capture_date") or "") <= period["end"]
