.PHONY: release release-test

# Bump patch version, commit, tag, and push to trigger the PyPI release workflow.
release:
	python scripts/bump_version.py patch
	$(eval VERSION := $(shell python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"))
	git add pyproject.toml
	git commit -m "chore: bump version to $(VERSION)"
	git push origin main
	git tag v$(VERSION)
	git push origin v$(VERSION)
	@echo "Released v$(VERSION) — PyPI workflow triggered."

# Bump patch version, commit, tag with -test suffix, and push to trigger the test.PyPI release workflow.
release-test:
	python scripts/bump_version.py patch
	$(eval VERSION := $(shell python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"))
	git add pyproject.toml
	git commit -m "chore: bump version to $(VERSION)"
	git push origin main
	git tag v$(VERSION)-test
	git push origin v$(VERSION)-test
	@echo "Released v$(VERSION)-test — test.PyPI workflow triggered."
