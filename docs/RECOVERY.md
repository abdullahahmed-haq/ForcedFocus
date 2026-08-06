# Recovery Guide

If `/api/health` reports `recovery_required`, do not manually clear PF, DNS, or
hosts rules. Preserve `/etc/forcefocus`, the previous version directory, and the
latest upgrade backup.

1. Run `forcefocus doctor --json` and create a redacted diagnostic bundle.
2. Verify whether `session.lock.prev` is valid and whether hosts/PF/DNS still
   contain ForcedFocus enforcement state.
3. Use the supported recovery command with the security key. An active session
   must retain the normal delayed-unlock policy.
4. Restore the previous application symlink and state backup together; never
   mix a newer schema with an older daemon.
5. Start the daemon and require both HTTP health and Unix socket health before
   removing the failed version.

The recovery CLI and transactional installer are release blockers until they
implement this procedure automatically and are covered by system tests.
