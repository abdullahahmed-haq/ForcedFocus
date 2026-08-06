# ForcedFocus Interface Design

## Register

ForcedFocus is a dense product interface, not a marketing surface. The UI
should feel like a calm command center: familiar controls, restrained semantic
color, clear system state, and no decorative motion that competes with focus.

## Existing identity

- Dark system surface with indigo as the primary action/current-state accent.
- Green indicates healthy/success, amber indicates pending/warning, and red is
  reserved for errors, destructive actions, and active blocking consequences.
- Use the system/Inter-style sans stack for controls and prose; use tabular
  numerals for timers and metrics.
- `shared/tokens.css` is the canonical token source. Generated copies must pass
  `scripts/sync_shared.sh --check`.

## Component contract

Interactive controls require default, hover, focus-visible, active, disabled,
loading, and error states. Transitions should take 150–250ms and convey state.
All motion must have a reduced-motion alternative. Body and placeholder text
must meet WCAG AA contrast.

## Reliability states

- Offline: persistent banner naming the daemon connection problem and a Retry
  action. Session controls are disabled until status is known.
- Version mismatch: persistent banner showing UI and daemon versions and a
  Check for Updates action.
- Migration: progress/status banner; mutations remain disabled.
- Recovery required: blocking error banner with a link to documented recovery;
  never imply that enforcement was cleared.
- Loading: retain layout with skeleton/disabled state; do not flash idle state.

## Constraints

Navigation, main layout, and established identity stay stable in Stage 1. Avoid
new nested cards, glass decoration, bounce easing, gradient text, or motion that
does not communicate a system transition.
