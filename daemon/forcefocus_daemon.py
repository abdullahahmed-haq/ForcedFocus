#!/usr/bin/env python3
"""
ForcedFocus Daemon v2 — Root-level macOS website blocker.
Supports blacklist mode (block listed sites) and whitelist mode
(allow ONLY listed sites by redirecting DNS + pinning IPs).
"""
from __future__ import annotations
import os
import sys
import json
import base64
import time
import signal
import socket
import struct
import select
import hashlib
import hmac
import logging
import threading
import queue
import subprocess
import concurrent.futures
import mimetypes
import re
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote
import urllib.request
import urllib.error
from forcefocus.sni_proxy import SniProxy
from forcefocus.dns_proxy import LocalDNSProxy
from forcefocus.utils import get_continuous_time
from forcefocus.constants import *
from forcefocus.history import HistoryManager
from forcefocus.settings import SettingsManager
from forcefocus.domains import DomainsManager
from forcefocus.state_store import StateStore, StateStoreError
from forcefocus.command_service import CommandService
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DAEMON
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class ForcedFocusDaemon:
    def __init__(self):
        from forcefocus.enforcement import EnforcementManager
        from forcefocus.session import SessionManager
        from forcefocus.prayer import PrayerManager
        from forcefocus.watchdog import WatchdogManager
        from forcefocus.api_socket import SocketAPIManager
        from forcefocus.api_http import HTTPAPIManager
        from forcefocus.notifications import NotificationsManager
        from forcefocus.sleep_schedule import SleepScheduleManager
        from forcefocus.state import DaemonState
        from forcefocus.events import EventManager
        self.events = EventManager()
        self.state = DaemonState()
        self.state_store = StateStore(CONFIG_DIR)
        self.recovery_required = False
        self.migration_in_progress = False
        self.enforcement_manager = EnforcementManager(self)
        self.session_manager = SessionManager(self)
        self.prayer_manager = PrayerManager(self)
        self.watchdog_manager = WatchdogManager(self)
        self.socket_api_manager = SocketAPIManager(self)
        self.http_api_manager = HTTPAPIManager(self)
        self.notifications_manager = NotificationsManager(self)
        self.state.session.active = False
        self.state.session.mode = "blacklist"
        self.dns_proxy = None
        self.sni_proxy = None
        self.state_changed = threading.Event()
        self.shutdown_event = threading.Event()
        self.state_revision = 0
        self.notification_warning: dict | None = None
        self.prayer_ban_active = ""
        # Populated while Prayer interrupts a regular session. The values are
        # persisted so a daemon restart cannot silently consume focus time.
        self.prayer_suspension: dict | None = None
        self._sse_listeners = set()
        self._sse_listeners_lock = threading.Lock()
        self.state.active_domains: list[str] = []
        self.active_domains_set: set[str] = set()
        self.session_base_domains: list[str] = (
            []
        )  # Raw domains before /etc/hosts expansion
        self.state.session.session_expiry: datetime | None = None
        self.state.session.pending_unlock_at: datetime | None = None
        self.hosts_hash: str | None = None
        self._hosts_stat: tuple | None = None  # ⚡ (mtime, size) for cheap watchdog pre-check
        self.dns_proxy = None
        self.original_dns: dict[str, str] = {}
        self.whitelist_resolved: dict[str, list[str]] = {}
        self._cached_lists: dict | None = None
        self._cached_lists_mtime: float = 0.0
        self.enforcement_lock = threading.RLock()
        self._cached_groups: dict | None = None
        self._cached_groups_mtime: float = 0.0
        self.whitelist_count: int = 0
        self.whitelist_expanded_count: int = 0
        self.state.session.total_duration_seconds: int = 0
        self.state.session.session_type: str = "standard"
        self.state.pomodoro.pomo_focus_minutes: int = 0
        self.state.pomodoro.pomo_break_minutes: int = 0
        self.state.pomodoro.pomo_total_cycles: int = 0
        self.state.pomodoro.pomo_current_cycle: int = 0
        self.state.pomodoro.pomo_phase: str = "focus"
        self.state.pomodoro.pomo_phase_expiry: datetime | None = None
        self.state.pomodoro.pomo_phases_tracked_seconds: int = 0
        self.state.session.intent: str | None = None
        self.state.session.intent_tasks: list = []
        self.state.session.session_group_id: str | None = None
        self.lock = threading.RLock()
        self.history_manager = HistoryManager(self)
        self.settings_manager = SettingsManager(self)
        self.domains_manager = DomainsManager(self)
        self.sleep_schedule_manager = SleepScheduleManager(self)
        from forcefocus.schedules import SchedulesManager
        self.schedules_manager = SchedulesManager(self)
        self.command_service = CommandService(self)
        self._passphrase_attempts = 0
        self._last_attempt_time = 0.0
        # Monotonic time anchors (immune to clock manipulation)
        self._mono_session_end: float = 0.0
        self._mono_unlock_end: float = 0.0
        self._mono_pomo_phase_end: float = 0.0
        self._mono_last_intent_notif: float = 0.0
        self._mono_last_recurring_check: float = 0.0
        self._reenforce_flag = False  # Set by signal handler, handled by watchdog
        self.state.session.session_groups: list[str] = []  # Group names active in current session
        self.schedules: list = []
        self.recurring_schedules: list = []
        self.settings = self.settings_manager.load_settings()
        # Permanent blocklist state (independent from session blacklist)
        self.perma_blocklist: list[str] = []
        self.perma_pending_unlocks: dict[str, datetime] = {}  # domain → unlock-ready-at
        self._mono_perma_unlock_ends: dict[str, float] = {}  # domain → monotonic anchor
        self._perma_hosts_hash: str | None = None  # SHA256 of permanent block in /etc/hosts
        self._perma_passphrase_attempts = 0
        self._perma_last_attempt_time = 0.0
        self._perma_hosts_stat: tuple[float, int] | None = None
        self._ip_backlog: dict[str, dict[str, float]] = {}  # Domain -> IP -> Expiry timestamp
        self._whitelisted_ip_backlog: dict[str, dict[str, float]] = {}  # Domain -> IP -> Expiry timestamp
        self._ip_resolution_running = False
        self._net_services_cache: list[str] = []
        self._net_services_cache_time: float = 0.0
        self._cached_ks_hash = None
        self._cached_ks_hash_mtime: float = 0.0
    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def run(self):
        setup_logging()
        logging.info("ForcedFocus daemon v2 starting (PID %d).", os.getpid())
        self._ensure_config_dir()
        self._ensure_lists_file()
        self._ensure_groups_file()
        self._ensure_perma_blocklist_file()
        self._ensure_templates_file()
        self._ensure_sleep_schedule_file()
        self._ensure_sounds_dir()
        self.migration_in_progress = True
        try:
            self.state_store.ensure_schema()
        except StateStoreError as exc:
            self.recovery_required = True
            logging.critical("State migration failed: %s", exc)
            raise
        finally:
            self.migration_in_progress = False
        self._secure_state_permissions()
        self._load_ks_hash_cache()
        self.sleep_schedule_manager.load()
        self._generate_api_token()
        self._install_signal_handlers()
        self._recover_stale_hosts_lock()
        # Load permanent blocklist and enforce immediately (before session restore)
        self.domains_manager._load_perma_state()
        self.enforcement_manager._enforce_perma_block()
        # Restore session BEFORE starting watchdog to avoid race (C2)
        with self.lock:
            self._restore_session()
        wt = threading.Thread(target=self.watchdog_manager.watchdog_loop, name="watchdog")
        wt.start()
        ht = threading.Thread(target=self.http_api_manager.http_server, name="http")
        ht.start()
        try:
            self.socket_api_manager.socket_server()
        finally:
            self.shutdown_event.set()
            self.http_api_manager.shutdown()
            wt.join(timeout=5)
            ht.join(timeout=5)
            logging.info("ForcedFocus daemon stopped; persisted enforcement state was preserved.")
    @staticmethod
    def _ensure_config_dir():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(str(CONFIG_DIR), 0o711)
    @staticmethod
    def _ensure_lists_file():
        if not LISTS_FILE.exists():
            LISTS_FILE.write_text(
                json.dumps({"blacklist": [], "whitelist": []}, indent=2)
            )
            os.chmod(str(LISTS_FILE), 0o600)
    @staticmethod
    def _ensure_groups_file():
        if not GROUPS_FILE.exists():
            GROUPS_FILE.write_text(json.dumps({}, indent=2))
            os.chmod(str(GROUPS_FILE), 0o600)
    @staticmethod
    def _ensure_perma_blocklist_file():
        if not PERMA_BLOCK_FILE.exists():
            PERMA_BLOCK_FILE.write_text(
                json.dumps({"domains": [], "pending_unlocks": {}}, indent=2)
            )
            os.chmod(str(PERMA_BLOCK_FILE), 0o600)
    @staticmethod
    def _ensure_templates_file():
        if not TEMPLATES_FILE.exists():
            TEMPLATES_FILE.write_text(json.dumps({"templates": []}, indent=2))
            os.chmod(str(TEMPLATES_FILE), 0o600)
    @staticmethod
    def _ensure_sleep_schedule_file():
        if not SLEEP_SCHEDULE_FILE.exists():
            SLEEP_SCHEDULE_FILE.write_text(
                json.dumps(
                    {
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
                    },
                    indent=2,
                )
            )
            os.chmod(str(SLEEP_SCHEDULE_FILE), 0o600)
    @staticmethod
    def _ensure_sounds_dir():
        SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(str(SOUNDS_DIR), 0o755)
    @staticmethod
    def _secure_state_permissions():
        """Keep user state private even after upgrading older installations."""
        for path in (
            SESSION_LOCK,
            SESSION_LOCK_PREVIOUS,
            STATE_MANIFEST_FILE,
            KS_HASH_FILE,
            LISTS_FILE,
            GROUPS_FILE,
            SETTINGS_FILE,
            PERMA_BLOCK_FILE,
            TEMPLATES_FILE,
            HISTORY_FILE,
            SLEEP_SCHEDULE_FILE,
            PRAYER_CACHE_FILE,
        ):
            if path.exists():
                ForcedFocusDaemon._secure_state_file_permissions(path)

    @staticmethod
    def _secure_state_file_permissions(path: Path):
        """Remove stale app-owned user locks before applying private permissions."""
        if sys.platform == "darwin":
            subprocess.run(
                ["chflags", "nouchg", str(path)],
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            )
        os.chmod(str(path), 0o600)
    def _load_ks_hash_cache(self):
        try:
            if KS_HASH_FILE.exists():
                self._cached_ks_hash = self.state_store.read_json(KS_HASH_FILE)
                if self._cached_ks_hash is None:
                    raise ValueError("security key file must contain an object")
                self._cached_ks_hash_mtime = KS_HASH_FILE.stat().st_mtime
        except Exception as exc:
            logging.error("Failed to load ks_hash into cache: %s", exc)

    def _generate_api_token(self):
        """Generate a per-launch API token for HTTP mutation endpoint auth."""
        import secrets
        self.api_token = secrets.token_hex(32)
        try:
            API_TOKEN_FILE.write_text(self.api_token)
            os.chmod(str(API_TOKEN_FILE), 0o600)
            # Chown to the real user so the web UI can read it
            user_file = Path("/etc/forcefocus/user")
            if user_file.exists():
                import pwd
                username = user_file.read_text().strip()
                try:
                    pw = pwd.getpwnam(username)
                    os.chown(str(API_TOKEN_FILE), pw.pw_uid, pw.pw_gid)
                except (KeyError, OSError):
                    pass
            logging.info("API token generated and written to %s", API_TOKEN_FILE)
        except OSError as exc:
            logging.error("Failed to write API token: %s", exc)
    def _install_signal_handlers(self):
        def _handler(signum, _frame):
            # Signal handlers only set thread-safe flags. The main/socket and
            # watchdog loops perform shutdown or re-enforcement outside the
            # signal context.
            if signum == signal.SIGHUP:
                self._reenforce_flag = True
            else:
                self.shutdown_event.set()
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGHUP, _handler)
    def _recover_stale_hosts_lock(self):
        """On startup, if no active session exists but hosts is locked, unlock it."""
        if self.state.session.active:
            return  # Session running, lock is intentional
        try:
            result = subprocess.run(
                ["ls", "-lO", str(HOSTS_PATH)],
                capture_output=True, text=True, timeout=5
            )
            if "uchg" in result.stdout:
                logging.warning(
                    "Detected stale uchg flag on %s from previous crash. Removing.",
                    HOSTS_PATH
                )
                subprocess.run(
                    ["chflags", "nouchg", str(HOSTS_PATH)],
                    capture_output=True, timeout=5
                )
                # Also strip any leftover ForcedFocus entries
                content = self.enforcement_manager._strip_block(HOSTS_PATH.read_text())
                HOSTS_PATH.write_text(content)
                self.enforcement_manager._flush_dns()
        except Exception as exc:
            logging.error("Stale hosts lock recovery failed: %s", exc)
    # ── Lists Management ──────────────────────────────────────────────────────
    # ── Permanent Blocklist Management ────────────────────────────────────────
    # ── Permanent Block Enforcement ───────────────────────────────────────────
    @staticmethod
    def _atomic_write_json(path: Path, data: dict, indent=None):
        try:
            return StateStore.write_json(path, data, indent=indent)
        except Exception as exc:
            logging.error("Atomic write failed for %s: %s", path, exc)
            raise
    # ── Session Management ────────────────────────────────────────────────────
    def _restore_session(self):
        if not SESSION_LOCK.exists():
            logging.info("No persisted session found. Daemon idle.")
            return
        data = self.state_store.read_json(SESSION_LOCK)
        if data is None:
            data = self.state_store.read_json(SESSION_LOCK_PREVIOUS)
            if data is None:
                self.recovery_required = True
                logging.critical(
                    "session.lock and session.lock.prev are unreadable; manual recovery is required."
                )
                return
            logging.warning("Recovered session state from session.lock.prev.")
            self._atomic_write_json(SESSION_LOCK, data)
        # Restore schedules first (they exist independently of active sessions)
        if data.get("schedules"):
            try:
                for sch in data["schedules"]:
                    sch_time = datetime.fromisoformat(sch["start_time"])
                    # Skip schedules whose end_time has already passed
                    end_time = datetime.fromisoformat(sch["end_time"])
                    if end_time <= datetime.now():
                        continue
                    mono_start = get_continuous_time() + (sch_time - datetime.now()).total_seconds()
                    self.schedules.append(
                        {
                            "start_time": sch_time,
                            "end_time": end_time,
                            "mono_start": mono_start,
                            "cmd": sch["cmd"],
                        }
                    )
                self.schedules.sort(key=lambda x: x["start_time"])
                if self.schedules:
                    logging.info("Restored %d scheduled sessions.", len(self.schedules))
            except Exception as exc:
                logging.error("Failed to restore scheduled sessions: %s", exc)
                self.schedules = []
        if data.get("recurring_schedules"):
            restored = []
            for raw_rule in data["recurring_schedules"]:
                ok, message, rule = self.schedules_manager.normalize_recurring_schedule(raw_rule)
                if ok:
                    restored.append(rule)
                else:
                    logging.warning("Skipped invalid recurring schedule during restore: %s", message)
            self.recurring_schedules = restored
            logging.info("Restored %d recurring schedules.", len(self.recurring_schedules))
        # If no active session data, we're done (schedule-only lockfile)
        if not data.get("expiry"):
            if data.get("session_type") == "sleep":
                self._sleep_restore_error("missing expiry")
            if self.schedules:
                self._persist_session_lock()
            return
        try:
            expiry = datetime.fromisoformat(data["expiry"])
        except (KeyError, TypeError, ValueError) as exc:
            previous = self.state_store.read_json(SESSION_LOCK_PREVIOUS)
            if previous is not None and previous != data:
                try:
                    datetime.fromisoformat(previous["expiry"])
                except (KeyError, TypeError, ValueError):
                    previous = None
            if previous is not None:
                logging.warning(
                    "Invalid expiry in session.lock (%s); restoring the previous durable session.",
                    exc,
                )
                self.schedules = []
                self.recurring_schedules = []
                self._atomic_write_json(SESSION_LOCK, previous)
                self._restore_session()
                return
            self.recovery_required = True
            logging.critical(
                "Invalid expiry in session.lock (%s); preserving state for recovery.",
                exc,
            )
            return
        if data.get("session_type") == "sleep":
            error = self._validate_restored_sleep_session(data, expiry)
            if error:
                self._sleep_restore_error(error)
        prayer_suspension = data.get("prayer_suspension")
        suspended_remaining = None
        if isinstance(prayer_suspension, dict):
            try:
                suspended_remaining = max(
                    0.0, float(prayer_suspension["session_remaining_seconds"])
                )
            except (KeyError, TypeError, ValueError):
                prayer_suspension = None
            else:
                # The persisted wall expiry predates the Prayer interruption.
                # Restore from the captured remaining time instead of treating
                # Prayer time (or daemon downtime during it) as focus time.
                expiry = datetime.now() + timedelta(seconds=suspended_remaining)
                data["expiry"] = expiry.isoformat()
        if datetime.now() >= expiry:
            logging.info("Persisted session expired. Cleaning up.")
            self.state.session.mode = data.get("mode", "blacklist")
            if self.state.session.mode in ("whitelist", "ban"):
                self.original_dns = data.get("original_dns", {})
            self.session_manager._cleanup_session()
            return
        wall_remaining = (expiry - datetime.now()).total_seconds()
        self.state.session.total_duration_seconds = data.get("duration_minutes", 120) * 60
        if "mono_elapsed" in data and "last_persist_wall" in data:
            wall_gap = (
                datetime.now() - datetime.fromisoformat(data["last_persist_wall"])
            ).total_seconds()
            mono_remaining = (
                self.state.session.total_duration_seconds - data["mono_elapsed"] - wall_gap
            )
            remaining = min(wall_remaining, mono_remaining)
        else:
            remaining = wall_remaining
        if suspended_remaining is not None:
            remaining = suspended_remaining
        remaining = max(0, remaining)
        self.state.session.mode = data.get("mode", "blacklist")
        self.state.session.session_expiry = expiry
        self.remaining_seconds = remaining
        self.state.session.session_type = data.get("session_type", "standard")
        self.state.session.intent = data.get("intent", None)
        self.state.session.intent_tasks = data.get("intent_tasks", [])
        self.state.session.session_groups = data.get("session_groups", [])
        self.state.pomodoro.pomo_focus_minutes = data.get("pomo_focus_minutes", 0)
        self.state.pomodoro.pomo_break_minutes = data.get("pomo_break_minutes", 0)
        self.state.pomodoro.pomo_total_cycles = data.get("pomo_total_cycles", 0)
        self.state.pomodoro.pomo_current_cycle = data.get("pomo_current_cycle", 0)
        self.state.pomodoro.pomo_phase = data.get("pomo_phase", "focus")
        now_mono = get_continuous_time()
        if data.get("pending_unlock_at"):
            self.state.session.pending_unlock_at = datetime.fromisoformat(data["pending_unlock_at"])
            unlock_remaining = max(
                0, (self.state.session.pending_unlock_at - datetime.now()).total_seconds()
            )
            if unlock_remaining <= 0:
                logging.info("Pending unlock expired during downtime. Ending session.")
                if self.state.session.mode in ("whitelist", "ban"):
                    self.original_dns = data.get("original_dns", {})
                self.session_manager._cleanup_session()
                return
            self._mono_unlock_end = now_mono + unlock_remaining
            self.pending_unlock_seconds = unlock_remaining
        else:
            self.state.session.pending_unlock_at = None
            self.pending_unlock_seconds = 0
            self._mono_unlock_end = 0.0
        if prayer_suspension and prayer_suspension.get("pomo_phase_remaining_seconds") is not None:
            try:
                pomo_remaining = max(
                    0.0, float(prayer_suspension["pomo_phase_remaining_seconds"])
                )
            except (TypeError, ValueError):
                pomo_remaining = 0.0
            self.state.pomodoro.pomo_phase_expiry = datetime.now() + timedelta(
                seconds=pomo_remaining
            )
            self.pomo_phase_remaining = pomo_remaining
        elif data.get("pomo_phase_expiry"):
            self.state.pomodoro.pomo_phase_expiry = datetime.fromisoformat(data["pomo_phase_expiry"])
            self.pomo_phase_remaining = max(
                0, (self.state.pomodoro.pomo_phase_expiry - datetime.now()).total_seconds()
            )
        else:
            self.state.pomodoro.pomo_phase_expiry = None
            self.pomo_phase_remaining = 0
        # Set monotonic anchors from remaining wall-clock time
        self._mono_session_end = now_mono + remaining
        if self.state.pomodoro.pomo_phase_expiry:
            self._mono_pomo_phase_end = now_mono + max(
                0, (self.state.pomodoro.pomo_phase_expiry - datetime.now()).total_seconds()
            )
        if prayer_suspension:
            self.prayer_suspension = prayer_suspension
        self.state.session.session_group_id = data.get("session_group_id")
        self.state.session.sleep_occurrence = data.get("sleep_occurrence")
        self.state.session.active = True
        if self.state.session.mode in ("whitelist", "ban"):
            self.original_dns = data.get("original_dns", {})
            self.state.active_domains = data.get(
                "active_domains", data.get("blocked_domains", [])
            )
            if self.state.session.mode == "ban":
                self.state.active_domains = []
            if not isinstance(self.state.active_domains, list):
                raise ValueError("Persisted active_domains must be a list.")
            self.active_domains_set = set(self.state.active_domains)
            self.whitelist_resolved = data.get("whitelist_resolved", {})
            self.whitelist_count = data.get("whitelist_count", len(self.state.active_domains))
            self.whitelist_expanded_count = data.get(
                "whitelist_expanded_count", len(self.state.active_domains)
            )
        else:
            self.state.active_domains = data.get(
                "active_domains",
                data.get("blocked_domains", self.domains_manager.get_blacklist_domains()),
            )
            if not isinstance(self.state.active_domains, list):
                raise ValueError("Persisted active_domains must be a list.")
            self.active_domains_set = set(self.state.active_domains)
        self.session_base_domains = data.get("session_base_domains", [])
        prayer_still_active = self.watchdog_manager.restore_prayer_suspension(
            get_continuous_time(), datetime.now()
        )
        if self.state.session.session_type == "pomodoro" and self.state.pomodoro.pomo_phase_expiry:
            if datetime.now() >= self.state.pomodoro.pomo_phase_expiry:
                logging.info("Pomodoro phase expired during downtime. Advancing.")
                self.session_manager._transition_pomodoro_phase()
                logging.info(
                    "Resuming %s session — %d min remaining.",
                    self.state.session.mode,
                    int(remaining / 60),
                )
                return
        is_break = self.state.session.session_type == "pomodoro" and self.state.pomodoro.pomo_phase == "break"
        if prayer_still_active:
            self.watchdog_manager._enforce_prayer_ban()
        elif self.state.session.mode in ("whitelist", "ban"):
            if not is_break:
                self.enforcement_manager._enforce_whitelist()
        else:
            if not is_break:
                self.enforcement_manager._enforce_block()
        logging.info(
            "Resuming %s session — %d min remaining.", self.state.session.mode, int(remaining / 60)
        )

    def _sleep_restore_error(self, reason: str) -> None:
        self.recovery_required = True
        logging.critical(
            "Sleep session recovery required; preserving session.lock: %s", reason
        )
        raise StateStoreError(f"Sleep session recovery required: {reason}")

    def _validate_restored_sleep_session(self, data: dict, expiry: datetime) -> str | None:
        if expiry.tzinfo is not None or expiry <= datetime.now():
            return "expiry is not a future local timestamp"
        mode = data.get("mode")
        if mode not in ("blacklist", "whitelist", "ban"):
            return "mode is invalid"
        occurrence = data.get("sleep_occurrence")
        if not isinstance(occurrence, str) or "T" not in occurrence:
            return "sleep occurrence identifier is invalid"
        try:
            start = datetime.fromisoformat(occurrence)
        except ValueError:
            return "sleep occurrence identifier is invalid"
        if start.tzinfo is not None:
            return "sleep occurrence identifier is invalid"
        if start > datetime.now():
            return "sleep occurrence has not started"
        schedule = self.sleep_schedule_manager.schedule
        if not schedule.get("enabled") or start.weekday() not in schedule["days_of_week"]:
            return "sleep occurrence does not match the active schedule"
        expected_start, expected_wake = self.sleep_schedule_manager._interval_for_date(
            schedule, start
        )
        if start != expected_start or expiry != expected_wake:
            return "sleep occurrence deadline does not match the active schedule"
        if mode == "ban":
            return None
        base_domains = data.get("session_base_domains")
        active_domains = data.get("active_domains")
        if not self._valid_sleep_domain_snapshot(base_domains):
            return "selected-site snapshot is missing or invalid"
        if not self._valid_sleep_domain_snapshot(active_domains):
            return "active selected-site snapshot is missing or invalid"
        if len(base_domains) > SLEEP_SELECTED_DOMAIN_MAX:
            return "selected-site snapshot exceeds the Chrome rule limit"
        return None

    def _valid_sleep_domain_snapshot(self, domains: object) -> bool:
        return (
            isinstance(domains, list)
            and bool(domains)
            and all(
                isinstance(domain, str)
                and self.domains_manager.validate_domain(domain)
                for domain in domains
            )
        )
    # ── Session History / Tracking ─────────────────────────────────────────────
    def _record_session_history(self):
        self.history_manager.record_session_history()
    # ── Blacklist Enforcement ─────────────────────────────────────────────────
    # ── Whitelist Enforcement ─────────────────────────────────────────────────
    def _stop_sni_proxy(self):
        """Stop the SNI proxy."""
        try:
            if getattr(self, "sni_proxy", None):
                self.sni_proxy.stop_sync()
                self.sni_proxy = None
        except Exception as exc:
            logging.error("Failed to stop SNI proxy: %s", exc)
    def _sni_is_allowed(self, domain: str) -> bool:
        """Callback for SNI proxy to verify if a domain is allowed."""
        if not domain:
            return False
        domain = domain.lower()
        parts = domain.split(".")
        for i in range(len(parts)):
            if ".".join(parts[i:]) in self.active_domains_set:
                return True
        return False
    @staticmethod
    def _get_active_interface() -> str:
        """Dynamically find the active default network interface."""
        try:
            result = subprocess.run(["route", "get", "default"], capture_output=True, text=True, timeout=2)
            for line in result.stdout.split("\n"):
                if "interface:" in line:
                    return line.split(":")[1].strip()
        except Exception:
            pass
        return "en0"
    # ── Common Helpers ────────────────────────────────────────────────────────
    def _set_notification_warning(self, message: str):
        self.notification_warning = {
            "message": message,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.notifications_manager.broadcast_state_changed()
    def _send_mac_notification(self, title: str, message: str, subtitle: str = None):
        """Send a macOS system notification natively via the Swift binary."""
        try:
            # Locate the app bundle
            app_path = Path("/Applications/ForcedFocusBar.app/Contents/MacOS/ForcedFocusBar")
            if not app_path.exists():
                # Fallback to local dev path
                app_path = Path(__file__).parent / "ForcedFocusBar.app/Contents/MacOS/ForcedFocusBar"
            if app_path.exists():
                args = [
                    str(app_path),
                    "-notify-title", title,
                    "-notify-body", message
                ]
                # Executes in <20ms, zero lag
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if self.notification_warning:
                    self.notification_warning = None
                    self.notifications_manager.broadcast_state_changed()
            else:
                fallback = "macOS notification could not be delivered because ForcedFocusBar.app was not found."
                self._set_notification_warning(fallback)
                logging.error(fallback)
        except Exception as e:
            self._set_notification_warning(
                "macOS notification could not be delivered. Check Menu Bar app notification permissions."
            )
            logging.error("Failed to send native notification: %s", e)
    def _persist_session_lock(self):
        """Re-create session.lock from in-memory state."""
        data = {
            "schedules": [
                {
                    "start_time": sch["start_time"].isoformat(),
                    "end_time": sch["end_time"].isoformat(),
                    "cmd": sch["cmd"],
                }
                for sch in self.schedules
            ],
            "recurring_schedules": self.recurring_schedules
        }
        if self.state.session.active and self.state.session.session_expiry:
            data.update(
                {
                    "started": (
                        self.state.session.session_expiry
                        - timedelta(seconds=self.state.session.total_duration_seconds)
                    ).isoformat(),
                    "expiry": self.state.session.session_expiry.isoformat(),
                    "duration_minutes": self.state.session.total_duration_seconds // 60,
                    "mode": self.state.session.mode,
                    "session_type": self.state.session.session_type,
                    "mono_elapsed": get_continuous_time()
                    - (self._mono_session_end - self.state.session.total_duration_seconds),
                    "last_persist_wall": datetime.now().isoformat(),
                    "settings": self.settings,
                }
            )
            if self.state.session.pending_unlock_at:
                data["pending_unlock_at"] = self.state.session.pending_unlock_at.isoformat()
            if self.state.session.session_type == "pomodoro":
                data.update(
                    {
                        "pomo_focus_minutes": self.state.pomodoro.pomo_focus_minutes,
                        "pomo_break_minutes": self.state.pomodoro.pomo_break_minutes,
                        "pomo_total_cycles": self.state.pomodoro.pomo_total_cycles,
                        "pomo_current_cycle": self.state.pomodoro.pomo_current_cycle,
                        "pomo_phase": self.state.pomodoro.pomo_phase,
                        "pomo_phase_expiry": (
                            self.state.pomodoro.pomo_phase_expiry.isoformat()
                            if self.state.pomodoro.pomo_phase_expiry
                            else None
                        ),
                    }
                )
            if self.state.session.mode in ("whitelist", "ban"):
                data["original_dns"] = self.original_dns
                data["whitelist_resolved"] = self.whitelist_resolved
                data["active_domains"] = self.state.active_domains
                data["whitelist_count"] = getattr(self, "whitelist_count", 0)
                data["whitelist_expanded_count"] = getattr(
                    self, "whitelist_expanded_count", 0
                )
            else:
                data["active_domains"] = self.state.active_domains
            data["session_base_domains"] = getattr(self, "session_base_domains", [])
            data["session_group_id"] = self.state.session.session_group_id
            data["sleep_occurrence"] = self.state.session.sleep_occurrence
            data["intent"] = self.state.session.intent
            data["intent_tasks"] = self.state.session.intent_tasks
            data["session_groups"] = self.state.session.session_groups
            if self.prayer_suspension:
                data["prayer_suspension"] = self.prayer_suspension
        try:
            self.state_store.backup_session_lock(SESSION_LOCK, SESSION_LOCK_PREVIOUS)
            self._atomic_write_json(SESSION_LOCK, data)
            logging.info("session.lock re-created from memory.")
            return True
        except Exception as exc:
            self.recovery_required = True
            logging.critical("Failed to persist session.lock: %s", exc)
            return False
    def _validate_settings(self, settings_dict: dict) -> tuple[bool, str, dict]:
        return self.settings_manager.validate_settings(settings_dict)
    # ── Passphrase ────────────────────────────────────────────────────────────
    def _verify_passphrase(self, passphrase: str) -> bool:
        if not self._cached_ks_hash:
            return False
        try:
            salt = bytes.fromhex(self._cached_ks_hash["salt"])
            expected = self._cached_ks_hash["hash"]
        except (KeyError, ValueError):
            return False
        computed = hashlib.pbkdf2_hmac(
            "sha256", passphrase.encode("utf-8"), salt, 100_000
        ).hex()
        return hmac.compare_digest(computed, expected)
def main():
    if os.geteuid() != 0:
        print("ERROR: ForcedFocus daemon must run as root.", file=sys.stderr)
        sys.exit(1)
    daemon = ForcedFocusDaemon()
    daemon.run()
if __name__ == "__main__":
    main()
