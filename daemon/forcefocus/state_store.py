"""Durable, schema-aware storage for ForcedFocus configuration state."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forcefocus.version import PRODUCT_VERSION, STATE_SCHEMA_VERSION


class StateStoreError(RuntimeError):
    """Raised when persisted state cannot be safely read or migrated."""


class StateStore:
    """Own JSON persistence, manifest creation, and recoverable backups."""

    state_files = (
        "session.lock",
        "lists.json",
        "groups.json",
        "settings.json",
        "perma_blocklist.json",
        "templates.json",
        "session_history.json",
    )

    def __init__(self, root: Path):
        self.root = root
        self.manifest_path = root / "state_manifest.json"
        self.backups_path = root / "backups"

    def read_json(self, path: Path) -> dict[str, Any] | None:
        """Return an object JSON document, or ``None`` when it is unreadable."""
        try:
            value = self.read_value(path)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def read_value(path: Path) -> Any | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    @staticmethod
    def write_json(path: Path, data: Any, indent: int | None = None) -> float:
        """Write JSON atomically and durably, retaining the old file mode/owner."""
        path.parent.mkdir(parents=True, exist_ok=True)
        original = path.stat() if path.exists() else None
        mode = stat.S_IMODE(original.st_mode) if original else 0o600
        encoded = json.dumps(data, indent=indent, ensure_ascii=False).encode("utf-8")
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            os.fchmod(fd, mode)
            if original is not None:
                os.fchown(fd, original.st_uid, original.st_gid)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            StateStore._fsync_directory(path.parent)
            return path.stat().st_mtime
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            temp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def ensure_schema(self) -> dict[str, Any]:
        """Create or validate the schema manifest without mutating user data."""
        self.root.mkdir(parents=True, exist_ok=True)
        if self.manifest_path.exists():
            manifest = self.read_json(self.manifest_path)
            if manifest is None:
                raise StateStoreError("state manifest is unreadable")
            schema_version = manifest.get("schema_version")
            if schema_version != STATE_SCHEMA_VERSION:
                raise StateStoreError(
                    f"unsupported state schema {schema_version!r}; expected {STATE_SCHEMA_VERSION}"
                )
            return manifest

        backup = self._backup_legacy_state()
        try:
            if backup is not None:
                for copied in backup.iterdir():
                    if copied.is_file() and copied.name in self.state_files:
                        if self.read_value(copied) is None:
                            raise StateStoreError(f"backup is unreadable: {copied.name}")

            from forcefocus.migrations import MigrationError, migrate_v0_to_v1

            try:
                migrated = migrate_v0_to_v1(self.root)
            except MigrationError as exc:
                raise StateStoreError(str(exc)) from exc
            for path, value in migrated.items():
                self.write_json(path, value, indent=2)

            manifest = {
                "product_version": PRODUCT_VERSION,
                "schema_version": STATE_SCHEMA_VERSION,
                "files": {name: STATE_SCHEMA_VERSION for name in self.state_files},
            }
            self.write_json(self.manifest_path, manifest, indent=2)
            return manifest
        except Exception:
            if backup is not None:
                self._restore_backup(backup)
            raise

    def backup_session_lock(self, session_path: Path, previous_path: Path) -> None:
        """Keep the most recently known-good session document for recovery."""
        data = self.read_json(session_path)
        if data is not None:
            self.write_json(previous_path, data)

    def _backup_legacy_state(self) -> Path | None:
        entries = [entry for entry in self.root.iterdir() if entry.name != "backups"]
        if not entries:
            return None
        self.backups_path.mkdir(mode=0o700, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = self.backups_path / f"schema-0-{timestamp}"
        destination.mkdir(mode=0o700)
        for entry in entries:
            target = destination / entry.name
            if entry.is_dir():
                shutil.copytree(entry, target)
            else:
                shutil.copy2(entry, target)
        self._trim_backups()
        return destination

    def _restore_backup(self, backup: Path) -> None:
        for entry in self.root.iterdir():
            if entry.name == "backups":
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        for entry in backup.iterdir():
            target = self.root / entry.name
            if entry.is_dir():
                shutil.copytree(entry, target)
            else:
                shutil.copy2(entry, target)
        self._fsync_directory(self.root)

    def _trim_backups(self) -> None:
        backups = sorted(
            (item for item in self.backups_path.iterdir() if item.is_dir()),
            key=lambda item: item.name,
            reverse=True,
        )
        for old_backup in backups[3:]:
            shutil.rmtree(old_backup)
