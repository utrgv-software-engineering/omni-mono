WEB_DIR := web
STATIC_DIR := omniagents_studio/static

# Build the UI bundle the wheel ships. The web app is an npm workspace of
# the monorepo, so it installs at the root; the bundle is never committed.
assets:
	cd .. && npm ci
	cd .. && npm run build -w omniagents-studio-web
	rm -rf $(STATIC_DIR)
	mkdir -p $(STATIC_DIR)
	cp -R $(WEB_DIR)/dist/. $(STATIC_DIR)/

build: assets
	python -m build

.PHONY: assets build
