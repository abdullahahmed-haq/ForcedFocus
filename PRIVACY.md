# Privacy

ForcedFocus is local-only in version 1.0.0. It has no telemetry, advertising,
analytics SDK, account system, or cloud synchronization.

Sensitive local data can include blocked/allowed domains, session intents and
tasks, prayer coordinates, schedule names, usernames, security-key hash/salt,
and the local API token. These values remain on the Mac and are excluded or
redacted from diagnostic bundles.

`forcefocus diagnostics --output <path>` includes product/system versions,
health-check results, and sanitized recent logs. Domain names, coordinates,
home-directory usernames, long token/hash values, and log lines containing
intent/task/passphrase material are removed. Users should still inspect the ZIP
before sharing it.

Uninstallation follows the enforcement policy: permanent blocks can remain by
design, while other product data is removed only after security-key and active
session rules are satisfied.
