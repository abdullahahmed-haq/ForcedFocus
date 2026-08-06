#!/usr/bin/env python3
"""Fail closed until every repository-owned Stage 1 release gate exists."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def has_all(*paths: str) -> bool:
    return all((ROOT / path).exists() for path in paths)


def contains(path: str, needle: str) -> bool:
    candidate = ROOT / path
    return candidate.exists() and needle in candidate.read_text(encoding="utf-8")


def has_git_commit() -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def direct_subprocess_files() -> list[str]:
    allowed = {
        "daemon/forcefocus/system_adapter.py",
        "scripts/stage1_audit.py",
    }
    offenders = []
    for base in (ROOT / "daemon", ROOT / "cli"):
        for path in base.rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            if relative in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            if "subprocess.run(" in text or "subprocess.Popen(" in text:
                offenders.append(relative)
    return sorted(offenders)


def shared_is_synced() -> bool:
    result = subprocess.run(
        ["bash", "scripts/sync_shared.sh", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def build_checks() -> list[Check]:
    subprocess_offenders = direct_subprocess_files()
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    docs = ("PRODUCT.md", "DESIGN.md", "SECURITY.md", "PRIVACY.md")
    return [
        Check(
            "A: development baseline",
            has_all(
                "VERSION",
                "LICENSE",
                "pyproject.toml",
                "requirements/runtime.lock",
                "requirements/dev.lock",
                ".github/workflows/check.yml",
                ".github/workflows/release.yml",
            )
            and has_git_commit(),
            "version, locks, license, CI, and a recoverable baseline commit",
        ),
        Check(
            "B: API command boundary",
            has_all("daemon/forcefocus/command_service.py")
            and contains("daemon/forcefocus/api_http.py", 'path == "/api/version"')
            and contains("daemon/forcefocus/api_http.py", 'path == "/api/health"'),
            "central dispatch plus version and health endpoints",
        ),
        Check("C: shared source", shared_is_synced(), "web and extension copies match shared/"),
        Check(
            "D: persistence and migrations",
            has_all("daemon/forcefocus/state_store.py", "daemon/forcefocus/migrations.py")
            and has_all("cli/commands/recovery.py")
            and not contains("daemon/forcefocus/notifications.py", 'WEB_DIR / "assets" / "sounds"')
            and not any(
                contains(path, "json.loads(")
                for path in (
                    "daemon/forcefocus/settings.py",
                    "daemon/forcefocus/history.py",
                    "daemon/forcefocus/domains.py",
                    "daemon/forcefocus/schedules.py",
                    "daemon/forcefocus/prayer.py",
                )
            ),
            "requires complete 0→1 migrations, centralized reads, recovery, and data-directory sounds",
        ),
        Check(
            "E: privileged seam",
            has_all("daemon/forcefocus/system_adapter.py") and not subprocess_offenders,
            "direct subprocess users: " + (", ".join(subprocess_offenders) or "none"),
        ),
        Check(
            "F: Universal PKG",
            has_all("packaging/scripts/build_runtime.sh", "packaging/scripts/build_pkg.sh")
            and "will be added" not in makefile,
            "requires pinned dual-architecture runtime and transactional PKG scripts",
        ),
        Check(
            "G: signing and Sparkle",
            any((ROOT / "menubar").glob("*.xcodeproj"))
            and contains("menubar/forcefocus_menubar.swift", "SPUStandardUpdaterController"),
            "requires Xcode project, Sparkle 2, appcast, signing and notarization",
        ),
        Check(
            "H: diagnostics and release docs",
            has_all("cli/commands/doctor.py", "cli/commands/diagnostics.py", *docs)
            and contains("web/html/index.html", "daemonHealthBanner")
            and contains("web/js/app.js", "checkVersionCompatibility"),
            "doctor, redacted diagnostics, UI reliability states, and operator documentation",
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable results")
    args = parser.parse_args()
    checks = build_checks()
    if args.json:
        print(json.dumps({"complete": all(item.passed for item in checks), "checks": [asdict(item) for item in checks]}, indent=2))
    else:
        for item in checks:
            marker = "PASS" if item.passed else "FAIL"
            print(f"[{marker}] {item.name}: {item.detail}")
        print("\nStage 1 is complete." if all(item.passed for item in checks) else "\nStage 1 is NOT complete.")
    return 0 if all(item.passed for item in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
