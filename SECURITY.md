# Security Model

## Trust boundaries

The root daemon is the privileged boundary. HTTP binds only to loopback and
mutations require the per-launch API token. CLI commands use a mode-0600 Unix
socket. Browser origins are restricted to configured extension identifiers.

Stage 1 introduces a `SystemAdapter` boundary so a later release can move the
minimum required operations into a dedicated privileged helper. Until that
boundary is complete, the project must not claim least-privilege architecture.

## Secrets

- The security key is stored only as PBKDF2 salt and hash under
  `/etc/forcefocus`; migrations must preserve both bytes exactly.
- The API token is regenerated on daemon start and never included in diagnostic
  bundles.
- Developer ID certificates, notarization credentials, and Sparkle private keys
  belong in CI secrets/keychains, never in the repository or release artifact.

## Release requirements

All Mach-O code and embedded frameworks are signed inside-out with hardened
runtime and secure timestamps. The flat PKG is signed with Developer ID
Installer, notarized, stapled, and verified before appcast publication.

## Reporting

Do not attach state files to bug reports. Use `forcefocus diagnostics`, inspect
the archive, then share it through the project's private security channel.
