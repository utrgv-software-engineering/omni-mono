include .env
export

clean:
	rm -rf dist/ build/ *.egg-info

build:
	python -m build

publish: clean build
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

.PHONY: clean build publish install dev bump-patch bump-minor bump-major
