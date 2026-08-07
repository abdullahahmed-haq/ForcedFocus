from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
import logging
from datetime import datetime, timedelta
from pathlib import Path

from forcefocus.constants import *
from forcefocus.utils import get_continuous_time

class PrayerManager:
    def __init__(self, daemon):
        self.daemon = daemon

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
        logging.info("cmd_skip_prayer called with: %s", cmd)
        prayer_name = cmd.get("prayer_name")
        cancel = cmd.get("cancel", False)
        
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


    def _fetch_prayer_calendar(self, year: int, month: int) -> dict:
        """Fetch prayer calendar from Aladhan API for the given year and month."""
        lat = self.daemon.settings.get("prayer_latitude", 0.0)
        lon = self.daemon.settings.get("prayer_longitude", 0.0)
        
        if not lat and not lon:
            logging.warning("Prayer latitude and longitude not set in settings. Skipping Aladhan API fetch.")
            return []
            
        method = self.daemon.settings.get("prayer_method", 2)
        url = f"https://api.aladhan.com/v1/calendar/{year}/{month}?latitude={lat}&longitude={lon}&method={method}"
        
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
            logging.error("Failed to fetch prayer calendar from %s: %s", url, exc)
        return []


    def _get_prayer_times_for_date(self, now: datetime) -> list[dict]:
        """Get prayer times for today, fetching from API if not cached."""
        cache_key = f"{now.year}-{now.month:02d}"
        day_key = f"{now.day:02d}"
        
        cache = {}
        if PRAYER_CACHE_FILE.exists():
            try:
                cache = self.daemon.state_store.read_json(PRAYER_CACHE_FILE)
                if cache is None:
                    raise ValueError("prayer cache must contain an object")
            except Exception:
                pass

        if cache_key not in cache or not cache[cache_key]:
            # Fetch and update cache
            month_data = self._fetch_prayer_calendar(now.year, now.month)
            if month_data:
                # Format to a simpler structure
                simple_data = {}
                for day_data in month_data:
                    dt = day_data.get("date", {}).get("gregorian", {}).get("day")
                    if dt:
                        dt = str(int(dt)).zfill(2) # ensure "05" instead of "5"
                    timings = day_data.get("timings", {})
                    # Clean timings (remove timezone string like ' (EEST)')
                    clean_timings = {}
                    for k, v in timings.items():
                        clean_timings[k] = v.split(" ")[0]
                    simple_data[dt] = clean_timings
                cache[cache_key] = simple_data
                try:
                    self.daemon._atomic_write_json(PRAYER_CACHE_FILE, cache)
                except Exception as exc:
                    logging.error("Failed to write prayer cache to %s: %s", PRAYER_CACHE_FILE, exc)
            else:
                logging.error("Prayer calendar API returned empty data, skipping cache update.")
        
        # Now extract the required prayers
        required_prayers = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
        times = []
        day_data = cache.get(cache_key, {}).get(day_key, {})
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
