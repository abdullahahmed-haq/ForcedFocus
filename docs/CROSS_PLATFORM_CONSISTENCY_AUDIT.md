# ForcedFocus Cross-Platform Consistency Audit

**Audited source:** commit `11976e3` (`main`)\
**Authoritative specification:** `ForcedFocus™.md` supplied at
`/Users/abdullahahmed/Library/Mobile Documents/com~apple~CloudDocs/Obsidian/Works/APPS/ForcedFocus™.md`\
**Audit date:** 2026-08-06\
**Scope:** Python daemon and enforcement, local web app, Swift menu-bar app,
and Chrome extension. This report does not change runtime state or production
code.

## Executive result

The daemon is the effective source of truth for persistent lists and it has
working event/SSE propagation to the web UI and extension. Permanent Block is
also correctly given precedence over an ordinary whitelist in both the daemon
firewall ordering and Chrome DNR priorities.

However, the implementation has **13 confirmed contradictions** with the
specification. Four are core-logic defects: an empty blacklist silently blocks
defaults, prayer does not fully override a whitelist, prayer consumes the
interrupted session's timer, and recorded focus can include time that never
occurred. The remaining issues affect list-edit semantics, Rescue semantics,
schedules, permanent-block immediacy in a Chrome race, and tracking fidelity.

Severity definitions: **Critical** can create unexpected or weaker blocking;
**High** breaks required session/accounting semantics; **Medium** breaks a
required workflow or state contract; **Low** is an explicit terminology or
presentation contradiction.

## Confirmed contradictions

### FF-AUD-01 — Active-session list edits are rejected instead of deferred

| Field | Evidence |
| --- | --- |
| Severity / type | High — session-rule semantics |
| Location | Daemon: `daemon/forcefocus/domains.py:110-164`; Web Rules UI: `web/js/app.js:2747-2797`; Chrome context menu: `chrome-extension/background.js:1153-1168` |
| Code actually does | Every add, bulk-add, and removal of Blacklist or Whitelist checks `state.session.active` and returns `Cannot modify lists during active session.` The two UIs surface that rejection. |
| Specification requires | Blacklist and Whitelist edits remain allowed, appear immediately, and apply only to the next session; the active session must retain its snapshot. |
| Why it matters | A user cannot save a discovery from Chrome or adjust future rules while focusing. This contradicts the stated design rather than protecting the active snapshot. |

**Remediation.** Remove the active-session rejection for ordinary list
mutations, while continuing to leave `session_base_domains` and
`state.active_domains` unchanged until the next start. Update the daemon,
web copy, Chrome success messaging, and regression tests. This preserves the
priority hierarchy: list persistence must not mutate a currently active
regular-session decision; Permanent Block remains the only immediately
enforced list.

### FF-AUD-02 — An empty Blacklist silently becomes a 563-domain block

| Field | Evidence |
| --- | --- |
| Severity / type | Critical — blocking logic |
| Location | Daemon: `daemon/forcefocus/domains.py:199-234`, `daemon/forcefocus/session/core.py:320-339`; Chrome consumes the resulting snapshot at `chrome-extension/background.js:783-793` |
| Code actually does | If the stored blacklist and selected groups are empty, both the daemon expansion path and session snapshot append `DEFAULT_BLOCKLIST`. An isolated probe returned 563 expanded domains for an empty list. |
| Specification requires | An empty Blacklist means no blocking; the app must not select default websites without showing and obtaining the user's choice. |
| Why it matters | Starting an apparently empty session unexpectedly blocks sites. The behavior propagates to hosts/PF/DNS and Chrome, so it is not merely a display issue. |

**Remediation.** Remove the default-list fallback from both session-snapshot
paths (or make defaults explicit, selectable Groups). Start a Blacklist
session with an empty effective set and report zero blocked domains. Cover
daemon, Chrome snapshot/DNR, web and menu-bar preflight text. This makes the
regular-session layer least restrictive as required; Permanent Block and
Prayer/Ban still override it.

### FF-AUD-03 — Prayer fails to override an active Whitelist at the firewall

| Field | Evidence |
| --- | --- |
| Severity / type | Critical — priority violation |
| Location | Daemon prayer transition: `daemon/forcefocus/watchdog.py:157-186`; PF rule construction: `daemon/forcefocus/enforcement/firewall.py:16-30, 133-144` |
| Code actually does | Prayer sets `prayer_ban_active` and enables the firewall, but `_update_blocked_ips` retains the normal Whitelist IP table. `_enforce_firewall` then emits `pass out quick ... <ff_whitelisted_ips>` before the global HTTP/HTTPS block. Those normal-session allow-listed sites remain reachable during Prayer. |
| Specification requires | Permanent Block > Prayer/Ban > regular session. Prayer uses Ban and blocks everything, independent of the Blacklist, Whitelist, and Groups. |
| Why it matters | Prayer can be bypassed simply by having a site on an already-active whitelist, directly violating the documented priority order. |

**Remediation.** Model an effective enforcement mode that is `prayer-ban`
while prayer is active. It must clear or ignore normal whitelist allowances
when building PF/DNS/SNI policy, then restore the captured regular-session
policy afterward. Update daemon enforcement and add an integration test for
Prayer over Blacklist, Whitelist, Ban, Pomodoro Focus, and Pomodoro Break.
Permanent domains remain denied throughout; Prayer's Ban must win over every
regular-session allowance.

### FF-AUD-04 — Prayer does not pause and resume an interrupted session

| Field | Evidence |
| --- | --- |
| Severity / type | High — interruption/resumption logic |
| Location | `daemon/forcefocus/watchdog.py:109-148, 157-186`; status overlay: `daemon/forcefocus/session/core.py:542-574`; timer fields: `daemon/forcefocus/session/core.py:588-665` |
| Code actually does | The watchdog starts Prayer but never saves a suspended regular session or shifts `_mono_session_end` / `_mono_pomo_phase_end`. It continues expiration and Pomodoro-transition checks immediately afterward. Status hides the regular session behind a Prayer status, but the hidden timer keeps running. |
| Specification requires | Prayer takes over, then the prior session resumes with its remaining time. Its timer must not restart and elapsed focus must not be lost. |
| Why it matters | A regular session can end wholly or partly during Prayer, so there is no real resumption and tracking becomes inaccurate. |

**Remediation.** Add persisted interruption state containing the effective
session and phase deadlines plus the Prayer-start monotonic time. Freeze both
regular and Pomodoro phase timing on Prayer entry; on exit restore them by the
remaining duration and re-enforce the captured session. Update status/UI to
show `Prayer` as the effective reason and the suspended session as the next
state. Test restart recovery while interrupted. Prayer remains above the
stored regular mode, but must not consume that mode's time.

### FF-AUD-05 — Actual focus time is overstated after an early stop or interruption

| Field | Evidence |
| --- | --- |
| Severity / type | High — tracking / daily-goal correctness |
| Location | Daemon: `daemon/forcefocus/history.py:28-89, 171-205, 273-290`; web tracking display: `web/js/app.js:2974-3002, 3271-3275` |
| Code actually does | Non-Pomodoro history records `duration_minutes` and `net_focus_minutes` as the configured session duration, calculated from expiry, regardless of the time at which a delayed stop actually cleans up. `daily_totals` and `total_focus_minutes` also sum duration rather than net focus. An isolated probe recorded a session stopped after approximately 20 minutes as 120 focus minutes. |
| Specification requires | Daily goal tracking counts actual Focus time only, not paused or interrupted time; canceled sessions should report actual time up to cancellation. |
| Why it matters | Goals, streaks, total-focus cards, and reports can claim focus that never occurred. Prayer's unpaused timer compounds the error. |

**Remediation.** Persist a start monotonic timestamp plus pause/interruption
intervals and calculate elapsed focus at cleanup. Record only actual active
focus for Standard/Rescue; calculate aggregates and `daily_totals` from
`net_focus_minutes`, not configured duration. Update daemon history and web
labels/breakdown. This is independent of enforcement priority, but must treat
Prayer as a higher-priority non-focus interruption.

### FF-AUD-06 — Rescue is persisted and reported as Whitelist, not a Ban-type session

| Field | Evidence |
| --- | --- |
| Severity / type | Medium — mode contract / state consistency |
| Location | Daemon: `daemon/forcefocus/schedules.py:52-59, 287-294`; session path: `daemon/forcefocus/session/core.py:275-316`; web and menu-bar start payloads: `web/js/app.js:2511-2516`, `web/js/menubar.js:854-860`; Chrome popup: `chrome-extension/popup.js:777-781` |
| Code actually does | Each client starts Rescue with `mode: "whitelist"`; template and recurring normalization force the same value. The daemon special-cases Rescue to use an empty allow list, so it functions as a Ban but exposes and persists a Whitelist mode. |
| Specification requires | Rescue Mode is a quick Ban-type session that blocks all sites without lists or Groups. |
| Why it matters | The state contract, stored templates/schedules, and history misclassify Rescue. Priority and future conflict logic cannot reliably distinguish a true Whitelist from a Ban-shaped Rescue. |

**Remediation.** Persist and return `mode: "ban"` for Rescue, retaining
`session_type: "rescue"` only for its quick-start UX and separate tracking.
Migrate old Rescue templates/schedules safely, update all three clients, and
test that no groups or whitelist domains are used. This places Rescue in the
Ban tier below Prayer and Permanent Block, never in the regular Whitelist tier.

### FF-AUD-07 — Rescue time is counted toward the daily focus goal

| Field | Evidence |
| --- | --- |
| Severity / type | Medium — tracking policy |
| Location | `daemon/forcefocus/history.py:42-80, 171-177, 218-226` |
| Code actually does | Rescue follows the non-Pomodoro history branch, receives full `net_focus_minutes`, and is included in range totals and daily-goal/streak calculations. |
| Specification requires | Daily goals count actual Focus time only; Break, Prayer, and Rescue appear separately. |
| Why it matters | A quick emergency lock can satisfy a daily focus goal despite not being a focus session. |

**Remediation.** Give Rescue a distinct tracking classification with zero
daily-goal contribution (while retaining a separate Rescue statistic/event).
Update history aggregation and Tracking UI. It is a Ban-tier enforcement
action, not a regular Focus contribution.

### FF-AUD-08 — Prayer events are not recorded as separate tracking events

| Field | Evidence |
| --- | --- |
| Severity / type | Medium — missing feature / tracking |
| Location | Prayer lifecycle: `daemon/forcefocus/watchdog.py:157-186`; history writer: `daemon/forcefocus/history.py:28-119`; reader explicitly excludes Prayer at `daemon/forcefocus/history.py:159-164` |
| Code actually does | Prayer changes an in-memory flag and broadcasts state, but no history entry/event is written at entry or exit. The history reader has a Prayer exclusion despite no corresponding writer. |
| Specification requires | Tracking may show blocking, prayer, and schedule events; the refinement explicitly calls for Prayer to appear separately from daily focus. |
| Why it matters | Users cannot audit why an interruption occurred or reconcile regular-session time with a prayer interval. |

**Remediation.** Record a non-goal Prayer event with start/end, duration,
reason, and interrupted-session identifier. Render it separately in Tracking
and exclude it from focus totals. The event documents the higher-priority
Prayer decision without competing with the suspended session's accounting.

### FF-AUD-09 — Recurring schedule conflicts are neither prevented nor resolved by priority

| Field | Evidence |
| --- | --- |
| Severity / type | High — scheduling / priority conflict |
| Location | Recurring creation: `daemon/forcefocus/schedules.py:250-415`; execution: `daemon/forcefocus/watchdog.py:188-237, 254-289`; active-session merge: `daemon/forcefocus/session/core.py:101-155` |
| Code actually does | Recurring schedule create/update performs no overlap analysis against recurring or one-off schedules. At trigger time it calls `_start_session`; an active compatible session is merged, and incompatible modes/types fail, without a conflict decision based on Permanent/Prayer/Ban/regular priority or a user-visible conflict record. |
| Specification requires | Conflicting schedules are prevented; unavoidable runtime conflicts apply the higher-priority mode and explain the reason in Tracking and the interface. |
| Why it matters | Overlapping schedules can unexpectedly extend a session, fail silently from the user's perspective, or apply a regular session with no priority explanation. |

**Remediation.** Validate recurring and one-off occurrences against existing
schedules on creation/update, and establish a single conflict resolver for
runtime collisions. Emit a status/history reason for every winner/loser. The
resolver must use Permanent > Prayer/Ban > Blacklist/Whitelist, never generic
session merging as a substitute for policy.

### FF-AUD-10 — Recurring schedules can be paused inside the forbidden 20-minute window

| Field | Evidence |
| --- | --- |
| Severity / type | Medium — schedule-rule violation |
| Location | `daemon/forcefocus/schedules.py:401-420`; HTTP command route: `daemon/forcefocus/api_http.py:342-355` |
| Code actually does | Pause delegates to a normal update and has no next-run or 20-minute validation. The 20-minute guard exists only for one-off cancellation in `daemon/forcefocus/schedules.py:456-493`. |
| Specification requires | A recurring schedule cannot be paused less than 20 minutes before it starts. |
| Why it matters | A client can bypass the intended commitment window through the recurring pause endpoint. |

**Remediation.** Enforce the cutoff in the daemon for every recurring pause
route, using the computed next run; present the returned reason in web/menu
bar UI. Test direct API calls as well as UI clicks. This protects a scheduled
regular/Ban decision before it enters the priority resolver.

### FF-AUD-11 — One-off schedules do not expose required lifecycle state or groups

| Field | Evidence |
| --- | --- |
| Severity / type | Medium — missing feature / state visibility |
| Location | State response: `daemon/forcefocus/session/core.py:526-541`; start/expiry flow: `daemon/forcefocus/session/core.py:157-195`, `daemon/forcefocus/watchdog.py:239-252`; web rendering: `web/js/app.js:470-570` |
| Code actually does | Pending one-off schedules expose only time, duration, mode, and session type. They have no stable ID, Groups, or `pending` status; once started, canceled, or expired they are removed from the list with no lifecycle record. |
| Specification requires | A schedule shows start time, duration, blocking type, session type, Groups, and status (`pending`, `started`, `ended`, or `canceled`). |
| Why it matters | Users cannot determine which scheduled rule applied or why it disappeared, especially during conflicts and interruptions. |

**Remediation.** Persist schedule IDs and lifecycle state/events, return groups
and status in the API, and render them in the web app/menu-bar where relevant.
Record final status in Tracking. These records provide the user-facing reason
needed when priority resolves a collision.

### FF-AUD-12 — Chrome can acknowledge a Permanent Block before its DNR rule is updated

| Field | Evidence |
| --- | --- |
| Severity / type | Medium — state-sync / immediacy failure |
| Location | Synchronization guard and fallback: `chrome-extension/background.js:707-874`; context-menu mutation: `chrome-extension/background.js:1126-1152` |
| Code actually does | After a successful Permanent Block POST, the extension updates its in-memory set and calls `syncBlockRules()` without awaiting it. If another sync is running, the guard immediately returns; an SSE event can be dropped by the same guard. The guaranteed fallback is the one-minute alarm. |
| Specification requires | Adding a site to Permanent Block from Chrome must take effect and update the app immediately, without restarts or reloads. |
| Why it matters | In a reachable race, the extension shows success but its browser-level DNR protection can remain stale for up to the next alarm, even though daemon-level hosts enforcement was requested. |

**Remediation.** Replace the drop-on-busy guard with a coalesced, awaited
pending-sync loop (or install the permanent DNR rule synchronously after the
POST, then reconcile). Keep SSE as an accelerator, not the only immediate
path. Add a mocked Chrome regression test where a context-menu click arrives
during an in-flight sync. Permanent Block must be applied before all
Prayer/Ban/session decisions in the extension as well as the daemon.

### FF-AUD-13 — The product uses both “Rescue Throne” and “Rescue Mode”

| Field | Evidence |
| --- | --- |
| Severity / type | Low — terminology / user-facing consistency |
| Location | “Rescue Throne”: `web/js/app.js:613-615, 2505-2533`, `chrome-extension/popup.js:394`; “Rescue Mode”: `daemon/forcefocus/session/core.py:352-356`, `web/js/settings.js:135-137` |
| Code actually does | Both names appear in active UI, errors, settings, and daemon notifications. |
| Specification requires | Choose one name; its explicit recommendation is `Rescue Mode`. |
| Why it matters | It creates contradictory terminology in a flow where the user must understand what type of block is active. |

**Remediation.** Standardize labels, notifications, template text, and
accessibility strings on `Rescue Mode`. Combine this with FF-AUD-06 so the
mode's Ban-tier behavior is obvious to users.

## Verified aligned behavior

These checks found no contradiction in the audited source:

- Lists and permanent data are daemon-owned; web and Chrome write through the
  same authenticated API rather than maintaining competing list stores.
- Domain extraction in the daemon removes scheme, path, port, and leading
  `www`; web group/list input and Chrome context menus feed normalized hosts.
- Permanent removal requires the security key and an enforced 30-minute delay
  (`daemon/forcefocus/domains.py:376-457`).
- Permanent entries are independently written and re-enforced, including after
  a session ends (`daemon/forcefocus/enforcement/__init__.py:27-53` and
  `daemon/forcefocus/enforcement/dns.py:18-104`).
- Web, menu-bar webview, and extension subscribe to `/api/stream`; the daemon
  increments `state_revision` on list/permanent mutations. The native menu-bar
  also polls `/api/status` every second.
- Pomodoro break paths preserve permanent DNR/firewall rules while suspending
  the session-specific block.

## Requirement coverage matrix

| Requirement group | Result | Evidence / finding |
| --- | --- | --- |
| Shared list source and Chrome-to-web propagation | Compliant in source | Daemon API + SSE/state revision; see verified behavior |
| Domain cleanup and duplicate prevention | Compliant in normal UI paths | `domains.py:76-125`; web settings/list extraction; Chrome hostname extraction |
| Permanent always active / secured removal | Compliant in source | Event-driven enforcement and delayed key-gated removal |
| Permanent immediate in Chrome | Contradiction | FF-AUD-12 |
| Permanent > Prayer/Ban > regular session | Contradiction | FF-AUD-03; Permanent-over-Whitelist ordering itself is correct |
| Blacklist/Whitelist edits apply next session | Contradiction | FF-AUD-01 |
| Empty Blacklist means no block | Contradiction | FF-AUD-02 |
| Prayer takeover and resume | Contradiction | FF-AUD-03, FF-AUD-04 |
| Pomodoro break retains Permanent Block | Compliant in source | Extension break branch and daemon break checks |
| Rescue semantics and naming | Contradiction | FF-AUD-06, FF-AUD-13 |
| Schedule conflicts / lifecycle / pause cutoff | Contradiction | FF-AUD-09 through FF-AUD-11 |
| Templates create, edit, delete, run, duplicate | Compliant in source | `daemon/forcefocus/schedules.py:13-244` |
| Tracking and daily goal use only actual focus | Contradiction | FF-AUD-05, FF-AUD-07, FF-AUD-08 |
| No restart/reload needed for normal state updates | Compliant in source, with Chrome race exception | SSE + polling; FF-AUD-12 |
| Sleep/time-zone recovery explanation | Not fully verifiable | One-off expiry is logged, but user-facing missed-schedule explanation was not established without mutating runtime state |

## Prioritized remediation order

1. **Enforcement priority:** fix Prayer-over-Whitelist and make Prayer a real
   interruption/resumption state (FF-AUD-03, FF-AUD-04).
2. **Regular-session correctness:** remove hidden default blocking and allow
   deferred list edits (FF-AUD-02, FF-AUD-01).
3. **Truthful accounting:** calculate actual focus; separate Rescue and Prayer
   from daily goals and events (FF-AUD-05, FF-AUD-07, FF-AUD-08).
4. **State contracts and sync:** model Rescue as Ban, serialize Permanent DNR
   updates, and standardize its name (FF-AUD-06, FF-AUD-12, FF-AUD-13).
5. **Scheduling:** introduce lifecycle persistence and a priority-aware
   conflict resolver; enforce recurring pause limits (FF-AUD-09 through
   FF-AUD-11).

## Verification record and limitations

- The live daemon at `127.0.0.1:7070` responded healthy, version `1.0.0`, and
  idle. No authenticated write or real enforcement action was performed.
- Isolated, non-persistent probes confirmed FF-AUD-01, FF-AUD-02, and
  FF-AUD-05. JavaScript syntax checks passed for dashboard, settings,
  menu-bar web UI, extension background, and extension popup. Shared files
  passed `scripts/sync_shared.sh --check`; Python source compiled successfully
  with the available Python 3.12 runtime; Swift source parsed successfully.
- Full `pytest` could not run because pytest is absent from both available
  Python runtimes. Swift type-checking was blocked by a local macOS SDK/toolchain
  mismatch and a sandboxed default module-cache location, not a source error.
- No external Chrome instance was connected for live extension automation.
  The extension findings are source-traced; FF-AUD-12 also follows from the
  explicit in-flight synchronization guard and one-minute fallback.
