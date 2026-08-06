"""The single command boundary shared by local clients and transport layers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from forcefocus.version import API_VERSION, PRODUCT_VERSION, STATE_SCHEMA_VERSION


def error_response(error_code: str, message: str) -> dict[str, str]:
    return {"status": "error", "error_code": error_code, "message": message}


class CommandService:
    """Dispatch supported commands without exposing implementation exceptions."""

    def __init__(self, daemon: Any):
        self.daemon = daemon

    def dispatch(self, command: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(command, dict):
            return error_response("INVALID_INPUT", "Command must be an object.")
        action = command.get("action")
        if not isinstance(action, str) or not action:
            return error_response("INVALID_INPUT", "Missing command action.")

        handler = self._handlers().get(action)
        if handler is None:
            return error_response("UNKNOWN_ACTION", f"Unknown action: {action}")
        try:
            result = handler(command)
        except Exception:
            logging.exception("Command failed: %s", action)
            return error_response("SYSTEM_FAILURE", "The command could not be completed.")
        return self._normalize_response(result)

    def _handlers(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        session = self.daemon.session_manager
        domains = self.daemon.domains_manager
        schedules = self.daemon.schedules_manager
        settings = self.daemon.settings_manager
        notifications = self.daemon.notifications_manager
        return {
            "start": session._start_session,
            "stop": lambda cmd: session._request_stop(cmd.get("key", "")),
            "cancel_stop": lambda _cmd: session._cancel_stop(),
            "status": lambda _cmd: session.cmd_get_status(),
            "health": lambda _cmd: self._health(),
            "set_intent": session.cmd_set_intent,
            "get_lists": lambda _cmd: domains.cmd_get_lists(),
            "add_domain": domains.cmd_add_domain,
            "add_domains": domains.cmd_add_domains,
            "remove_domain": domains.cmd_remove_domain,
            "get_groups": lambda _cmd: domains.cmd_get_groups(),
            "add_group": domains.cmd_add_group,
            "remove_group": domains.cmd_remove_group,
            "get_perma_blocklist": lambda _cmd: domains.cmd_get_perma_blocklist(),
            "add_perma_block": domains.cmd_add_perma_block,
            "request_perma_unblock": domains.cmd_request_perma_unblock,
            "cancel_perma_unblock": domains.cmd_cancel_perma_unblock,
            "get_recurring_schedules": lambda _cmd: schedules.cmd_get_recurring_schedules(),
            "add_recurring_schedule": schedules.cmd_add_recurring_schedule,
            "update_recurring_schedule": schedules.cmd_update_recurring_schedule,
            "pause_recurring_schedule": lambda cmd: schedules.cmd_toggle_recurring_schedule(cmd, False),
            "resume_recurring_schedule": lambda cmd: schedules.cmd_toggle_recurring_schedule(cmd, True),
            "duplicate_recurring_schedule": schedules.cmd_duplicate_recurring_schedule,
            "remove_recurring_schedule": schedules.cmd_remove_recurring_schedule,
            "cancel_schedule": schedules.cmd_cancel_schedule,
            "get_templates": lambda _cmd: schedules.cmd_get_templates(),
            "add_template": schedules.cmd_add_template,
            "update_template": schedules.cmd_update_template,
            "remove_template": schedules.cmd_remove_template,
            "duplicate_template": schedules.cmd_duplicate_template,
            "start_template": schedules.cmd_start_template,
            "get_settings": lambda _cmd: settings.cmd_get_settings(),
            "save_settings": settings.cmd_save_settings,
            "get_sounds": lambda _cmd: notifications.cmd_get_sounds(),
            "delete_sound": notifications.cmd_delete_sound,
            "upload_sound": notifications.cmd_upload_sound,
            "get_history": lambda cmd: self.daemon.history_manager.cmd_get_session_history(
                cmd.get("query", {})
            ),
            "clear_history": lambda _cmd: self.daemon.history_manager.cmd_clear_session_history(),
            "get_prayer": lambda _cmd: self.daemon.prayer_manager.cmd_get_prayer(),
            "skip_prayer": self.daemon.prayer_manager.cmd_skip_prayer,
            "get_session_domains": lambda _cmd: domains.cmd_get_session_domains(),
        }

    def _health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "healthy": not self.daemon.recovery_required,
            "recovery_required": self.daemon.recovery_required,
            "product_version": PRODUCT_VERSION,
            "api_version": API_VERSION,
            "state_schema_version": STATE_SCHEMA_VERSION,
            "session_active": self.daemon.state.session.active,
            "socket_configured": bool(self.daemon.socket_api_manager),
            "enforcement_configured": bool(self.daemon.enforcement_manager),
        }

    @staticmethod
    def _normalize_response(result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return error_response("SYSTEM_FAILURE", "Command returned an invalid response.")
        if result.get("status") != "error" or "error_code" in result:
            return result
        message = str(result.get("message", "Command failed."))
        lower_message = message.lower()
        if "unauthorized" in lower_message:
            code = "UNAUTHORIZED"
        elif "active session" in lower_message or "overlap" in lower_message:
            code = "STATE_CONFLICT"
        elif "invalid" in lower_message or "missing" in lower_message or "must be" in lower_message:
            code = "INVALID_INPUT"
        else:
            code = "COMMAND_REJECTED"
        return {**result, "error_code": code}
