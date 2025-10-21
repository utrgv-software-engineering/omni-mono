include .env
export

clean:
	rm -rf build/ dist/ *.egg-info

build:
	python -m build

publish-pypi: clean build
	twine upload dist/*

publish-gemfury: clean build
	@set -e; \
	for file in dist/*; do \
		if [ -f "$$file" ]; then \
			curl -f -sS -F package=@"$$file" "https://$${FURY_TOKEN}@push.fury.io/$${FURY_USER}/"; \
		fi; \
	done

publish:
	git push --follow-tags

install:
	pip install {sanitized}

install-gemfury:
	pip install {sanitized} --index-url https://$${FURY_TOKEN}@pypi.fury.io/$${FURY_USER}/

dev:
	pip install -e .[dev]

format:
	black .

bump-patch:
	bump2version patch

bump-minor:
	bump2version minor

bump-major:
	bump2version major

.PHONY: clean build publish-pypi publish-gemfury publish install install-gemfury dev format bump-patch bump-minor bump-major
