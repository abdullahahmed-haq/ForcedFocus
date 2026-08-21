# Build Guide

## Requirements

- macOS 13 or later.
- Full Xcode selected with `xcode-select`, not Command Line Tools alone.
- CPython 3.13.15 for development checks.
- Node.js 22 and npm.

Install locked development dependencies in an isolated environment, then run:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements/dev.lock
npm ci --prefix web
make check PYTHON=.venv/bin/python
make build
```

`make package` must produce an unsigned development PKG without accessing
release credentials. Chrome extension files are syntax/compatibility fixtures
and must not be copied into the PKG payload.

Repository-owned macOS launchd, newsyslog, and application icon resources live
under `packaging/macos/`; installed system paths remain unchanged.
