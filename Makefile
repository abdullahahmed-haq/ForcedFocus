PYTHON ?= python3.13
NPM ?= npm

.PHONY: check check-python check-js check-shared test lint typecheck build build-web build-menubar package release audit-stage1

check: check-shared check-python check-js

check-python: lint typecheck test

lint:
	$(PYTHON) -m ruff check daemon cli

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest --cov --cov-report=term-missing

check-js:
	node --input-type=module --check < web/js/app.js
	node --input-type=module --check < web/js/settings.js
	node --input-type=module --check < web/js/menubar.js
	node --input-type=module --check < shared/api.js
	node --input-type=module --check < shared/utils.js
	node --check chrome-extension/background.js
	node --check chrome-extension/popup.js
	node --check chrome-extension/blocked.js

check-shared:
	bash scripts/sync_shared.sh --check

build: build-web build-menubar

build-web:
	$(NPM) --prefix web run build:css

build-menubar: build-web
	bash menubar/build_menubar.sh

package:
	@echo "PKG packaging will be added with the distribution work package."
	@false

release:
	@echo "Signed release automation will be added with the distribution work package."
	@false

audit-stage1:
	$(PYTHON) scripts/stage1_audit.py
