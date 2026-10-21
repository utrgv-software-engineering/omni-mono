include .env
export

clean:
	rm -rf dist/ build/ *.egg-info

build:
	python -m build

# Releases come only from a clean, up-to-date main (dev is where work happens).
release-guard:
	@test "$$(git rev-parse --abbrev-ref HEAD)" = main || { echo "Error: publish from main (merge dev into main first)"; exit 1; }
	@test -z "$$(git status --porcelain)" || { echo "Error: working tree is dirty"; exit 1; }
	@git fetch -q origin main && test "$$(git rev-parse HEAD)" = "$$(git rev-parse origin/main)" || { echo "Error: main is not in sync with origin/main"; exit 1; }

publish: release-guard clean build
	@test -n "$(FURY_TOKEN)" || { echo "Error: FURY_TOKEN not set (check .env)"; exit 1; }
	@test -n "$(FURY_USER)" || { echo "Error: FURY_USER not set (check .env)"; exit 1; }
	@echo "Uploading distributions to Fury.io..."
	@set -e; \
	for file in dist/*; do \
		if [ -f "$$file" ]; then \
			echo "Uploading $$file to Fury.io..."; \
			curl -f -sS -F package=@"$$file" https://$(FURY_TOKEN)@push.fury.io/$(FURY_USER)/ || exit 1; \
		fi; \
	done

install:
	pip install omni-code --index-url https://$(FURY_TOKEN)@pypi.fury.io/$(FURY_USER)/

dev:
	pip install -e .

bump-patch:
	bump2version patch

bump-minor:
	bump2version minor

bump-major:
	bump2version major

.PHONY: clean build publish install dev bump-patch bump-minor bump-major release-guard
