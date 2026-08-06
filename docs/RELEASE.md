# Release Guide

1. Pass `make audit-stage1`, `make check`, architecture system tests, and the
   seven-day RC pilot.
2. Confirm `VERSION`, Python package, daemon, CLI, app bundle, and tag match.
3. Build both pinned CPython runtimes and verify their published SHA-256 sums.
4. Build the Universal menu-bar app and flat PKG.
5. Sign nested code with Developer ID Application, then sign the flat PKG with
   Developer ID Installer.
6. Submit with `xcrun notarytool`, retain the JSON log, staple, and validate with
   `codesign`, `pkgutil`, `spctl`, and `stapler`.
7. Test `1.0.0-rc.1 → 1.0.0` while a session is active on Intel and Apple Silicon.
8. Publish the GitHub Release. Generate and sign the appcast only after every
   previous check succeeds.

The release workflow expects only secret names; credentials must never be
written to repository files.
