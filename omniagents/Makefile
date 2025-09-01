include .env
export

clean:
	rm -rf dist/ build/ *.egg-info

build:
	python -m build

publish: clean build
	@for file in dist/*.tar.gz; do \
		echo "Uploading $$file to Fury.io..."; \
		curl -F package=@$$file https://$(FURY_TOKEN)@push.fury.io/$(FURY_USER)/; \
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

.PHONY: clean build publish install dev bump-patch bump-minor bump-major