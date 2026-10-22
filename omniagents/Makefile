-include .env
export

WEB_UI_DIR := omniagents/backends/web/ui
INK_TUI_DIR := omniagents/backends/ink/tui

clean:
	rm -rf dist/ build/ *.egg-info

build:
	python -m build

ink-assets:
	cd $(INK_TUI_DIR) && npm ci
	cd $(INK_TUI_DIR) && npm run build
	@test -f $(INK_TUI_DIR)/dist/index.js || (echo "Ink TUI build failed: dist/index.js not found" && exit 1)

web-assets:
	cd $(WEB_UI_DIR) && npm ci
	cd $(WEB_UI_DIR) && npm run build
	@test -f $(WEB_UI_DIR)/dist/index.html || (echo "Web UI build failed: dist/index.html not found" && exit 1)

# Build everything needed for packaging (Ink TUI dist + Web UI dist)
package-build: ink-assets web-assets
	python -m build

# Releases come only from a clean, up-to-date main (dev is where work happens).
release-guard:
	@test "$$(git rev-parse --abbrev-ref HEAD)" = main || { echo "Error: publish from main (merge dev into main first)"; exit 1; }
	@test -z "$$(git status --porcelain)" || { echo "Error: working tree is dirty"; exit 1; }
	@git fetch -q origin main && test "$$(git rev-parse HEAD)" = "$$(git rev-parse origin/main)" || { echo "Error: main is not in sync with origin/main"; exit 1; }

publish: release-guard clean package-build
	@echo "Uploading distributions to Fury.io..."
	@set -e; \
	for file in dist/*; do \
		if [ -f "$$file" ]; then \
			echo "Uploading $$file to Fury.io..."; \
			curl -f -sS -F package=@"$$file" https://$(FURY_TOKEN)@push.fury.io/$(FURY_USER)/ || exit 1; \
		fi; \
	done

install:
	pip install omniagents --index-url https://$(FURY_TOKEN)@pypi.fury.io/$(FURY_USER)/

dev:
	pip install -e .[dev]

format:
	black .

coverage-python:
	mkdir -p coverage/python
	pytest

bump-patch:
	bump2version patch

bump-minor:
	bump2version minor

bump-major:
	bump2version major

.PHONY: clean ink-assets web-assets build publish install dev format coverage-python bump-patch bump-minor bump-major release-guard
