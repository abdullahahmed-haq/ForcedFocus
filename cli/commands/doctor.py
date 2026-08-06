from __future__ import annotations

import json
import os
import platform
import socket
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from rich.table import Table

from cli.output import console


PRODUCT_VERSION = "1.0.0"
SOCK_PATH = Path("/var/run/forcefocus.sock")
CONFIG_DIR = Path("/etc/forcefocus")
PLIST_PATH = Path("/Library/LaunchDaemons/com.forcefocus.daemon.plist")
HOSTS_PATH = Path("/private/etc/hosts")


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str


def _socket_health() -> dict:
    if not SOCK_PATH.exists():
        return {"status": "error", "error_code": "DAEMON_NOT_FOUND"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(SOCK_PATH))
            client.sendall(b'{"action":"health"}')
            client.shutdown(socket.SHUT_WR)
            return json.loads(client.recv(65536).decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "error_code": "SOCKET_FAILURE", "message": str(exc)}


def _http_health() -> dict:
    request = urllib.request.Request("http://127.0.0.1:7070/api/health")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"status": "error", "error_code": "HTTP_FAILURE", "message": str(exc)}


def gather_checks() -> tuple[list[DoctorCheck], dict]:
    socket_health = _socket_health()
    http_health = _http_health()
    manifest_path = CONFIG_DIR / "state_manifest.json"
    manifest = None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    usage = os.statvfs("/")
    available_bytes = usage.f_bavail * usage.f_frsize
    hosts_text = ""
    try:
        hosts_text = HOSTS_PATH.read_text(encoding="utf-8")
    except OSError:
        pass

    checks = [
        DoctorCheck("platform", "ok" if platform.system() == "Darwin" else "error", f"{platform.system()} {platform.mac_ver()[0]} ({platform.machine()})"),
        DoctorCheck("product_version", "ok", PRODUCT_VERSION),
        DoctorCheck("launchd_definition", "ok" if PLIST_PATH.exists() else "error", str(PLIST_PATH)),
        DoctorCheck("unix_socket", "ok" if socket_health.get("status") == "ok" else "error", socket_health.get("error_code", "connected")),
        DoctorCheck("http_api", "ok" if http_health.get("status") == "ok" else "error", http_health.get("error_code", "connected")),
        DoctorCheck("state_schema", "ok" if manifest and manifest.get("schema_version") == 1 else "error", f"schema={manifest.get('schema_version') if manifest else 'unavailable'}"),
        DoctorCheck("recovery", "error" if socket_health.get("recovery_required") else "ok", "required" if socket_health.get("recovery_required") else "not required"),
        DoctorCheck("pf_anchor_config", "ok" if Path("/etc/pf.conf").exists() and 'anchor "forcefocus"' in Path("/etc/pf.conf").read_text(errors="ignore") else "warning", "configured" if Path("/etc/pf.conf").exists() else "pf.conf unavailable"),
        DoctorCheck("hosts_markers", "ok", f"session={int('BEGIN FORCEFOCUS ─' in hosts_text)} permanent={int('BEGIN FORCEFOCUS PERMANENT' in hosts_text)}"),
        DoctorCheck("disk_space", "ok" if available_bytes >= 500 * 1024 * 1024 else "error", f"{available_bytes // (1024 * 1024)} MiB available"),
    ]
    summary = {
        "status": "ok" if not any(item.status == "error" for item in checks) else "error",
        "product_version": PRODUCT_VERSION,
        "checks": [asdict(item) for item in checks],
    }
    return checks, summary


def cmd_doctor(args) -> None:
    checks, summary = gather_checks()
    if args.json:
        print(json.dumps(summary, indent=2))
        return

    table = Table(title="ForcedFocus Doctor", header_style="bold cyan")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for item in checks:
        style = {"ok": "success", "warning": "warning", "error": "error"}[item.status]
        table.add_row(item.name, f"[{style}]{item.status}[/{style}]", item.detail)
    console.print(table)
