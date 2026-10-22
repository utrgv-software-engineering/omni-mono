WEB_DIR := web
STATIC_DIR := omniagents_studio/static

# Rebuild the UI bundle that ships in the wheel (commit the result).
assets:
	cd $(WEB_DIR) && npm ci
	cd $(WEB_DIR) && npm run build
	rm -rf $(STATIC_DIR)
	mkdir -p $(STATIC_DIR)
	cp -R $(WEB_DIR)/dist/. $(STATIC_DIR)/

build: assets
	python -m build

.PHONY: assets build
