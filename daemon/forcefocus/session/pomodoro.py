import logging
import threading
import uuid
import time
import json
from pathlib import Path
from datetime import datetime, timedelta
from forcefocus.constants import *
from forcefocus.utils import get_continuous_time
from forcefocus.events import Event

class PomodoroMixin:
    def _transition_pomodoro_phase(self):
            now = datetime.now()
            elapsed_seconds = self.daemon.history_manager._current_pomodoro_phase_elapsed_seconds()
            phase_started = now - timedelta(seconds=elapsed_seconds)
    
            if self.daemon.state.pomodoro.pomo_phase == "focus":
                self.daemon.history_manager.record_pomodoro_phase("focus", elapsed_seconds, phase_started, now, True)
                self.daemon.state.pomodoro.pomo_phases_tracked_seconds = self.daemon.state.pomodoro.pomo_phases_tracked_seconds + (self.daemon.state.pomodoro.pomo_focus_minutes * 60)
    
                self.daemon.state.pomodoro.pomo_phase = "done"
                self.daemon.state.pomodoro.pomo_next_phase = "break"
                self.daemon.pomo_phase_remaining = 2
                self.daemon.state.pomodoro.pomo_phase_expiry = datetime.now() + timedelta(
                    seconds=self.daemon.pomo_phase_remaining
                )
                self.daemon._mono_pomo_phase_end = (
                    get_continuous_time() + self.daemon.pomo_phase_remaining
                )
                self._remove_block()
                self.daemon._persist_session_lock()
                self.daemon.notifications_manager.play_sound("break")
                self.daemon.notifications_manager.send_mac_notification(
                    "Break Started",
                    f"Take a {self.daemon.state.pomodoro.pomo_break_minutes}m break! Good job focusing.",
                )
                logging.info(
                    "Pomodoro: cycle %d focus ended. Transitioning to done state.",
                    self.daemon.state.pomodoro.pomo_current_cycle
                )
            elif self.daemon.state.pomodoro.pomo_phase == "break":
                self.daemon.history_manager.record_pomodoro_phase("break", elapsed_seconds, phase_started, now, True)
                self.daemon.state.pomodoro.pomo_phases_tracked_seconds = self.daemon.state.pomodoro.pomo_phases_tracked_seconds + (self.daemon.state.pomodoro.pomo_break_minutes * 60)
    
                self.daemon.state.pomodoro.pomo_current_cycle += 1
                if self.daemon.state.pomodoro.pomo_current_cycle > self.daemon.state.pomodoro.pomo_total_cycles:
                    logging.info(
                        "Pomodoro: all %d cycles complete.", self.daemon.state.pomodoro.pomo_total_cycles
                    )
                    # The break was recorded immediately above.  Prevent
                    # cleanup from treating that same completed phase as a
                    # partial one and recording it again.
                    self.daemon.state.pomodoro.pomo_phase = "done"
                    self._cleanup_session()
                    return
                
                self.daemon.state.pomodoro.pomo_phase = "done"
                self.daemon.state.pomodoro.pomo_next_phase = "focus"
                self.daemon.pomo_phase_remaining = 2
                self.daemon.state.pomodoro.pomo_phase_expiry = datetime.now() + timedelta(
                    seconds=self.daemon.pomo_phase_remaining
                )
                self.daemon._mono_pomo_phase_end = (
                    get_continuous_time() + self.daemon.pomo_phase_remaining
                )
                self.daemon.enforcement_manager._enforce_current_mode()
                self.daemon._persist_session_lock()
                self.daemon.notifications_manager.play_sound("start")
                self.daemon.notifications_manager.send_mac_notification(
                    "Focus Time",
                    f"Cycle {self.daemon.state.pomodoro.pomo_current_cycle} of {self.daemon.state.pomodoro.pomo_total_cycles} has started.",
                )
                logging.info(
                    "Pomodoro: cycle %d/%d break ended. Transitioning to done state.",
                    self.daemon.state.pomodoro.pomo_current_cycle,
                    self.daemon.state.pomodoro.pomo_total_cycles,
                )
            elif self.daemon.state.pomodoro.pomo_phase == "done":
                next_phase = self.daemon.state.pomodoro.pomo_next_phase
                self.daemon.state.pomodoro.pomo_phase = next_phase
                self.daemon.state.pomodoro.pomo_next_phase = None
                
                if next_phase == "break":
                    self.daemon.pomo_phase_remaining = self.daemon.state.pomodoro.pomo_break_minutes * 60
                    logging.info("Pomodoro: transitioning into break phase for %dm.", self.daemon.state.pomodoro.pomo_break_minutes)
                elif next_phase == "focus":
                    self.daemon.pomo_phase_remaining = self.daemon.state.pomodoro.pomo_focus_minutes * 60
                    logging.info("Pomodoro: transitioning into focus phase for %dm.", self.daemon.state.pomodoro.pomo_focus_minutes)

                self.daemon.state.pomodoro.pomo_phase_expiry = datetime.now() + timedelta(
                    seconds=self.daemon.pomo_phase_remaining
                )
                self.daemon._mono_pomo_phase_end = (
                    get_continuous_time() + self.daemon.pomo_phase_remaining
                )
                self.daemon._persist_session_lock()

            self.daemon.notifications_manager.broadcast_state_changed()
