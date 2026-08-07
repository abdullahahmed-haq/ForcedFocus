# ForcedFocus Architecture

This document is the map of the repository. Runtime paths are intentionally
stable because the installer, LaunchDaemon, browser extension, and existing
diagnostic tooling refer to them directly.

## System shape

```text
                    +--------------------+
                    |      Clients       |
                    | Web / CLI / Swift   |
                    | Chrome extension   |
                    +----------+---------+
                               |
                    HTTP/SSE or Unix socket
                               |
                    +----------v---------+
                    |      Daemon         |
                    | orchestration +    |
                    | state + lifecycle  |
                    +----+-----------+----+
                         |           |
                  domain/session   adapters
                         |           |
                    +----v-----------v----+
                    | Enforcement / OS   |
                    | PF / DNS / hosts   |
                    +--------------------+
```

## Directory ownership

| Directory | Owns | Must not own |
| --- | --- | --- |
| `daemon/forcefocus/` | Daemon domain logic, state, persistence, and OS adapters | UI rendering or CLI presentation |
| `daemon/forcefocus/session/` | Session lifecycle, Pomodoro, and intent behavior | HTTP/socket transport |
| `daemon/forcefocus/enforcement/` | Firewall, DNS, and system enforcement adapters | Session policy decisions |
| `daemon/tests/` | Daemon and contract tests | Production code |
| `cli/` | CLI commands, transport client, and terminal output | Daemon internals |
| `web/` | Dashboard pages, browser-side behavior, and CSS | Canonical shared browser utilities |
| `chrome-extension/` | Manifest, extension UI, and service worker | Daemon business rules |
| `shared/` | Canonical browser-shared JavaScript/CSS sources | Generated copies |
| `menubar/` | Native macOS menu-bar wrapper | Daemon business logic |
| `scripts/` | Development, release, and audit automation | Runtime application code |
| `docs/` | Product, architecture, recovery, and release documentation | Executable behavior |

## Dependency direction

The intended dependency direction is:

```text
clients -> transport interfaces -> daemon orchestration -> domain modules -> OS adapters
```

The daemon is the only owner of focus policy and enforcement state. Clients
send commands and render responses; they should not reimplement policy.
`shared/` is the single source for browser code copied into `web/shared/` and
`chrome-extension/shared/` by `scripts/sync_shared.sh`.

## Safe change rules

1. Preserve the public paths used by `install.sh`, the LaunchDaemon plist, and
   `pyproject.toml` unless the packaging contract is updated in the same change.
2. Put new policy in a daemon module and expose it through the existing command
   or HTTP interfaces; do not duplicate it in a client.
3. Put OS-specific behavior behind an adapter in `daemon/forcefocus/enforcement/`.
4. Update or add a focused test in `daemon/tests/` for every policy change.
5. Edit browser shared sources only under `shared/`, then run
   `bash scripts/sync_shared.sh --write` and verify with `--check`.
6. Keep one-off migration and historical scripts under `scripts/archive/`; do
   not import them from production code.

## Verification gates

Run `make check` before merging. On a machine without the pinned Python
runtime, use the project interpreter explicitly (for example, `python -m
pytest` and `python -m ruff check daemon cli`) and record the limitation.
