-include .env
export

STUDIO_WEB_DIR := omniagents/studio/server/web
STUDIO_STATIC_DIR := omniagents/studio/server/static
WEB_UI_DIR := omniagents/backends/web/ui
BUBBLETEA_CMD_DIR := omniagents/backends/bubbletea/cmd/omni-tui
INK_TUI_DIR := omniagents/backends/ink/tui

clean:
	rm -rf dist/ build/ *.egg-info

studio-assets:
	cd $(STUDIO_WEB_DIR) && npm ci
	cd $(STUDIO_WEB_DIR) && npm run build
	rm -rf $(STUDIO_STATIC_DIR)
	mkdir -p $(STUDIO_STATIC_DIR)
	cp -R $(STUDIO_WEB_DIR)/dist/. $(STUDIO_STATIC_DIR)/

build: studio-assets
	python -m build

ink-assets:
	cd $(INK_TUI_DIR) && npm ci
	cd $(INK_TUI_DIR) && npm run build
	@test -f $(INK_TUI_DIR)/dist/index.js || (echo "Ink TUI build failed: dist/index.js not found" && exit 1)

web-assets:
	cd $(WEB_UI_DIR) && npm ci
	cd $(WEB_UI_DIR) && npm run build
	@test -f $(WEB_UI_DIR)/dist/index.html || (echo "Web UI build failed: dist/index.html not found" && exit 1)

# Build everything needed for packaging (Studio static + Ink TUI dist + Web UI dist)
package-build: studio-assets ink-assets web-assets
	python -m build

publish: clean package-build
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

coverage-go:
	mkdir -p coverage/go
	cd $(BUBBLETEA_CMD_DIR) && go test ./... -covermode=atomic -coverprofile=$(CURDIR)/coverage/go/cover.out

coverage-web:
	mkdir -p coverage/web
	cd $(STUDIO_WEB_DIR) && npm run test:coverage

coverage-all: coverage-python coverage-web coverage-go

bump-patch:
	bump2version patch

bump-minor:
	bump2version minor

bump-major:
	bump2version major

.PHONY: clean studio-assets ink-assets web-assets build publish install dev format coverage-python coverage-go coverage-web coverage-all bump-patch bump-minor bump-major
