"""State schema migrations. Every migration is deterministic and fail-closed."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from forcefocus.constants import DEFAULT_SETTINGS


class MigrationError(RuntimeError):
    pass


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MigrationError(f"unreadable JSON: {path.name}") from exc


def _domain(value: Any) -> str:
    if not isinstance(value, str):
        raise MigrationError("domain values must be strings")
    domain = value.strip().lower()
    if "://" in domain:
        parsed = urlparse(domain)
        domain = parsed.hostname or parsed.path
    else:
        domain = domain.split("/")[0].split("?")[0].split("#")[0]
    domain = domain.split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    domain = re.sub(r"^\*\.?", "", domain).rstrip("*")
    if (
        not domain
        or len(domain) > 253
        or "." not in domain
        or any(char in domain for char in {"\n", "\r", "\t", " ", "\\", "/"})
        or domain[0] in ".-"
        or domain[-1] in ".-"
        or ".." in domain
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", domain)
    ):
        raise MigrationError(f"invalid domain in legacy state: {value!r}")
    return domain


def _domains(values: Any) -> list[str]:
    if not isinstance(values, list):
        raise MigrationError("domain collection must be a list")
    normalized = []
    for value in values:
        domain = _domain(value)
        if domain not in normalized:
            normalized.append(domain)
    return normalized


def _validate_iso(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise MigrationError(f"{field} must be an ISO timestamp")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise MigrationError(f"{field} must be an ISO timestamp") from exc


def _sleep_schedule(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise MigrationError("sleep_schedule.json must be an object")
    allowed = {
        "enabled", "days_of_week", "sleep_time", "wake_time", "mode",
        "blacklist", "whitelist", "suppressed_occurrences", "pending_config",
        "pending_apply_at",
    }
    if set(data) - allowed:
        raise MigrationError("sleep_schedule.json has unknown structure")
    if not isinstance(data.get("enabled", False), bool):
        raise MigrationError("sleep schedule enabled must be a boolean")
    days = data.get("days_of_week", [])
    if not isinstance(days, list) or any(isinstance(day, bool) or not isinstance(day, int) or day < 0 or day > 6 for day in days):
        raise MigrationError("sleep schedule days must be integers 0-6")
    for field in ("sleep_time", "wake_time"):
        if not isinstance(data.get(field, ""), str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", data.get(field, "")):
            raise MigrationError(f"sleep schedule {field} must be HH:MM")
    mode = data.get("mode", "blacklist")
    if mode not in ("blacklist", "whitelist", "ban"):
        raise MigrationError("sleep schedule mode is invalid")
    if data["sleep_time"] == data["wake_time"]:
        raise MigrationError("sleep schedule times must differ")
    if data.get("enabled", False) and not days:
        raise MigrationError("enabled sleep schedule requires days")
    suppressed = data.get("suppressed_occurrences", [])
    if not isinstance(suppressed, list) or not all(isinstance(item, str) for item in suppressed):
        raise MigrationError("sleep schedule suppressions must be strings")
    blacklist = _domains(data.get("blacklist", []))
    whitelist = _domains(data.get("whitelist", []))
    if data.get("enabled", False) and mode in ("blacklist", "whitelist") and not (blacklist if mode == "blacklist" else whitelist):
        raise MigrationError("enabled selected-site sleep schedule requires domains")
    return {
        "enabled": data.get("enabled", False),
        "days_of_week": sorted(set(days)),
        "sleep_time": data["sleep_time"],
        "wake_time": data["wake_time"],
        "mode": mode,
        "blacklist": blacklist,
        "whitelist": whitelist,
        "suppressed_occurrences": suppressed,
    }


def migrate_v1_to_v2(root: Path) -> dict[Path, Any]:
    """Add the independent Sleep Schedule document to manifest-backed v1 state."""
    sleep_path = root / "sleep_schedule.json"
    if not sleep_path.exists():
        return {
            sleep_path: {
                "enabled": False,
                "days_of_week": [],
                "sleep_time": "22:00",
                "wake_time": "07:00",
                "mode": "blacklist",
                "blacklist": [],
                "whitelist": [],
                "suppressed_occurrences": [],
                "pending_config": None,
                "pending_apply_at": None,
            }
        }
    schedule = _sleep_schedule(_load(sleep_path))
    schedule["pending_config"] = None
    schedule["pending_apply_at"] = None
    return {sleep_path: schedule}


def migrate_v0_to_v1(root: Path) -> dict[Path, Any]:
    """Return validated migrated documents without writing them."""
    migrated: dict[Path, Any] = {}

    lists_path = root / "lists.json"
    if lists_path.exists():
        data = _load(lists_path)
        if not isinstance(data, dict) or set(data) - {"blacklist", "whitelist"}:
            raise MigrationError("lists.json has unknown structure")
        migrated[lists_path] = {
            "blacklist": _domains(data.get("blacklist", [])),
            "whitelist": _domains(data.get("whitelist", [])),
        }

    groups_path = root / "groups.json"
    if groups_path.exists():
        data = _load(groups_path)
        if not isinstance(data, dict) or not all(isinstance(name, str) for name in data):
            raise MigrationError("groups.json has unknown structure")
        migrated[groups_path] = {name: _domains(values) for name, values in data.items()}

    settings_path = root / "settings.json"
    if settings_path.exists():
        data = _load(settings_path)
        if not isinstance(data, dict) or set(data) - set(DEFAULT_SETTINGS):
            raise MigrationError("settings.json contains unknown keys")
        settings = DEFAULT_SETTINGS.copy()
        settings.update(data)
        migrated[settings_path] = settings

    perma_path = root / "perma_blocklist.json"
    if perma_path.exists():
        data = _load(perma_path)
        if not isinstance(data, dict) or set(data) - {"domains", "pending_unlocks"}:
            raise MigrationError("perma_blocklist.json has unknown structure")
        pending = data.get("pending_unlocks", {})
        if not isinstance(pending, dict):
            raise MigrationError("pending permanent unlocks must be an object")
        migrated_pending = {}
        for raw_domain, timestamp in pending.items():
            _validate_iso(timestamp, "permanent unlock")
            migrated_pending[_domain(raw_domain)] = timestamp
        migrated[perma_path] = {
            "domains": _domains(data.get("domains", [])),
            "pending_unlocks": migrated_pending,
        }

    templates_path = root / "templates.json"
    if templates_path.exists():
        data = _load(templates_path)
        if not isinstance(data, dict) or set(data) != {"templates"}:
            raise MigrationError("templates.json has unknown structure")
        if not isinstance(data["templates"], list) or not all(
            isinstance(item, dict) for item in data["templates"]
        ):
            raise MigrationError("templates must be a list of objects")
        migrated[templates_path] = data

    session_path = root / "session.lock"
    if session_path.exists():
        data = _load(session_path)
        if not isinstance(data, dict):
            raise MigrationError("session.lock must be an object")
        for field in ("expiry", "started", "pending_unlock_at", "last_persist_wall", "pomo_phase_expiry"):
            _validate_iso(data.get(field), field)
        for field in ("schedules", "recurring_schedules"):
            value = data.get(field, [])
            if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                raise MigrationError(f"{field} must be a list of objects")
        migrated[session_path] = data

    history_path = root / "session_history.json"
    if history_path.exists():
        data = _load(history_path)
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise MigrationError("session history must be a list of objects")
        migrated[history_path] = data

    sleep_path = root / "sleep_schedule.json"
    if sleep_path.exists():
        migrated[sleep_path] = _sleep_schedule(_load(sleep_path))

    return migrated
