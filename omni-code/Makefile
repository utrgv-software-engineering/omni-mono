include .env
export

clean:
	rm -rf dist/ build/ *.egg-info

build:
	# Generate the PACKAGED project.yml from the canonical repo-root one, but
	# rewrite paths to ../<dir>: in a wheel this file lands in omni_code/, while
	# agents/tools/capabilities install as top-level packages one level up. The
	# repo-root project.yml keeps bare paths for editable/dev installs.
	python scripts/gen_packaged_project.py project.yml omni_code/project.yml
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
