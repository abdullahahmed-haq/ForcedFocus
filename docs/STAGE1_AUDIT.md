# Stage 1 Closure Audit

Audit date: 2026-08-06

Status: **NOT COMPLETE**

The repository must not be tagged `v1.0.0` or distributed publicly yet. The
automated functional suite currently passes 19 tests, but measured coverage is
19.09%, below the 85% release gate. No signed/notarized PKG, Universal bundled runtime,
Sparkle integration, or successful upgrade/rollback evidence exists.

## Verified complete

- Product version `1.0.0`, dependency locks, and license. The
  Git repository still has no recoverable baseline commit.
- Python and development dependency locks.
- Central command dispatch used by HTTP and Unix socket transports.
- `/api/version`, `/api/health`, stable timer fields, and structured error codes.
- Canonical `shared/` source with drift detection.
- Durable atomic JSON writes, state manifest scaffolding, legacy backup, and
  `session.lock.prev` recovery.
- Schema `0 → 1` migration for known legacy state, centralized reads for the
  migrated JSON stores, and sounds stored outside the web root.
- `doctor`, redacted diagnostics, persistent reliability UI states, and product,
  design, security, privacy, build, release, and recovery documentation.
- Known intent, prayer-state, CLI status/web, and permanent-list filename fixes.

## Release blockers

- Create a recoverable baseline Git commit without swallowing unrelated local
  configuration files.
- Finish migration edge-case and rollback tests for every state document.
- Recovery command requiring the security key and enforcement-aware recovery.
- `SystemAdapter`, fake adapter, privileged command plans, rollback, and removal
  of direct subprocess calls outside the adapter.
- Dual-architecture CPython 3.13.15 runtime, transactional installer, downgrade
  prevention, rollback, and unsigned development PKG.
- Xcode project and Sparkle 2 integration.
- Developer ID Application/Installer identities, signing, notarization, staple,
  appcast generation, and a real update test.
- Contract/integration/system test matrix and at least 85% daemon logic coverage.
- Intel and Apple Silicon clean-install/upgrade/rollback tests, seven-day RC
  pilot, and CPU targets.

Run `make audit-stage1 PYTHON=python3` for the repository-owned gate. External
release gates still require Apple credentials and physical/virtual test Macs.
