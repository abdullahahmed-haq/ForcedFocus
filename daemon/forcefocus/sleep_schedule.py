"""Sleep Schedule policy, persistence, and occurrence calculations."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from forcefocus.constants import (
    CHROME_DYNAMIC_RULE_LIMIT,
    SLEEP_DNR_RULES_PER_BLACKLIST_DOMAIN,
    SLEEP_DNR_RULES_PER_PERMANENT_DOMAIN,
    SLEEP_DNR_RULES_PER_WHITELIST_DOMAIN,
    SLEEP_DNR_WHITELIST_STATIC_RULES,
    SLEEP_SCHEDULE_FILE,
    SLEEP_SELECTED_DOMAIN_MAX,
)
from forcefocus.state_store import StateStoreError


DEFAULT_SLEEP_SCHEDULE = {
    "enabled": False,
    "days_of_week": [],
    "sleep_time": "22:00",
    "wake_time": "07:00",
    "mode": "blacklist",
    "blacklist": [],
    "whitelist": [],
    "suppressed_occurrences": [],
}
SLEEP_CONFIG_KEYS = frozenset(DEFAULT_SLEEP_SCHEDULE)
SLEEP_PERSISTED_KEYS = SLEEP_CONFIG_KEYS | {"pending_config", "pending_apply_at"}


class SleepScheduleManager:
    def __init__(self, daemon):
        self.daemon = daemon
        self.schedule = dict(DEFAULT_SLEEP_SCHEDULE)
        self.pending_config: dict | None = None
        self.pending_apply_at: datetime | None = None

    def load(self) -> None:
        # A missing file is the only first-run case. Never replace an existing
        # document because it may be evidence of a failed or interrupted write.
        if not SLEEP_SCHEDULE_FILE.exists():
            self._save(DEFAULT_SLEEP_SCHEDULE, None, None)
            return
        data = self.daemon.state_store.read_json(SLEEP_SCHEDULE_FILE)
        if data is None:
            self._persistence_error("sleep_schedule.json is unreadable")
            return
        if set(data) != SLEEP_PERSISTED_KEYS:
            self._persistence_error("sleep_schedule.json has an invalid schema")
        ok, _message, schedule = self.normalize(data, include_suppressions=True)
        if not ok:
            self._persistence_error(f"sleep_schedule.json is invalid: {_message}")
        self.schedule = schedule
        pending = data.get("pending_config")
        apply_at = data.get("pending_apply_at")
        if pending is None:
            if apply_at is not None:
                self._persistence_error("sleep_schedule.json has an invalid pending schedule")
            return
        required_pending_keys = {
            "enabled", "days_of_week", "sleep_time", "wake_time", "mode",
            "blacklist", "whitelist",
        }
        if not isinstance(pending, dict) or set(pending) != required_pending_keys:
            self._persistence_error("sleep_schedule.json has an invalid pending schedule")
        ok, _message, normalized_pending = self.normalize(pending)
        if not ok or not isinstance(apply_at, str):
            self._persistence_error("sleep_schedule.json has an invalid pending schedule")
        try:
            self.pending_apply_at = datetime.fromisoformat(apply_at)
        except ValueError:
            self._persistence_error("sleep_schedule.json has an invalid pending apply time")
        self.pending_config = self._config(normalized_pending)
        self.promote_pending_if_due(datetime.now())

    def _persistence_error(self, message: str) -> None:
        self.daemon.recovery_required = True
        logging.critical("Sleep Schedule recovery required: %s", message)
        raise StateStoreError(f"Sleep Schedule recovery required: {message}")

    @staticmethod
    def _config(schedule: dict) -> dict:
        return {
            key: value
            for key, value in schedule.items()
            if key != "suppressed_occurrences"
        }

    def _save(
        self,
        schedule: dict,
        pending_config: dict | None,
        pending_apply_at: datetime | None,
    ) -> None:
        data = dict(schedule)
        data["pending_config"] = pending_config
        data["pending_apply_at"] = (
            pending_apply_at.isoformat() if pending_apply_at else None
        )
        self.daemon._atomic_write_json(SLEEP_SCHEDULE_FILE, data, indent=2)
        self.schedule = schedule
        self.pending_config = pending_config
        self.pending_apply_at = pending_apply_at

    @staticmethod
    def _time(value: object, name: str) -> tuple[int, str] | tuple[None, str]:
        if not isinstance(value, str):
            return None, f"{name} must be HH:MM."
        parts = value.strip().split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            return None, f"{name} must be HH:MM."
        hour, minute = (int(part) for part in parts)
        if hour > 23 or minute > 59 or len(parts[0]) != 2 or len(parts[1]) != 2:
            return None, f"{name} must be HH:MM."
        return hour * 60 + minute, f"{hour:02d}:{minute:02d}"

    def normalize(self, raw: dict, include_suppressions: bool = False) -> tuple[bool, str, dict]:
        if not isinstance(raw, dict):
            return False, "Sleep Schedule payload must be an object.", {}
        enabled = raw.get("enabled", self.schedule["enabled"])
        if not isinstance(enabled, bool):
            return False, "enabled must be a boolean.", {}
        days_raw = raw.get("days_of_week", self.schedule["days_of_week"])
        if not isinstance(days_raw, list):
            return False, "days_of_week must be a list of integers 0-6.", {}
        days = []
        for day in days_raw:
            if isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6:
                return False, "days_of_week must contain integers 0-6.", {}
            if day not in days:
                days.append(day)
        sleep_minutes, sleep_time = self._time(raw.get("sleep_time", self.schedule["sleep_time"]), "sleep_time")
        wake_minutes, wake_time = self._time(raw.get("wake_time", self.schedule["wake_time"]), "wake_time")
        if sleep_minutes is None or wake_minutes is None:
            return False, sleep_time if sleep_minutes is None else wake_time, {}
        if sleep_minutes == wake_minutes:
            return False, "Invalid Sleep Schedule: sleep_time and wake_time must differ.", {}
        mode = raw.get("mode", self.schedule["mode"])
        if mode not in ("blacklist", "whitelist", "ban"):
            return False, "Invalid mode.", {}
        lists = {}
        for name in ("blacklist", "whitelist"):
            values = raw.get(name, self.schedule[name])
            if not isinstance(values, list):
                return False, f"{name} must be a list of domains.", {}
            normalized = []
            for value in values:
                if not isinstance(value, str):
                    return False, f"{name} contains an invalid domain.", {}
                domain = self.daemon.domains_manager.extract_domain(value)
                if not self.daemon.domains_manager.validate_domain(domain):
                    return False, f"{name} contains an invalid domain.", {}
                if domain not in normalized:
                    normalized.append(domain)
            lists[name] = normalized
        if enabled and not days:
            return False, "days_of_week must include at least one day when enabled.", {}
        if enabled and mode in ("blacklist", "whitelist") and not lists[mode]:
            return False, f"Sleep {mode} mode requires at least one valid domain.", {}
        if enabled:
            capacity_error = self._dnr_capacity_error(mode, len(lists.get(mode, [])))
            if capacity_error:
                return False, capacity_error, {}
        suppressed = raw.get("suppressed_occurrences", []) if include_suppressions else self.schedule.get("suppressed_occurrences", [])
        if not isinstance(suppressed, list) or not all(isinstance(item, str) for item in suppressed):
            return False, "suppressed_occurrences must be a list.", {}
        return True, "", {
            "enabled": enabled,
            "days_of_week": sorted(days),
            "sleep_time": sleep_time,
            "wake_time": wake_time,
            "mode": mode,
            "blacklist": lists["blacklist"],
            "whitelist": lists["whitelist"],
            "suppressed_occurrences": suppressed[-31:],
        }

    def _dnr_capacity_error(self, mode: str, selected_count: int) -> str | None:
        permanent_domains = {
            domain.strip().lower()
            for domain in getattr(self.daemon, "perma_blocklist", [])
            if isinstance(domain, str) and domain.strip()
        }
        permanent_rules = len(permanent_domains) * SLEEP_DNR_RULES_PER_PERMANENT_DOMAIN
        static_rules = SLEEP_DNR_WHITELIST_STATIC_RULES if mode in ("whitelist", "ban") else 0
        remaining_rules = CHROME_DYNAMIC_RULE_LIMIT - permanent_rules - static_rules
        if mode == "ban":
            if remaining_rules < 0:
                return (
                    f"Sleep ban cannot start: {len(permanent_domains)} permanent sites require "
                    f"{permanent_rules} Chrome dynamic rules, leaving no room for its "
                    f"{static_rules} required rules."
                )
            return None
        per_domain_rules = (
            SLEEP_DNR_RULES_PER_BLACKLIST_DOMAIN
            if mode == "blacklist"
            else SLEEP_DNR_RULES_PER_WHITELIST_DOMAIN
        )
        allowed = min(
            SLEEP_SELECTED_DOMAIN_MAX,
            max(0, remaining_rules // per_domain_rules),
        )
        if selected_count <= allowed:
            return None
        return (
            f"Sleep {mode} mode can use at most {allowed} selected sites with "
            f"{len(permanent_domains)} permanent sites. Chrome allows "
            f"{CHROME_DYNAMIC_RULE_LIMIT} dynamic rules; this mode needs "
            f"{per_domain_rules} rules per selected site"
            f"{' plus ' + str(static_rules) + ' static rules' if static_rules else ''}."
        )

    @staticmethod
    def _interval_for_date(schedule: dict, date: datetime) -> tuple[datetime, datetime]:
        start_hour, start_minute = (int(part) for part in schedule["sleep_time"].split(":"))
        wake_hour, wake_minute = (int(part) for part in schedule["wake_time"].split(":"))
        start = date.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        wake = date.replace(hour=wake_hour, minute=wake_minute, second=0, microsecond=0)
        if wake <= start:
            wake += timedelta(days=1)
        return start, wake

    def active_occurrence(self, now: datetime | None = None, schedule: dict | None = None) -> tuple[datetime, datetime] | None:
        schedule = schedule or self.schedule
        if not schedule["enabled"]:
            return None
        now = now or datetime.now()
        for offset in (0, 1):
            day = now - timedelta(days=offset)
            if day.weekday() not in schedule["days_of_week"]:
                continue
            start, wake = self._interval_for_date(schedule, day)
            if start <= now < wake:
                return start, wake
        return None

    def next_occurrence(self, now: datetime | None = None, schedule: dict | None = None) -> tuple[datetime, datetime] | None:
        schedule = schedule or self.schedule
        if not schedule["enabled"]:
            return None
        now = now or datetime.now()
        for offset in range(8):
            day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=offset)
            if day.weekday() not in schedule["days_of_week"]:
                continue
            start, wake = self._interval_for_date(schedule, day)
            if start > now:
                return start, wake
        return None

    def _occurrences(self, schedule: dict, around: datetime, days: int = 8):
        first = around.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        for offset in range(days + 2):
            day = first + timedelta(days=offset)
            if day.weekday() in schedule["days_of_week"]:
                yield self._interval_for_date(schedule, day)

    def conflicts_interval(self, start: datetime, end: datetime, schedule: dict | None = None) -> bool:
        schedule = schedule or self.schedule
        if not schedule["enabled"]:
            return False
        return any(max(start, sleep_start) < min(end, sleep_end) for sleep_start, sleep_end in self._occurrences(schedule, start))

    def conflicts_recurring(self, rule: dict, schedule: dict | None = None) -> bool:
        schedule = schedule or self.schedule
        if not schedule["enabled"]:
            return False
        for start, end in self._occurrences(schedule, datetime.now()):
            if self.daemon.schedules_manager._recurring_overlaps_interval(rule, start, end):
                return True
        return False

    def _conflict_message(self, schedule: dict) -> str | None:
        now = datetime.now()
        if self.daemon.state.session.active and self.daemon.state.session.session_type != "sleep":
            expiry = self.daemon.state.session.session_expiry
            if expiry and self.conflicts_interval(now, expiry, schedule):
                return "Sleep Schedule overlaps with the active session."
        for oneoff in self.daemon.schedules:
            if self.conflicts_interval(oneoff["start_time"], oneoff["end_time"], schedule):
                return "Sleep Schedule overlaps with a scheduled session."
        for rule in self.daemon.recurring_schedules:
            if self.conflicts_recurring(rule, schedule):
                return "Sleep Schedule overlaps with a recurring schedule."
        return None

    def cmd_get_sleep_schedule(self) -> dict:
        with self.daemon.lock:
            response = dict(self.schedule)
            response.pop("suppressed_occurrences", None)
            return {
                "status": "ok",
                "sleep_schedule": response,
                "pending_config": self.pending_config,
                "pending_apply_at": (
                    self.pending_apply_at.isoformat() if self.pending_apply_at else None
                ),
                "summary": self.status_summary(),
            }

    def cmd_save_sleep_schedule(self, cmd: dict) -> dict:
        with self.daemon.lock:
            ok, message, schedule = self.normalize(cmd)
            if not ok:
                return {"status": "error", "message": message}
            conflict = self._conflict_message(schedule)
            if conflict:
                return {"status": "error", "message": conflict}
            was_active = self.daemon.state.session.active and self.daemon.state.session.session_type == "sleep"
            if was_active:
                apply_at = self.daemon.state.session.session_expiry
                if apply_at is None:
                    return {"status": "error", "message": "Sleep wake deadline is unavailable."}
                pending = self._config(schedule)
                self._save(self.schedule, pending, apply_at)
            else:
                self._save(schedule, None, None)
            self.daemon.notifications_manager.broadcast_state_changed()
            return {
                "status": "ok",
                "message": "Sleep Schedule queued for the next occurrence." if was_active else "Sleep Schedule saved.",
                "queued": was_active,
                "sleep_schedule": self._config(self.schedule),
                "pending_config": self.pending_config,
                "apply_at": self.pending_apply_at.isoformat() if self.pending_apply_at else None,
            }

    def start_if_due(self, now: datetime) -> dict | None:
        # Pending edits are bound to the original occurrence's wake deadline,
        # even if that occurrence ended early or no session was restored.
        self.promote_pending_if_due(now)
        occurrence = self.active_occurrence(now)
        if not occurrence or self.daemon.state.session.active:
            return None
        start, wake = occurrence
        occurrence_id = start.isoformat()
        if occurrence_id in self.schedule.get("suppressed_occurrences", []):
            return None
        selected = list(self.schedule[self.schedule["mode"]]) if self.schedule["mode"] != "ban" else []
        expanded = (
            self.daemon.domains_manager.expand_blacklist_domains(selected)
            if self.schedule["mode"] == "blacklist"
            else self.daemon.domains_manager.expand_whitelist_domains(selected)
            if self.schedule["mode"] == "whitelist"
            else []
        )
        return {
            "action": "start",
            "duration_minutes": max(1, int((wake - now).total_seconds() / 60) + 1),
            "mode": self.schedule["mode"],
            "session_type": "sleep",
            "_sleep": True,
            "_sleep_authority": self,
            "_sleep_wake": wake,
            "_sleep_occurrence": occurrence_id,
            "_sleep_base_domains": selected,
            "_sleep_active_domains": expanded,
        }

    def suppress_current_occurrence(self, occurrence: str | None) -> None:
        if not occurrence or occurrence in self.schedule["suppressed_occurrences"]:
            return
        updated = dict(self.schedule)
        updated["suppressed_occurrences"] = (updated["suppressed_occurrences"] + [occurrence])[-31:]
        try:
            self._save(updated, self.pending_config, self.pending_apply_at)
        except Exception as exc:
            # Keep the current daemon from restarting the occurrence even if
            # durable storage is temporarily unavailable.
            self.schedule = updated
            logging.error("Failed to persist Sleep Schedule early-stop suppression: %s", exc)

    def promote_pending_if_due(self, now: datetime | None = None) -> bool:
        """Atomically apply queued edits only after their active occurrence wakes."""
        if self.pending_config is None or self.pending_apply_at is None:
            return False
        now = now or datetime.now()
        if now < self.pending_apply_at:
            return False
        promoted = dict(self.pending_config)
        promoted["suppressed_occurrences"] = self.schedule["suppressed_occurrences"]
        self._save(promoted, None, None)
        self.daemon.notifications_manager.broadcast_state_changed()
        return True

    def status_summary(self) -> dict:
        now = datetime.now()
        active = self.active_occurrence(now)
        next_occurrence = self.next_occurrence(now)
        is_active = self.daemon.state.session.active and self.daemon.state.session.session_type == "sleep"
        wake = self.daemon.state.session.session_expiry if is_active else (active[1] if active else None)
        return {
            "enabled": self.schedule["enabled"],
            "active": is_active,
            "mode": self.schedule["mode"],
            "days_of_week": list(self.schedule["days_of_week"]),
            "sleep_time": self.schedule["sleep_time"],
            "wake_time": self.schedule["wake_time"],
            "wake_at": wake.isoformat() if wake else None,
            "next_start_at": next_occurrence[0].isoformat() if next_occurrence else None,
            "remaining_seconds": max(0, int((wake - now).total_seconds())) if wake else 0,
            "pending_changes": self.pending_config is not None,
            "pending_apply_at": self.pending_apply_at.isoformat() if self.pending_apply_at else None,
        }
