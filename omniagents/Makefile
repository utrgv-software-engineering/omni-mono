-include .env
export

STUDIO_WEB_DIR := omniagents/studio/server/web
STUDIO_STATIC_DIR := omniagents/studio/server/static
BUBBLETEA_CMD_DIR := omniagents/backends/bubbletea/cmd/omni-tui

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

publish: clean build
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

.PHONY: clean studio-assets build publish install dev format coverage-python coverage-go coverage-web coverage-all bump-patch bump-minor bump-major
