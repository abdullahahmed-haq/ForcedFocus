# Privacy

ForcedFocus has no telemetry, advertising, analytics SDK, account system, cloud
synchronization, or remote control plane.

Sensitive local data can include blocked/allowed domains, session intents and
tasks, prayer coordinates, schedule names, usernames, security-key hash/salt,
and the local API token. These values are excluded or redacted from diagnostic
bundles.

Prayer scheduling is the only optional external data request in version 1.0.0.
When Prayer blocking is enabled, coordinates are configured, and a monthly
calendar is not already cached, ForcedFocus sends the configured latitude,
longitude, and calculation-method number over HTTPS to the AlAdhan calendar
API. The returned prayer times are cached locally. ForcedFocus does not send
domains, session details, security material, or an account identifier with that
request. Users who do not want coordinates sent to AlAdhan should leave Prayer
blocking disabled.

All other application state remains on the Mac.

`forcefocus diagnostics --output <path>` includes product/system versions,
health-check results, and sanitized recent logs. Domain names, coordinates,
home-directory usernames, long token/hash values, and log lines containing
intent/task/passphrase material are removed. Users should still inspect the ZIP
before sharing it.

Uninstallation follows the enforcement policy: permanent blocks can remain by
design, while other product data is removed only after security-key and active
session rules are satisfied.
