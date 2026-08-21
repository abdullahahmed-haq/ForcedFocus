#!/usr/bin/env python3
"""Verify that every repository-owned release surface uses VERSION."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def python_product_version() -> str:
    module = ast.parse((ROOT / "daemon/forcefocus/version.py").read_text(encoding="utf-8"))
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "PRODUCT_VERSION" for target in statement.targets):
            value = ast.literal_eval(statement.value)
            if isinstance(value, str):
                return value
    raise ValueError("daemon/forcefocus/version.py does not define PRODUCT_VERSION")


def main() -> int:
    expected = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project_section = pyproject_text.split("[project]", 1)[1].split("\n[", 1)[0]
    version_match = re.search(r'^version\s*=\s*"([^"]+)"', project_section, re.MULTILINE)
    if not version_match:
        raise ValueError("pyproject.toml [project] does not define version")
    web_package = json.loads((ROOT / "web/package.json").read_text(encoding="utf-8"))
    extension_manifest = json.loads(
        (ROOT / "chrome-extension/manifest.json").read_text(encoding="utf-8")
    )
    versions = {
        "VERSION": expected,
        "pyproject.toml": version_match.group(1),
        "daemon/forcefocus/version.py": python_product_version(),
        "web/package.json": str(web_package["version"]),
        "chrome-extension/manifest.json": str(extension_manifest["version"]),
    }
    mismatches = {name: version for name, version in versions.items() if version != expected}
    if mismatches:
        for name, version in mismatches.items():
            print(f"Version mismatch: {name} has {version!r}; expected {expected!r}.", file=sys.stderr)
        return 1
    print(f"Release versions are consistent at {expected}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
