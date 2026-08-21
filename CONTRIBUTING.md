# Contributing to ForcedFocus

## Before changing code

- Read [the architecture map](docs/ARCHITECTURE.md).
- Check `git status` and preserve unrelated local changes.
- Keep runtime paths stable unless the installer and LaunchDaemon contract are
  updated together.

## Change boundaries

- Daemon policy belongs in `daemon/forcefocus/`.
- Enforcement adapters belong in `daemon/forcefocus/enforcement/`.
- CLI behavior belongs in `cli/`.
- Browser behavior belongs in `web/` or `chrome-extension/`.
- Canonical browser-shared code belongs in `shared/` only.
- macOS installation and package resources belong in `packaging/macos/`.
- Documentation and operational scripts belong in `docs/` and `scripts/`.

## Validation

```bash
make check
```

For browser-shared changes, also run:

```bash
bash scripts/sync_shared.sh --check
```

Tests must cover policy changes. Avoid committing generated files, caches,
local logs, or packaged application artifacts.
