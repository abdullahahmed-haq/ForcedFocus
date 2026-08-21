# ForcedFocus Product

## Purpose

ForcedFocus is a local-first macOS focus enforcement utility for people who
want a commitment stronger than a browser extension. It applies time-bound
domain and network restrictions and deliberately makes early unlocks slow.

## Platform

macOS 13 and later, distributed as a Universal signed and notarized PKG. The
product is single-user per Mac in version 1.0.0.

## Product principles

- A focus commitment must survive browser changes, daemon restarts, sleep, and
  reboot without silently weakening enforcement.
- Recovery must preserve user data and restore the original network state.
- Every privileged mutation must be explicit, logged safely, idempotent, and
  testable without changing the developer's Mac.
- The product is local-first. There is no telemetry, account, cloud sync, or
  remote control plane in version 1.0.0. Optional Prayer scheduling makes the
  narrowly disclosed calendar-provider request described in `PRIVACY.md`.
- Existing CLI, HTTP, Unix socket, configuration paths, and extension clients
  remain compatible during the 1.0.0 migration.

## Primary surfaces

- Web dashboard for sessions, rules, schedules, templates, history, and settings.
- Native menu-bar shell for quick access, status, and updates.
- CLI for automation, diagnostics, and recovery.
- A privileged root daemon that owns enforcement and persisted state.

## Version 1.0.0 scope

Reliability, secure persistence, recovery, privileged-operation isolation,
packaging, updates, local diagnostics, and the UI states required to explain
those systems. New productivity features and a full visual redesign are out of
scope.

## Success criteria

The release gates in `docs/STAGE1_AUDIT.md` and `PLAN.md` are authoritative.
The version is not release-ready until clean install, upgrade, rollback,
notarization, architecture, active-session continuity, and seven-day RC tests
are recorded as passing.
