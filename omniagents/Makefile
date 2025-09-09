include .env
export

clean:
	rm -rf dist/ build/ *.egg-info

build:
	python -m build --sdist

publish: clean build
	@echo "Uploading sdist to Fury.io..."
	@set -e; \
	for file in dist/*.tar.gz; do \
		if [ -f "$$file" ]; then \
			echo "Uploading $$file to Fury.io..."; \
			curl -f -sS -F package=@"$$file" https://$(FURY_TOKEN)@push.fury.io/$(FURY_USER)/ || exit 1; \
		fi; \
	done

install:
	pip install omniagents --index-url https://$(FURY_TOKEN)@pypi.fury.io/$(FURY_USER)/

dev:
	pip install -e .

bump-patch:
	bump2version patch

bump-minor:
	bump2version minor

bump-major:
	bump2version major

# --- Bubble Tea TUI helpers ---

# Build omni-tui for the current host platform into the package bin/
tui-build:
	@echo "Building Bubble Tea TUI (omni-tui) for host platform..."
	@cd omniagents/backends/bubbletea/cmd/omni-tui && \
		go build -ldflags="-s -w" -o ../../bin/omni-tui

tui-clean:
	rm -rf omniagents/backends/bubbletea/bin/omni-tui dist/tui

.PHONY: clean build publish install dev bump-patch bump-minor bump-major tui-build tui-clean
