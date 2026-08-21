from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
import urllib.parse
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from forcefocus.constants import *
from forcefocus.utils import get_continuous_time

class PrayerManager:
    _REFRESH_BACKOFF_INITIAL_SECONDS = 60.0
    _REFRESH_BACKOFF_MAX_SECONDS = 3600.0

    def __init__(self, daemon):
        self.daemon = daemon
        self._calendar_lock = threading.RLock()
        self._calendar_cache: dict[str, dict] = {}
        self._fallback_calendar_cache: dict[str, dict] = {}
        self._calendar_cache_loaded = False
        self._calendar_generation = 0
        self._refresh_queue: list[tuple[int, int, int, tuple[object, object, object]]] = []
        self._refresh_pending: set[tuple[str, int]] = set()
        self._refresh_failures: dict[str, tuple[int, float]] = {}
        self._refresh_worker_thread: threading.Thread | None = None

    @staticmethod
    def _skip_key(prayer: dict) -> str:
        return f"{prayer['time'].strftime('%Y-%m-%d')}-{prayer['name']}"

    def _upcoming_prayers(self, now: datetime) -> list[dict]:
        """Return today's and tomorrow's prayer times after ``now``."""
        prayers = self._get_prayer_times_for_date(now)
        tomorrow = now + timedelta(days=1)
        prayers.extend(self._get_prayer_times_for_date(tomorrow))
        return sorted((p for p in prayers if p["time"] > now), key=lambda p: p["time"])

    def active_prayer_window(self, now: datetime) -> dict | None:
        """Find an active, unskipped block window, including one from yesterday."""
        if not self.daemon.settings.get("prayer_block_enabled", False):
            return None
        mins_before = self.daemon.settings.get("prayer_minutes_before", 10)
        mins_after = self.daemon.settings.get("prayer_minutes_after", 30)
        skipped = self.daemon.settings.get("prayer_skipped", {})

        prayers = self._get_prayer_times_for_date(now)
        prayers.extend(self._get_prayer_times_for_date(now - timedelta(days=1)))
        for prayer in prayers:
            start = prayer["time"] - timedelta(minutes=mins_before)
            end = prayer["time"] + timedelta(minutes=mins_after)
            if start <= now <= end and self._skip_key(prayer) not in skipped:
                return {"name": prayer["name"], "start": start, "end": end}
        return None

    def cmd_get_prayer(self) -> dict:
        now = datetime.now()
        prayers = self._get_prayer_times_for_date(now)
        skipped = self.daemon.settings.get("prayer_skipped", {})
        next_prayer = None
        upcoming = self._upcoming_prayers(now)
        if upcoming:
            prayer = upcoming[0]
            next_prayer = {
                "name": prayer["name"],
                "time": prayer["time"].isoformat(),
                "is_skipped": self._skip_key(prayer) in skipped,
            }
                
        all_prayers = []
        for p in prayers:
            p_skip_key = self._skip_key(p)
            all_prayers.append({
                "name": p["name"],
                "time": p["time"].isoformat(),
                "is_skipped": p_skip_key in skipped
            })

        return {
            "status": "ok",
            "enabled": self.daemon.settings.get("prayer_block_enabled", False),
            "next_prayer": next_prayer,
            "all_prayers": all_prayers,
            "active_ban": getattr(self.daemon, "prayer_ban_active", "")
        }


    def cmd_skip_prayer(self, cmd: dict) -> dict:
        prayer_name = cmd.get("prayer_name")
        cancel = cmd.get("cancel", False)
        logging.debug(
            "Prayer skip request received (prayer=%s, cancel=%s).",
            prayer_name,
            bool(cancel),
        )
        
        if not prayer_name:
            return {"status": "error", "message": "Missing prayer_name"}
            
        if not self.daemon.settings.get("prayer_block_enabled", False):
            return {"status": "error", "message": "Prayer blocking is disabled."}

        now = datetime.now()
        target = next(
            (p for p in self._upcoming_prayers(now) if p["name"] == prayer_name), None
        )
        
        if not target:
            return {"status": "error", "message": "Prayer is not an upcoming prayer."}
            
        skip_key = self._skip_key(target)
        skipped = self.daemon.settings.get("prayer_skipped", {})
        
        if cancel:
            if skip_key in skipped:
                del skipped[skip_key]
                self.daemon.settings["prayer_skipped"] = skipped
                if not self.daemon.settings_manager.save_settings(self.daemon.settings):
                    return {"status": "error", "message": "Failed to save prayer skip."}
                self.daemon.notifications_manager.broadcast_state_changed()
            return {"status": "ok"}
            
        # Check strict 30-minute rule
        time_diff = (target["time"] - now).total_seconds() / 60.0
        if time_diff <= 30:
            return {"status": "error", "message": "Too late to skip. Can only skip > 30 minutes before prayer."}
            
        # Register skip
        skipped[skip_key] = now.isoformat()
        
        # Clean up old skips
        yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        keys_to_delete = [k for k in skipped.keys() if k < yesterday]
        for k in keys_to_delete:
            del skipped[k]
            
        self.daemon.settings["prayer_skipped"] = skipped
        try:
            logging.info("Saving settings with new skipped: %s", skipped)
            if not self.daemon.settings_manager.save_settings(self.daemon.settings):
                return {"status": "error", "message": "Failed to save prayer skip."}
            self.daemon.notifications_manager.broadcast_state_changed()
            logging.info("Successfully skipped prayer.")
            return {"status": "ok"}
        except Exception as exc:
            logging.error("Exception in cmd_skip_prayer: %s", exc)
            return {"status": "error", "message": f"Failed to save settings: {exc}"}


    def _settings_fingerprint(self) -> tuple[object, object, object]:
        settings = self.daemon.settings
        return (
            settings.get("prayer_latitude", 0.0),
            settings.get("prayer_longitude", 0.0),
            settings.get("prayer_method", 2),
        )

    def invalidate_calendar(self) -> None:
        """Retire cached coordinates without letting an old fetch win a race."""
        with self._calendar_lock:
            # Keep known times available until the replacement calendar is
            # fetched. This prevents a transient network outage from silently
            # weakening an active Prayer Ban.
            fallback = dict(self._fallback_calendar_cache)
            fallback.update(self._calendar_cache)
            self._fallback_calendar_cache = fallback
            self._calendar_cache = {}
            self._calendar_cache_loaded = True
            self._calendar_generation += 1
            self._refresh_queue.clear()
            self._refresh_pending.clear()
            self._refresh_failures.clear()

    def _fetch_prayer_calendar(
        self,
        year: int,
        month: int,
        fingerprint: tuple[object, object, object] | None = None,
    ) -> dict:
        """Fetch prayer calendar from Aladhan API for the given year and month."""
        lat, lon, method = fingerprint or self._settings_fingerprint()
        
        if not lat and not lon:
            logging.warning("Prayer latitude and longitude not set in settings. Skipping Aladhan API fetch.")
            return []
            
        query = urllib.parse.urlencode(
            {"latitude": lat, "longitude": lon, "method": method}
        )
        url = f"https://api.aladhan.com/v1/calendar/{year}/{month}?{query}"
        
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'ForcedFocus/2.0'})
            with urllib.request.urlopen(req, timeout=10.0) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    if data.get("code") == 200:
                        return data.get("data", [])
                else:
                    logging.error("Aladhan API returned status %s", response.status)
        except Exception as exc:
            # The request URL contains configured coordinates. Never include it
            # in logs or diagnostic bundles.
            logging.error("Failed to fetch prayer calendar from AlAdhan: %s", exc)
        return []


    def _load_calendar_cache(self) -> None:
        """Load the durable calendar once; hot status paths use memory afterwards."""
        with self._calendar_lock:
            if self._calendar_cache_loaded:
                return
            cache = {}
            if PRAYER_CACHE_FILE.exists():
                try:
                    loaded = self.daemon.state_store.read_json(PRAYER_CACHE_FILE)
                    if isinstance(loaded, dict):
                        cache = loaded
                except Exception as exc:
                    logging.error("Failed to read the cached prayer calendar: %s", exc)
            self._calendar_cache = cache
            self._calendar_cache_loaded = True

    @staticmethod
    def _simplify_calendar(month_data: list[dict]) -> dict[str, dict[str, str]]:
        simple_data = {}
        for day_data in month_data:
            if not isinstance(day_data, dict):
                continue
            dt = day_data.get("date", {}).get("gregorian", {}).get("day")
            if dt:
                try:
                    dt = str(int(dt)).zfill(2)
                except (TypeError, ValueError):
                    continue
            timings = day_data.get("timings", {})
            if not dt or not isinstance(timings, dict):
                continue
            simple_data[dt] = {
                key: value.split(" ")[0]
                for key, value in timings.items()
                if isinstance(value, str)
            }
        return simple_data

    def _schedule_calendar_refresh(self, year: int, month: int) -> None:
        """Queue a missing month for one background worker with failure backoff."""
        cache_key = f"{year}-{month:02d}"
        now_mono = get_continuous_time()
        worker = None
        with self._calendar_lock:
            generation = self._calendar_generation
            pending_key = (cache_key, generation)
            if self._calendar_cache.get(cache_key) or pending_key in self._refresh_pending:
                return
            _failures, retry_at = self._refresh_failures.get(cache_key, (0, 0.0))
            if now_mono < retry_at:
                return
            fingerprint = self._settings_fingerprint()
            self._refresh_pending.add(pending_key)
            self._refresh_queue.append((year, month, generation, fingerprint))
            if self._refresh_worker_thread is None or not self._refresh_worker_thread.is_alive():
                worker = threading.Thread(
                    target=self._refresh_calendar_worker,
                    name="prayer-calendar-refresh",
                    daemon=True,
                )
                self._refresh_worker_thread = worker
        if worker is not None:
            worker.start()

    def _refresh_calendar_worker(self) -> None:
        """Fetch queued calendars without holding the daemon's orchestration lock."""
        while True:
            with self._calendar_lock:
                if not self._refresh_queue:
                    self._refresh_worker_thread = None
                    return
                year, month, generation, fingerprint = self._refresh_queue.pop(0)
            cache_key = f"{year}-{month:02d}"
            pending_key = (cache_key, generation)
            with self._calendar_lock:
                if generation != self._calendar_generation:
                    self._refresh_pending.discard(pending_key)
                    continue
            month_data = self._fetch_prayer_calendar(year, month, fingerprint)
            simple_data = self._simplify_calendar(month_data) if month_data else {}

            if simple_data:
                with self._calendar_lock:
                    refresh_is_current = (
                        generation == self._calendar_generation
                        and fingerprint == self._settings_fingerprint()
                    )
                    if not refresh_is_current:
                        self._refresh_pending.discard(pending_key)
                        continue
                    # Never discard last-known data on refresh failure. A
                    # successful refresh atomically replaces only its month.
                    self._calendar_cache[cache_key] = simple_data
                    self._fallback_calendar_cache.pop(cache_key, None)
                    self._refresh_failures.pop(cache_key, None)
                    cache_snapshot = dict(self._calendar_cache)
                try:
                    self.daemon._atomic_write_json(
                        PRAYER_CACHE_FILE, cache_snapshot, indent=2
                    )
                except Exception as exc:
                    # The in-memory calendar remains usable for this process.
                    logging.error("Failed to persist the prayer calendar cache: %s", exc)
                notifications = getattr(self.daemon, "notifications_manager", None)
                if notifications is not None:
                    notifications.broadcast_state_changed()
            else:
                with self._calendar_lock:
                    refresh_is_current = (
                        generation == self._calendar_generation
                        and fingerprint == self._settings_fingerprint()
                    )
                    if not refresh_is_current:
                        self._refresh_pending.discard(pending_key)
                        continue
                    failures, _retry_at = self._refresh_failures.get(
                        cache_key, (0, 0.0)
                    )
                    failures += 1
                    delay = min(
                        self._REFRESH_BACKOFF_INITIAL_SECONDS * (2 ** (failures - 1)),
                        self._REFRESH_BACKOFF_MAX_SECONDS,
                    )
                    self._refresh_failures[cache_key] = (
                        failures,
                        get_continuous_time() + delay,
                    )
            with self._calendar_lock:
                self._refresh_pending.discard(pending_key)


    def _get_prayer_times_for_date(self, now: datetime) -> list[dict]:
        """Return cached prayer times and refresh missing months asynchronously."""
        cache_key = f"{now.year}-{now.month:02d}"
        day_key = f"{now.day:02d}"
        self._load_calendar_cache()
        with self._calendar_lock:
            month_cache = self._calendar_cache.get(cache_key, {})
            fallback_cache = self._fallback_calendar_cache.get(cache_key, {})
        if not month_cache:
            self._schedule_calendar_refresh(now.year, now.month)
            month_cache = fallback_cache
        
        # Now extract the required prayers
        required_prayers = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
        times = []
        day_data = month_cache.get(day_key, {})
        if day_data:
            for p in required_prayers:
                if p in day_data:
                    t_str = day_data[p]
                    # Parse time
                    try:
                        hour, minute = map(int, t_str.split(":"))
                        p_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                        times.append({"name": p, "time": p_time})
                    except Exception:
                        pass
        return times


    def _evaluate_prayer_block(self, now: datetime) -> tuple[bool, str]:
        """Check if we are currently in a prayer block window."""
        active = self.active_prayer_window(now)
        return (True, active["name"]) if active else (False, "")
