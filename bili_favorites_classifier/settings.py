from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import ClassificationRule


APP_DIR_NAME = "BiliFavoritesClassifier"
RULES_FILE_NAME = "custom_rules.json"
METADATA_CACHE_FILE_NAME = "video_metadata_cache.json"


def get_config_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".bili_favorites_classifier"


def get_rules_file_path() -> Path:
    return get_config_dir() / RULES_FILE_NAME


def get_metadata_cache_file_path() -> Path:
    return get_config_dir() / METADATA_CACHE_FILE_NAME


def save_custom_rules(rules: list[ClassificationRule], file_path: Path | None = None) -> Path:
    target = file_path or get_rules_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"rules": [rule.to_dict() for rule in rules]}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_custom_rules(file_path: Path | None = None) -> list[ClassificationRule]:
    target = file_path or get_rules_file_path()
    if not target.exists():
        return []

    payload = json.loads(target.read_text(encoding="utf-8"))
    raw_rules = payload.get("rules", []) if isinstance(payload, dict) else payload
    if not isinstance(raw_rules, list):
        return []

    rules: list[ClassificationRule] = []
    for item in raw_rules:
        rule = _parse_rule(item)
        if rule is not None:
            rules.append(rule)
    return rules


def load_video_metadata_cache(file_path: Path | None = None) -> dict[str, dict[str, Any]]:
    target = file_path or get_metadata_cache_file_path()
    if not target.exists():
        return {}

    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    videos = payload.get("videos", payload)
    if not isinstance(videos, dict):
        return {}
    return {str(bvid): value for bvid, value in videos.items() if isinstance(value, dict)}


def save_video_metadata_cache(cache: dict[str, dict[str, Any]], file_path: Path | None = None) -> Path:
    target = file_path or get_metadata_cache_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"videos": cache}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _parse_rule(item: Any) -> ClassificationRule | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name", "")).strip()
    raw_keywords = item.get("keywords", [])
    if isinstance(raw_keywords, str):
        keywords = [keyword.strip() for keyword in raw_keywords.split(",") if keyword.strip()]
    elif isinstance(raw_keywords, list):
        keywords = [str(keyword).strip() for keyword in raw_keywords if str(keyword).strip()]
    else:
        keywords = []
    if not name or not keywords:
        return None
    return ClassificationRule(name=name, keywords=keywords)
