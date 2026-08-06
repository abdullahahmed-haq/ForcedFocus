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

    return migrated
