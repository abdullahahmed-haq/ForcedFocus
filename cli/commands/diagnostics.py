from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cli.commands.doctor import gather_checks
from cli.output import out


LOG_PATHS = (
    Path("/var/log/forcefocus.log"),
    Path("/var/log/forcefocus_error.log"),
    Path("/var/log/forcefocus_web.log"),
    Path("/var/log/forcefocus_web_error.log"),
)


def redact(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if re.search(r"\b(intent|intent_tasks|passphrase|salt|ks_hash|api_token)\b", line, re.IGNORECASE):
            lines.append("[REDACTED_SENSITIVE_LOG_LINE]")
            continue
        line = re.sub(r"/Users/[^/\s]+", "/Users/[REDACTED]", line)
        line = re.sub(r"\b[a-fA-F0-9]{32,}\b", "[REDACTED_SECRET]", line)
        line = re.sub(r"(?<!\d)-?\d{1,3}\.\d{4,}(?!\d)", "[REDACTED_COORDINATE]", line)
        line = re.sub(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b", "[REDACTED_DOMAIN]", line)
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def cmd_diagnostics(args) -> None:
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    _checks, summary = gather_checks()
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "privacy": "No state files, domains, intents, tasks, coordinates, usernames, or secrets are included.",
        "doctor": summary,
    }

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.json", json.dumps(metadata, indent=2))
        for path in LOG_PATHS:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
            except OSError:
                continue
            archive.writestr(f"logs/{path.name}", redact("\n".join(lines)))

    out.print_data({"status": "ok", "message": f"Diagnostics written to {output}", "output": str(output)}, title="Diagnostics")
