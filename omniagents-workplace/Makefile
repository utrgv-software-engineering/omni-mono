-include .env
export

WEB_DIR := web
STATIC_DIR := omniagents_workplace/static

clean:
	rm -rf dist/ build/ *.egg-info

# Build the UI bundle the wheel ships. The web app is an npm workspace of
# the monorepo, so it installs at the root; the bundle is never committed.
assets:
	cd .. && npm ci
	cd .. && npm run build -w omniagents-workplace-web
	rm -rf $(STATIC_DIR)
	mkdir -p $(STATIC_DIR)
	cp -R $(WEB_DIR)/dist/. $(STATIC_DIR)/
	@test -f $(STATIC_DIR)/index.html || (echo "Web UI build failed: index.html not found" && exit 1)

build: assets
	python -m build

# Releases come only from a clean, up-to-date main (dev is where work happens),
# as omniagents' do.
release-guard:
	@test "$$(git rev-parse --abbrev-ref HEAD)" = main || { echo "Error: publish from main (merge dev into main first)"; exit 1; }
	@test -z "$$(git status --porcelain)" || { echo "Error: working tree is dirty"; exit 1; }
	@git fetch -q origin main && test "$$(git rev-parse HEAD)" = "$$(git rev-parse origin/main)" || { echo "Error: main is not in sync with origin/main"; exit 1; }

publish: release-guard clean build
	@echo "Uploading distributions to Fury.io..."
	@set -e; \
	for file in dist/*; do \
		if [ -f "$$file" ]; then \
			echo "Uploading $$file to Fury.io..."; \
			curl -f -sS -F package=@"$$file" https://$(FURY_TOKEN)@push.fury.io/$(FURY_USER)/ || exit 1; \
		fi; \
	done

bump-patch:
	bump2version patch

bump-minor:
	bump2version minor

bump-major:
	bump2version major

.PHONY: clean assets build release-guard publish bump-patch bump-minor bump-major
