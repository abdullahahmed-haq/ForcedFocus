import logging
import threading
import uuid
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from forcefocus.constants import *
from forcefocus.utils import get_continuous_time
from forcefocus.events import Event

class CoreMixin:
    @staticmethod
    def _normalize_status(response: dict) -> dict:
        """Keep the status response stable for all supported clients.

        Clients have historically consumed a mixture of legacy and newer
        fields.  This normalizer preserves the legacy response while ensuring
        the documented timer fields are always available and numeric.
        """
        total_seconds = max(0, int(response.get("total_duration_seconds", 0) or 0))
        response["total_duration_seconds"] = total_seconds
        response["duration_minutes"] = (total_seconds + 59) // 60
        response["remaining_seconds"] = max(
            0, int(response.get("remaining_seconds", 0) or 0)
        )
        response["pomo_phase_remaining"] = max(
            0, int(response.get("pomo_phase_remaining", 0) or 0)
        )
        response["pomo_phase_total"] = max(
            0, int(response.get("pomo_phase_total", 0) or 0)
        )
        return response

    def _start_session(self, cmd: dict) -> dict:
            duration_minutes = cmd.get("duration_minutes", 120)
            mode = cmd.get("mode", "blacklist")
            session_type = cmd.get("session_type", "standard")
            is_sleep = (
                cmd.get("_sleep") is True
                and cmd.get("_sleep_authority") is self.daemon.sleep_schedule_manager
            )
            # D3: Validate inputs before acquiring lock
            try:
                if isinstance(duration_minutes, bool):
                    raise ValueError
                duration_minutes = int(duration_minutes)
            except (TypeError, ValueError):
                return {"status": "error", "message": "Invalid duration."}
            if duration_minutes < 1 or duration_minutes > 1440:
                return {"status": "error", "message": "Duration must be 1–1440 minutes."}
            if mode not in ("blacklist", "whitelist", "ban"):
                return {"status": "error", "message": "Invalid mode."}
            if session_type not in ("standard", "pomodoro", "rescue") and not (
                is_sleep and session_type == "sleep"
            ):
                return {"status": "error", "message": "Invalid session type."}
            groups = cmd.get("groups", [])
            if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
                return {"status": "error", "message": "Groups must be a list of names."}
            intent_tasks = cmd.get("intent_tasks", [])
            if not isinstance(intent_tasks, list):
                return {"status": "error", "message": "Intent tasks must be a list."}
            cmd = dict(cmd)
            cmd["duration_minutes"] = duration_minutes
            cmd["session_type"] = session_type
            cmd["groups"] = groups
            cmd["intent_tasks"] = intent_tasks
            if session_type == "pomodoro":
                normalized_pomodoro = {}
                for key, default in (
                    ("focus_minutes", 25),
                    ("break_minutes", 5),
                    ("cycles", 4),
                ):
                    value = cmd.get(key, default)
                    try:
                        if isinstance(value, bool):
                            raise ValueError
                        normalized_pomodoro[key] = int(value)
                    except (TypeError, ValueError):
                        return {"status": "error", "message": f"Invalid {key.replace('_', ' ')}."}
                focus_minutes = normalized_pomodoro["focus_minutes"]
                break_minutes = normalized_pomodoro["break_minutes"]
                cycles = normalized_pomodoro["cycles"]
                if not 1 <= focus_minutes <= 240:
                    return {"status": "error", "message": "Focus minutes must be 1–240."}
                if not 1 <= break_minutes <= 60:
                    return {"status": "error", "message": "Break minutes must be 1–60."}
                if not 1 <= cycles <= 50:
                    return {"status": "error", "message": "Cycles must be 1–50."}
                if (focus_minutes + break_minutes) * cycles > 1440:
                    return {"status": "error", "message": "Pomodoro duration must be 1440 minutes or less."}
                cmd.update(normalized_pomodoro)
            with self.daemon.lock:
                # Parse scheduling arguments
                schedule_in = cmd.get("schedule_in_minutes")
                schedule_at = cmd.get("schedule_at_time")
                start_time = None
                if schedule_in:
                    start_time = datetime.now() + timedelta(minutes=int(schedule_in))
                elif schedule_at:
                    try:
                        now = datetime.now()
                        formats = [
                            "%Y-%m-%dT%H:%M",  # HTML5 datetime-local
                            "%Y-%m-%d %H:%M",  # CLI basic
                            "%Y-%m-%d %I:%M %p",  # CLI AM/PM
                            "%Y-%m-%d %I:%M%p",
                            "%I:%M %p",  # Just time AM/PM
                            "%I:%M%p",
                            "%H:%M",  # Just time 24h
                        ]
                        for fmt in formats:
                            try:
                                parsed = datetime.strptime(schedule_at.strip(), fmt)
                                if parsed.year == 1900:
                                    start_time = now.replace(
                                        hour=parsed.hour,
                                        minute=parsed.minute,
                                        second=0,
                                        microsecond=0,
                                    )
                                    if start_time <= now:
                                        start_time += timedelta(days=1)
                                else:
                                    start_time = parsed
                                break
                            except ValueError:
                                continue
    
                        if not start_time:
                            return {
                                "status": "error",
                                "message": "Invalid date/time format. Use 'YYYY-MM-DD HH:MM AM/PM' or 'HH:MM AM/PM'.",
                            }
    
                    except Exception as exc:
                        return {
                            "status": "error",
                            "message": f"Failed to parse schedule time: {exc}",
                        }
    
                # duration_minutes already validated before lock acquisition
    
                is_scheduling = start_time and start_time > datetime.now()
    
                # Check overlap if active
                if self.daemon.state.session.active:
                    if self.daemon.state.session.session_type == "sleep" and not is_scheduling:
                        return {"status": "error", "message": "Session conflicts with the active Sleep Schedule."}
                    if not is_scheduling:
                        merge_snapshot = {
                            "session_expiry": self.daemon.state.session.session_expiry,
                            "total_duration_seconds": self.daemon.state.session.total_duration_seconds,
                            "session_groups": list(self.daemon.state.session.session_groups),
                            "session_base_domains": list(self.daemon.session_base_domains),
                            "active_domains": list(self.daemon.state.active_domains),
                            "active_domains_set": set(self.daemon.active_domains_set),
                            "mono_session_end": self.daemon._mono_session_end,
                        }
                        immediate_end = datetime.now() + timedelta(minutes=duration_minutes)
                        if not is_sleep and self.daemon.sleep_schedule_manager.conflicts_interval(
                            datetime.now(), immediate_end
                        ):
                            return {
                                "status": "error",
                                "message": "Session overlaps with Sleep Schedule.",
                            }
                        if cmd.get("scheduled_execution"):
                            return {
                                "status": "error",
                                "message": "Scheduled session conflicts with the currently active session.",
                            }
                        if self.daemon.state.session.session_type != session_type:
                            return {"status": "error", "message": "Cannot merge different session types (e.g. standard and pomodoro)."}
                        if self.daemon.state.session.mode != mode:
                            return {"status": "error", "message": "Cannot merge different modes (whitelist/blacklist)."}
    
                        new_expiry = datetime.now() + timedelta(minutes=duration_minutes)
                        added_minutes = 0
                        if new_expiry > self.daemon.state.session.session_expiry:
                            added_minutes = int((new_expiry - self.daemon.state.session.session_expiry).total_seconds() / 60)
                            self.daemon.state.session.session_expiry = new_expiry
                            self.daemon._mono_session_end = get_continuous_time() + (duration_minutes * 60)
                            self.daemon.state.session.total_duration_seconds = max(self.daemon.state.session.total_duration_seconds, duration_minutes * 60)
    
                        # Merge groups
                        selected_groups = cmd.get("groups", [])
                        if selected_groups:
                            self.daemon.state.session.session_groups = list(set(self.daemon.state.session.session_groups + selected_groups))
                            groups = self.daemon.domains_manager.load_groups()
                            new_domains = []
                            for gname in selected_groups:
                                if gname in groups:
                                    new_domains.extend(groups[gname])
                            
                            if self.daemon.state.session.mode == "blacklist":
                                self.daemon.session_base_domains.extend(new_domains)
                                self.daemon.session_base_domains = list(set(d.strip().lower() for d in self.daemon.session_base_domains if d.strip() and "." in d))
                                
                                new_expanded = self.daemon.domains_manager.get_blacklist_domains(selected_groups)
                                self.daemon.state.active_domains.extend(new_expanded)
                                self.daemon.state.active_domains = list(set(self.daemon.state.active_domains))
                                self.daemon.active_domains_set = set(self.daemon.state.active_domains)
                            # For whitelist, adding domains makes it less restrictive. 
                            # We skip expanding the whitelist during a merge to enforce strictness.
    
                        if not self.daemon._persist_session_lock():
                            self.daemon.state.session.session_expiry = merge_snapshot["session_expiry"]
                            self.daemon.state.session.total_duration_seconds = merge_snapshot["total_duration_seconds"]
                            self.daemon.state.session.session_groups = merge_snapshot["session_groups"]
                            self.daemon.session_base_domains = merge_snapshot["session_base_domains"]
                            self.daemon.state.active_domains = merge_snapshot["active_domains"]
                            self.daemon.active_domains_set = merge_snapshot["active_domains_set"]
                            self.daemon._mono_session_end = merge_snapshot["mono_session_end"]
                            return {
                                "status": "error",
                                "message": "Failed to persist the merged session.",
                            }
                        if selected_groups and self.daemon.state.session.mode == "blacklist":
                            self.daemon.events.emit(Event.SESSION_STARTED)
                        self.daemon.notifications_manager.broadcast_state_changed()
                        
                        msg = f"Session merged. Extended by {added_minutes} minutes." if added_minutes > 0 else "Session merged. Constraints updated."
                        logging.info(msg)
                        return {
                            "status": "ok",
                            "message": msg,
                            "mode": self.daemon.state.session.mode,
                            "domains_count": len(self.daemon.state.active_domains),
                            "expires_at": self.daemon.state.session.session_expiry.strftime("%H:%M:%S"),
                            "event": "merged",
                            "added_minutes": added_minutes
                        }
                    else:
                        # Allow scheduling even if it overlaps. It will be merged when it executes.
                        pass
    
                if is_scheduling:
                    end_time = start_time + timedelta(minutes=duration_minutes)

                    if self.daemon.sleep_schedule_manager.conflicts_interval(start_time, end_time):
                        return {
                            "status": "error",
                            "message": "Schedule overlaps with Sleep Schedule.",
                        }

                    if (
                        self.daemon.state.session.active
                        and self.daemon.state.session.session_expiry
                        and start_time < self.daemon.state.session.session_expiry
                    ):
                        return {
                            "status": "error",
                            "message": "Schedule overlaps with the currently active session.",
                        }

                    # Check overlap with existing schedules
                    for sch in self.daemon.schedules:
                        if max(start_time, sch["start_time"]) < min(
                            end_time, sch["end_time"]
                        ):
                            return {
                                "status": "error",
                                "message": f"Schedule overlaps with an existing schedule (starts at {sch['start_time'].strftime('%m-%d %H:%M')}).",
                            }
                    if self.daemon.schedules_manager.oneoff_conflicts_with_recurring(
                        start_time, end_time
                    ):
                        return {
                            "status": "error",
                            "message": "Schedule overlaps with an active recurring schedule.",
                        }

                    sch_cmd = cmd.copy()
                    sch_cmd.pop("schedule_in_minutes", None)
                    sch_cmd.pop("schedule_at_time", None)
    
                    mono_start = get_continuous_time() + (start_time - datetime.now()).total_seconds()
                    scheduled_entry = {
                        "start_time": start_time,
                        "end_time": end_time,
                        "mono_start": mono_start,
                        "cmd": sch_cmd,
                    }
                    self.daemon.schedules.append(scheduled_entry)
                    self.daemon.schedules.sort(key=lambda x: x["start_time"])
                    if not self.daemon._persist_session_lock():
                        self.daemon.schedules.remove(scheduled_entry)
                        return {
                            "status": "error",
                            "message": "Failed to persist the scheduled session.",
                        }
    
                    logging.info(
                        "Session scheduled to start at %s.",
                        start_time.strftime("%Y-%m-%d %I:%M %p"),
                    )
                    return {
                        "status": "ok",
                        "message": f"Session scheduled to start at {start_time.strftime('%Y-%m-%d %I:%M %p')}.",
                        "scheduled": True,
                        "starts_at": start_time.strftime("%Y-%m-%d %I:%M %p"),
                    }

                if not is_sleep:
                    immediate_end = datetime.now() + timedelta(minutes=duration_minutes)
                    if self.daemon.sleep_schedule_manager.conflicts_interval(
                        datetime.now(), immediate_end
                    ):
                        return {
                            "status": "error",
                            "message": "Session overlaps with Sleep Schedule.",
                        }

                self.daemon.state.session.mode = mode
                self.daemon.state.session.session_type = session_type
                self.daemon.state.session.intent = (
                    cmd.get("intent", None) or self.daemon.state.session.intent
                )  # Keep existing intent if set via /api/intent and not provided in start
                self.daemon.state.session.intent_tasks = (
                    cmd.get("intent_tasks", None) or self.daemon.state.session.intent_tasks
                )
                sleep_wake = cmd.get("_sleep_wake") if is_sleep else None
                if is_sleep and not isinstance(sleep_wake, datetime):
                    return {"status": "error", "message": "Invalid Sleep Schedule wake time."}
                self.daemon.state.session.session_expiry = sleep_wake or (
                    datetime.now() + timedelta(minutes=duration_minutes)
                )
                self.daemon.state.session.sleep_occurrence = (
                    cmd.get("_sleep_occurrence") if is_sleep else None
                )
                if not self.daemon.state.session.active:
                    self.daemon.state.session.session_group_id = str(uuid.uuid4())
                self.daemon.state.session.active = True
                self.daemon.state.session.total_duration_seconds = int(
                    (self.daemon.state.session.session_expiry - datetime.now()).total_seconds()
                ) if is_sleep else duration_minutes * 60
                self.daemon.state.session.pending_unlock_at = None
                # Monotonic anchors
                now_mono = get_continuous_time()
                self.daemon._mono_session_end = now_mono + max(
                    0, (self.daemon.state.session.session_expiry - datetime.now()).total_seconds()
                ) if is_sleep else now_mono + (duration_minutes * 60)
                self.daemon._mono_unlock_end = 0.0
                self.daemon._mono_last_intent_notif = now_mono
    
                # Extract pomodoro params from command
                if self.daemon.state.session.session_type == "pomodoro":
                    self.daemon.state.pomodoro.pomo_focus_minutes = cmd.get("focus_minutes", 25)
                    self.daemon.state.pomodoro.pomo_break_minutes = cmd.get("break_minutes", 5)
                    self.daemon.state.pomodoro.pomo_total_cycles = cmd.get("cycles", 4)
                    self.daemon.state.pomodoro.pomo_current_cycle = 1
                    self.daemon.state.pomodoro.pomo_phase = "focus"
                    self.daemon.state.pomodoro.pomo_phase_expiry = datetime.now() + timedelta(
                        minutes=self.daemon.state.pomodoro.pomo_focus_minutes
                    )
                    self.daemon._mono_pomo_phase_end = now_mono + (self.daemon.state.pomodoro.pomo_focus_minutes * 60)
                    # S7: Override duration with exact Pomodoro calculation to prevent timer divergence
                    pomo_total = (
                        self.daemon.state.pomodoro.pomo_focus_minutes + self.daemon.state.pomodoro.pomo_break_minutes
                    ) * self.daemon.state.pomodoro.pomo_total_cycles
                    duration_minutes = pomo_total
                    self.daemon.state.session.total_duration_seconds = pomo_total * 60
                    self.daemon.state.session.session_expiry = datetime.now() + timedelta(minutes=pomo_total)
                    self.daemon._mono_session_end = now_mono + (pomo_total * 60)
    
                # MEDIUM #1 fix: Use self.daemon.state.session.session_expiry (post-Pomodoro override)
                # instead of the stale local `expiry` variable.
                session_data = {
                    "started": datetime.now().isoformat(),
                    "expiry": self.daemon.state.session.session_expiry.isoformat(),
                    "mode": mode,
                    "duration_minutes": duration_minutes,
                    "session_type": self.daemon.state.session.session_type,
                    "sleep_occurrence": self.daemon.state.session.sleep_occurrence,
                    "pomo_focus_minutes": self.daemon.state.pomodoro.pomo_focus_minutes,
                    "pomo_break_minutes": self.daemon.state.pomodoro.pomo_break_minutes,
                    "pomo_total_cycles": self.daemon.state.pomodoro.pomo_total_cycles,
                    "pomo_current_cycle": self.daemon.state.pomodoro.pomo_current_cycle,
                    "pomo_phase": self.daemon.state.pomodoro.pomo_phase,
                    "pomo_phase_expiry": (
                        self.daemon.state.pomodoro.pomo_phase_expiry.isoformat()
                        if self.daemon.state.pomodoro.pomo_phase_expiry
                        else None
                    ),
                    "settings": self.daemon.settings,
                    "mono_elapsed": 0.0,
                    "last_persist_wall": datetime.now().isoformat(),
                    "schedules": [
                        {
                            "start_time": sch["start_time"].isoformat(),
                            "end_time": sch["end_time"].isoformat(),
                            "cmd": sch["cmd"],
                        }
                        for sch in self.daemon.schedules
                    ],
                    "recurring_schedules": self.daemon.recurring_schedules,
                }
                self.daemon.remaining_seconds = duration_minutes * 60
                self.daemon.pending_unlock_seconds = 0
                if self.daemon.state.session.session_type == "pomodoro":
                    self.daemon.pomo_phase_remaining = self.daemon.state.pomodoro.pomo_focus_minutes * 60
    
                selected_groups = cmd.get("groups", [])
                self.daemon.state.session.session_groups = list(selected_groups)
                if mode in ("whitelist", "ban"):
                    # Original DNS is saved by the enforcement manager itself on SESSION_STARTED
                    if is_sleep:
                        wl_domains = list(cmd.get("_sleep_base_domains", []))
                    elif self.daemon.state.session.session_type == "rescue" or mode == "ban":
                        wl_domains = []
                    else:
                        wl_domains = self.daemon.domains_manager.load_lists().get("whitelist", [])
                        if selected_groups:
                            groups = self.daemon.domains_manager.load_groups()
                            for gname in selected_groups:
                                if gname in groups:
                                    wl_domains.extend(groups[gname])
                    self.daemon.session_base_domains = list(
                        set(d.strip().lower() for d in wl_domains if d.strip())
                    )
    
                    # Whitelist mode: active_domains holds the ALLOW-list.
                    if is_sleep:
                        wl_domains_expanded = list(cmd.get("_sleep_active_domains", []))
                    elif self.daemon.state.session.session_type == "rescue" or mode == "ban":
                        wl_domains_expanded = []
                    else:
                        wl_domains_expanded = self.daemon.domains_manager.expand_whitelist_domains(wl_domains)
                    self.daemon.state.active_domains = wl_domains_expanded
                    self.daemon.active_domains_set = set(self.daemon.state.active_domains)
                    count = len(wl_domains)
                    expanded_count = len(wl_domains_expanded)
                    self.daemon.whitelist_count = count
                    self.daemon.whitelist_expanded_count = expanded_count
                    session_data["active_domains"] = self.daemon.state.active_domains
                    session_data["session_base_domains"] = self.daemon.session_base_domains
                    session_data["original_dns"] = self.daemon.original_dns
                    session_data["whitelist_count"] = count
                    session_data["whitelist_expanded_count"] = expanded_count
                    try:
                        self.daemon._atomic_write_json(SESSION_LOCK, session_data)
                    except Exception:
                        self._rollback_failed_session_start()
                        raise
                    self.daemon.events.emit(Event.SESSION_STARTED)
                    if is_sleep:
                        msg = f"Sleep Schedule active until {self.daemon.state.session.session_expiry.strftime('%H:%M')}."
                    elif self.daemon.state.session.session_type == "pomodoro":
                        msg = f"Pomodoro (Whitelist): {count} domains allowed ({expanded_count} total with CDNs) for {self.daemon.state.pomodoro.pomo_total_cycles} cycles."
                    elif self.daemon.state.session.session_type == "rescue":
                        msg = f"Rescue Throne activated: All sites blocked for {duration_minutes} min."
                    elif mode == "ban":
                        if self.daemon.state.session.session_type == "pomodoro":
                            msg = f"Pomodoro (Ban): All sites blocked for {self.daemon.state.pomodoro.pomo_total_cycles} cycles."
                        else:
                            msg = f"Ban mode: All sites blocked for {duration_minutes} min."
                    else:
                        msg = f"Whitelist mode: {count} domains allowed ({expanded_count} total with CDNs) for {duration_minutes} min."
                else:
                    # Build base domain list (for Chrome extension — no subdomain expansion)
                    base_bl = list(cmd.get("_sleep_base_domains", [])) if is_sleep else list(
                        self.daemon.domains_manager.load_lists().get("blacklist", [])
                    )
                    if selected_groups:
                        groups = self.daemon.domains_manager.load_groups()
                        for gname in selected_groups:
                            if gname in groups:
                                base_bl.extend(groups[gname])
                    self.daemon.session_base_domains = list(
                        set(d.strip().lower() for d in base_bl if d.strip() and "." in d)
                    )
                    # Build expanded domain list (for /etc/hosts — needs explicit subdomain entries)
                    self.daemon.state.active_domains = list(cmd.get("_sleep_active_domains", [])) if is_sleep else self.daemon.domains_manager.get_blacklist_domains(selected_groups)
                    self.daemon.active_domains_set = set(self.daemon.state.active_domains)
                    session_data["active_domains"] = self.daemon.state.active_domains
                    session_data["session_base_domains"] = self.daemon.session_base_domains
                    try:
                        self.daemon._atomic_write_json(SESSION_LOCK, session_data)
                    except Exception:
                        self._rollback_failed_session_start()
                        raise
                    self.daemon.events.emit(Event.SESSION_STARTED)
                    count = len(self.daemon.state.active_domains)
                    if is_sleep:
                        msg = f"Sleep Schedule active until {self.daemon.state.session.session_expiry.strftime('%H:%M')}."
                    elif self.daemon.state.session.session_type == "pomodoro":
                        msg = f"Pomodoro (Blacklist): {count} domains blocked for {self.daemon.state.pomodoro.pomo_total_cycles} cycles."
                    else:
                        msg = f"Blacklist mode: {count} domains blocked for {duration_minutes} min."

                # Prayer has higher priority than every regular session mode. A
                # session that starts while Prayer is active is immediately
                # suspended, so its timer begins only when Prayer ends.
                if is_sleep and getattr(self.daemon, "prayer_ban_active", ""):
                    # Sleep's fixed deadline continues, but Prayer remains the
                    # active enforcement overlay until its own window ends.
                    self.daemon.watchdog_manager._enforce_prayer_ban()
                elif getattr(self.daemon, "prayer_ban_active", ""):
                    self.daemon.watchdog_manager._suspend_session_for_prayer(
                        now_mono, datetime.now()
                    )
    
                logging.info(
                    "Session started (%s) — expires %s.",
                    mode,
                    self.daemon.state.session.session_expiry.strftime("%H:%M:%S"),
                )
                # Centralized sound + notification for ALL session starts
                if is_sleep:
                    self.daemon.notifications_manager.play_sound("scheduled")
                elif self.daemon.state.session.session_type == "rescue":
                    self.daemon.notifications_manager.play_sound("rescue")
                    self.daemon.notifications_manager.send_mac_notification(
                        "Rescue Mode",
                        f"All sites blocked for {duration_minutes} min. Stay focused!",
                    )
                else:
                    self.daemon.notifications_manager.play_sound("start")
                    self.daemon.notifications_manager.send_mac_notification(
                        "Session Started",
                        msg,
                        subtitle=self.daemon.state.session.session_expiry.strftime("Expires at %H:%M"),
                    )
                self.daemon.notifications_manager.broadcast_state_changed()
                return {
                    "status": "ok",
                    "message": msg,
                    "mode": mode,
                    "domains_count": count,
                    "expires_at": self.daemon.state.session.session_expiry.strftime("%H:%M:%S"),
                }

    def _request_stop(self, passphrase: str) -> dict:
            with self.daemon.lock:
                if not self.daemon.state.session.active:
                    return {"status": "ok", "message": "No active session."}
                # Rate limit passphrase attempts
                now_mono = time.monotonic()
                if self.daemon._passphrase_attempts >= 5:
                    cooldown = min(60, 2 ** (self.daemon._passphrase_attempts - 5))
                    elapsed = now_mono - self.daemon._last_attempt_time
                    if elapsed < cooldown:
                        wait = int(cooldown - elapsed)
                        logging.warning("Passphrase rate-limited. %ds remaining.", wait)
                        return {
                            "status": "error",
                            "message": f"Too many attempts. Wait {wait}s.",
                        }
                self.daemon._last_attempt_time = now_mono
                if not self.daemon._verify_passphrase(passphrase):
                    self.daemon._passphrase_attempts += 1
                    logging.warning(
                        "Invalid kill-switch passphrase attempt (#%d).",
                        self.daemon._passphrase_attempts,
                    )
                    return {"status": "error", "message": "Invalid passphrase."}
                # Reset rate limiter on success
                self.daemon._passphrase_attempts = 0
                if self.daemon.state.session.pending_unlock_at:
                    now_mono = get_continuous_time()
                    rem_mono = self.daemon._mono_unlock_end - now_mono
                    if rem_mono > 0:
                        return {
                            "status": "pending",
                            "message": f"Unlock already pending. {int(rem_mono/60)}m {int(rem_mono%60)}s remaining.",
                        }
                previous_unlock_at = self.daemon.state.session.pending_unlock_at
                previous_mono_unlock_end = self.daemon._mono_unlock_end
                self.daemon.state.session.pending_unlock_at = datetime.now() + timedelta(
                    seconds=DELAYED_UNLOCK_S
                )
                self.daemon._mono_unlock_end = get_continuous_time() + DELAYED_UNLOCK_S
                if not self.daemon._persist_session_lock():
                    self.daemon.state.session.pending_unlock_at = previous_unlock_at
                    self.daemon._mono_unlock_end = previous_mono_unlock_end
                    return {
                        "status": "error",
                        "message": "Failed to persist the unlock request.",
                    }
                self.daemon.notifications_manager.play_sound("unlock")
                self.daemon.notifications_manager.broadcast_state_changed()
                unlock_str = self.daemon.state.session.pending_unlock_at.strftime("%H:%M:%S")
                logging.info("Delayed unlock requested — scheduled at %s.", unlock_str)
                return {
                    "status": "pending",
                    "message": f"Unlock request accepted. Releases at {unlock_str} (20-min delay).",
                }

    def _cancel_stop(self) -> dict:
            with self.daemon.lock:
                if not self.daemon.state.session.active:
                    return {"status": "error", "message": "No active session."}
                if not self.daemon.state.session.pending_unlock_at:
                    return {"status": "error", "message": "No unlock pending."}
                previous_unlock_at = self.daemon.state.session.pending_unlock_at
                previous_mono_unlock_end = self.daemon._mono_unlock_end
                self.daemon.state.session.pending_unlock_at = None
                self.daemon._mono_unlock_end = 0.0
                if not self.daemon._persist_session_lock():
                    self.daemon.state.session.pending_unlock_at = previous_unlock_at
                    self.daemon._mono_unlock_end = previous_mono_unlock_end
                    return {
                        "status": "error",
                        "message": "Failed to persist the cancellation.",
                    }
                self.daemon.notifications_manager.broadcast_state_changed()
                logging.info("Pending unlock cancelled. Focus session continues.")
                return {"status": "ok", "message": "Unlock request cancelled. Continuing focus."}

    def _remove_block(self):
            """Remove blocking from /etc/hosts without ending the session."""
            try:
                subprocess.run(
                    ["chflags", "nouchg", str(HOSTS_PATH)], capture_output=True, timeout=5
                )
                self.daemon.events.emit(Event.SESSION_ENDED)
            except Exception as exc:
                logging.error("_remove_block error: %s", exc)

    def _rollback_failed_session_start(self) -> None:
            """Restore the idle in-memory state when the commitment was not durable."""
            self.daemon.state.session.reset()
            self.daemon.state.pomodoro.reset()
            self.daemon.state.active_domains = []
            self.daemon.active_domains_set = set()
            self.daemon.session_base_domains = []
            self.daemon.whitelist_count = 0
            self.daemon.whitelist_expanded_count = 0
            self.daemon._mono_session_end = 0.0
            self.daemon._mono_unlock_end = 0.0
            self.daemon._mono_pomo_phase_end = 0.0

    def _cleanup_session(self):
            """Teardown active session completely."""
            with self.daemon.enforcement_lock:
                logging.info("Cleaning up session (mode=%s)...", self.daemon.state.session.mode)
                self.daemon.notifications_manager.play_sound("end")
                self.daemon.notifications_manager.send_mac_notification(
                    "Session Complete", "Great job! Your ForcedFocus session has ended."
                )
                self.daemon.state.session.active = False  # Ensure firewall logic knows session is ending
                was_whitelist = self.daemon.state.session.mode in ("whitelist", "ban")
                was_sleep = self.daemon.state.session.session_type == "sleep"
                sleep_occurrence = self.daemon.state.session.sleep_occurrence
                now_wall = datetime.now()
                now_mono = get_continuous_time()
                sleep_ended_early = (
                    was_sleep
                    and self.daemon.state.session.pending_unlock_at is not None
                    and self.daemon.state.session.session_expiry is not None
                    and now_wall < self.daemon.state.session.session_expiry
                    and now_mono < self.daemon._mono_session_end
                )
    
                try:
                    subprocess.run(
                        ["chflags", "nouchg", str(HOSTS_PATH)], capture_output=True, timeout=5
                    )
                    self.daemon.events.emit(Event.SESSION_ENDED)
                except Exception as exc:
                    logging.error("cleanup_session error: %s", exc)
    
                # Record session history BEFORE resetting state
                try:
                    if not was_sleep:
                        self.daemon.history_manager.record_session_history()
                except Exception as exc:
                    logging.error("Failed to record session history: %s", exc)

                # Suppress before deleting the session lock so a restart cannot
                # restart the occurrence after an approved early stop.
                if sleep_ended_early:
                    self.daemon.sleep_schedule_manager.suppress_current_occurrence(sleep_occurrence)
                elif was_sleep:
                    self.daemon.sleep_schedule_manager.promote_pending_if_due(now_wall)


                if self.daemon.schedules or self.daemon.recurring_schedules:
                    self.daemon._persist_session_lock()
                else:
                    SESSION_LOCK.unlink(missing_ok=True)
    
                self.daemon.hosts_hash = None
                self.daemon._hosts_stat = None
                self.daemon.state.session.session_expiry = None
                self.daemon.state.session.pending_unlock_at = None
                self.daemon.state.active_domains = []
                self.daemon.active_domains_set = set(self.daemon.state.active_domains)
                self.daemon._ip_backlog.clear()
                self.daemon._whitelisted_ip_backlog.clear()
                self.daemon.session_base_domains = []
                self.daemon.original_dns = {}
                self.daemon.whitelist_resolved = {}
                self.daemon.whitelist_count = 0
                self.daemon.whitelist_expanded_count = 0
                self.daemon.state.session.total_duration_seconds = 0
                self.daemon.state.session.mode = "blacklist"
                self.daemon.state.session.session_type = "standard"
                self.daemon.state.pomodoro.pomo_focus_minutes = 0
                self.daemon.state.pomodoro.pomo_break_minutes = 0
                self.daemon.state.pomodoro.pomo_total_cycles = 0
                self.daemon.state.pomodoro.pomo_current_cycle = 0
    
                self.daemon._reenforce_flag = False
                self.daemon.state.pomodoro.pomo_phase = "focus"
                self.daemon.state.pomodoro.pomo_phase_expiry = None
                self.daemon.state.pomodoro.pomo_phases_tracked_seconds = 0
                self.daemon.state.session.session_group_id = None
                self.daemon.state.session.sleep_occurrence = None
                self.daemon._mono_session_end = 0.0
                self.daemon._mono_unlock_end = 0.0
                self.daemon._mono_pomo_phase_end = 0.0
                self.daemon._passphrase_attempts = 0
                self.daemon.state.session.intent = None
                self.daemon.state.session.intent_tasks = []
                self.daemon.state.session.session_groups = []
                self.daemon.prayer_suspension = None
                self.daemon.notifications_manager.broadcast_state_changed()
                # Do NOT clear schedules on session cleanup!
                logging.info("Session ended. Hosts restored. DNS flushed.")
                # Re-enforce permanent blocks (session cleanup may have modified /etc/hosts)
                # Enforcement manager handles perma_block via SESSION_ENDED event

    def cmd_get_status(self) -> dict:
            with self.daemon.lock:
                now = datetime.now()
                prayers = self.daemon.prayer_manager._get_prayer_times_for_date(now)
                next_prayer = next((p for p in prayers if p["time"] > now), None)
                skipped = self.daemon.settings.get("prayer_skipped", {})
                next_prayer_seconds = None
                if next_prayer:
                    if self.daemon.settings.get("prayer_block_enabled", False):
                        next_prayer_seconds = int(max(0, (next_prayer["time"] - now).total_seconds()))

                schedules_res = []
                recurring_res = self.daemon.schedules_manager.recurring_schedules_response()
                for sch in self.daemon.schedules:
                    starting_in_seconds = max(
                        0, int((sch["start_time"] - now).total_seconds())
                    )
                    schedules_res.append(
                        {
                            "starts_at": sch["start_time"].strftime("%Y-%m-%d %I:%M %p"),
                            "start_time_iso": sch["start_time"].isoformat(),
                            "starting_in_seconds": starting_in_seconds,
                            "mode": sch["cmd"].get("mode", "blacklist"),
                            "session_type": sch["cmd"].get("session_type", "standard"),
                            "duration_minutes": sch["cmd"].get("duration_minutes", 120),
                        }
                    )
                if getattr(self.daemon, "prayer_ban_active", ""):
                    prayer_name = self.daemon.prayer_ban_active
                    now = datetime.now()
                    rem = 0
                    expires_at = ""
                    total_dur = 2400
                    active_window = self.daemon.prayer_manager.active_prayer_window(now)
                    if active_window and active_window["name"] == prayer_name:
                        rem = int(max(0, (active_window["end"] - now).total_seconds()))
                        expires_at = active_window["end"].strftime("%H:%M:%S")
                        total_dur = int(
                            (active_window["end"] - active_window["start"]).total_seconds()
                        )
                    return self._normalize_status({
                        "status": "ok",
                        "active": True,
                        "state": "prayer",
                        "mode": "ban",
                        "expires_at": expires_at,
                        "remaining_seconds": rem,
                        "total_duration_seconds": total_dur,
                        "session_type": "prayer",
                        "intent": f"Prayer Time: {prayer_name}",
                        "schedules": schedules_res,
                        "recurring_schedules": recurring_res,
                        "state_revision": self.daemon.state_revision,
                        "notification_warning": self.daemon.notification_warning,
                        "next_prayer_seconds": next_prayer_seconds,
                        "sleep_schedule": self.daemon.sleep_schedule_manager.status_summary(),
                    })
                if not self.daemon.state.session.active:
                    return self._normalize_status({
                        "status": "ok",
                        "active": False,
                        "state": "idle",
                        "mode": None,
                        "message": "Idle.",
                        "schedules": schedules_res,
                        "recurring_schedules": recurring_res,
                        "state_revision": self.daemon.state_revision,
                        "notification_warning": self.daemon.notification_warning,
                        "next_prayer_seconds": next_prayer_seconds,
                        "sleep_schedule": self.daemon.sleep_schedule_manager.status_summary(),
                    })
                # C3: Use monotonic time for all remaining-seconds fields
                now_mono = get_continuous_time()
                rem = int(max(0, self.daemon._mono_session_end - now_mono))
                # Safety net: if session is expired but watchdog hasn't cleaned up,
                # trigger cleanup now to prevent stuck sessions
                if (
                    rem <= 0
                    and self.daemon._mono_session_end > 0
                    and now_mono >= self.daemon._mono_session_end
                ):
                    logging.warning(
                        "Status safety-net: session expired but not cleaned up. Forcing cleanup."
                    )
                    self._cleanup_session()
                    return self._normalize_status({
                        "status": "ok",
                        "active": False,
                        "state": "idle",
                        "mode": None,
                        "message": "Session expired.",
                        "schedules": schedules_res,
                        "recurring_schedules": recurring_res,
                        "state_revision": self.daemon.state_revision,
                        "notification_warning": self.daemon.notification_warning,
                        "next_prayer_seconds": next_prayer_seconds,
                        "sleep_schedule": self.daemon.sleep_schedule_manager.status_summary(),
                    })
                result = {
                    "status": "ok",
                    "active": True,
                    "state": "pending" if self.daemon.state.session.pending_unlock_at else "active",
                    "mode": self.daemon.state.session.mode,
                    "expires_at": self.daemon.state.session.session_expiry.strftime("%H:%M:%S"),
                    "remaining_seconds": rem,
                    "total_duration_seconds": self.daemon.state.session.total_duration_seconds,
                    "domains_count": (
                        len(self.daemon.state.active_domains)
                        if self.daemon.state.session.mode == "blacklist"
                        else self.daemon.whitelist_count
                    ),
                    "whitelist_total_count": (
                        None if self.daemon.state.session.mode == "blacklist" else self.daemon.whitelist_expanded_count
                    ),
                    "pending_unlock": (
                        self.daemon.state.session.pending_unlock_at.strftime("%H:%M:%S")
                        if self.daemon.state.session.pending_unlock_at
                        else None
                    ),
                    "pending_unlock_seconds": (
                        int(max(0, self.daemon._mono_unlock_end - now_mono))
                        if self.daemon._mono_unlock_end > 0
                        else 0
                    ),
                    "session_type": self.daemon.state.session.session_type,
                    "schedules": schedules_res,
                    "recurring_schedules": recurring_res,
                    "intent": self.daemon.state.session.intent,
                    "intent_tasks": self.daemon.state.session.intent_tasks,
                    "state_revision": self.daemon.state_revision,
                    "notification_warning": self.daemon.notification_warning,
                    "next_prayer_seconds": next_prayer_seconds,
                    "sleep_schedule": self.daemon.sleep_schedule_manager.status_summary(),
                }
                if self.daemon.state.session.session_type == "pomodoro":
                    result["pomo_phase"] = self.daemon.state.pomodoro.pomo_phase
                    result["pomo_current_cycle"] = self.daemon.state.pomodoro.pomo_current_cycle
                    result["pomo_total_cycles"] = self.daemon.state.pomodoro.pomo_total_cycles
                    result["pomo_focus_minutes"] = self.daemon.state.pomodoro.pomo_focus_minutes
                    result["pomo_break_minutes"] = self.daemon.state.pomodoro.pomo_break_minutes
                    if self.daemon.state.pomodoro.pomo_phase_expiry:
                        time_str = self.daemon.state.pomodoro.pomo_phase_expiry.strftime("%I:%M %p").lstrip("0")
                        result["pomo_phase_expiry_time"] = time_str
                    if self.daemon._mono_pomo_phase_end > 0:
                        phase_rem = int(max(0, self.daemon._mono_pomo_phase_end - now_mono))
                        result["pomo_phase_remaining"] = phase_rem
                        result["pomo_phase_total"] = (
                            self.daemon.state.pomodoro.pomo_focus_minutes
                            if self.daemon.state.pomodoro.pomo_phase == "focus"
                            else self.daemon.state.pomodoro.pomo_break_minutes
                        ) * 60
                return self._normalize_status(result)
