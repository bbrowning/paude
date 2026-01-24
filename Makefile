# Paude build and release automation
#
# Usage:
#   make build          - Build images locally (dev/testing)
#   make publish        - Build multi-arch and push to registry
#   make release VERSION=x.y.z - Tag, update version, build, and push
#   make clean          - Remove local images

REGISTRY ?= quay.io/bbrowning
IMAGE_NAME = paude-base-centos9
PROXY_IMAGE_NAME = paude-proxy-centos9

# Get version from git tag, or use 'dev' if not on a tag
VERSION ?= $(shell git describe --tags --exact-match 2>/dev/null || echo "dev")

FULL_IMAGE = $(REGISTRY)/$(IMAGE_NAME):$(VERSION)
FULL_PROXY_IMAGE = $(REGISTRY)/$(PROXY_IMAGE_NAME):$(VERSION)
LATEST_IMAGE = $(REGISTRY)/$(IMAGE_NAME):latest
LATEST_PROXY_IMAGE = $(REGISTRY)/$(PROXY_IMAGE_NAME):latest

# Architectures for multi-arch builds
PLATFORMS = linux/amd64,linux/arm64

.PHONY: build run publish release clean login help test install lint format typecheck pypi-build pypi-publish

help:
	@echo "Paude build targets:"
	@echo "  make build          - Build images locally for current arch"
	@echo "  make run            - Run paude in dev mode (builds locally)"
	@echo "  make test           - Run all tests"
	@echo "  make publish        - Build multi-arch images and push to registry"
	@echo "  make release VERSION=x.y.z - Full release: tag git, update version, build, push"
	@echo "  make clean          - Remove local paude images"
	@echo "  make login          - Authenticate with container registry"
	@echo "  make pypi-build     - Build Python package for PyPI"
	@echo "  make pypi-publish   - Upload Python package to PyPI"
	@echo ""
	@echo "Development targets:"
	@echo "  make install        - Install Python package with dev dependencies"
	@echo "  make lint           - Run ruff linter"
	@echo "  make format         - Format code with ruff"
	@echo "  make typecheck      - Run mypy type checker"
	@echo ""
	@echo "Current settings:"
	@echo "  REGISTRY=$(REGISTRY)"
	@echo "  VERSION=$(VERSION)"

# Detect native architecture for builds
NATIVE_ARCH := $(shell uname -m | sed 's/x86_64/amd64/')

# Build images locally (native arch, for development)
build:
	podman build --platform linux/$(NATIVE_ARCH) -t $(IMAGE_NAME):latest ./containers/paude
	podman build --platform linux/$(NATIVE_ARCH) -t $(PROXY_IMAGE_NAME):latest ./containers/proxy

# Run paude in dev mode (builds images locally)
run:
	PAUDE_DEV=1 paude

# Run all tests
test:
	pytest --cov=paude --cov-report=term-missing

# Development targets
install:
	pip install -e ".[dev]"

lint:
	ruff check src tests

format:
	ruff format src tests

typecheck:
	mypy src

# Login to container registry
login:
	@echo "Logging in to $(REGISTRY)..."
	podman login quay.io

# Build and push multi-arch images
publish: check-version
	@echo "Building and pushing $(FULL_IMAGE) and $(FULL_PROXY_IMAGE)..."
	@echo ""
	# Build and push paude image (remove any existing image/manifest first)
	-podman rmi -f $(FULL_IMAGE) 2>/dev/null
	-podman manifest rm $(FULL_IMAGE) 2>/dev/null
	podman manifest create $(FULL_IMAGE)
	podman build --platform $(PLATFORMS) --manifest $(FULL_IMAGE) ./containers/paude
	podman manifest push --all $(FULL_IMAGE) $(FULL_IMAGE)
	# Tag as latest
	-podman rmi -f $(LATEST_IMAGE) 2>/dev/null
	-podman manifest rm $(LATEST_IMAGE) 2>/dev/null
	podman manifest create $(LATEST_IMAGE)
	podman build --platform $(PLATFORMS) --manifest $(LATEST_IMAGE) ./containers/paude
	podman manifest push --all $(LATEST_IMAGE) $(LATEST_IMAGE)
	@echo ""
	# Build and push proxy image
	-podman rmi -f $(FULL_PROXY_IMAGE) 2>/dev/null
	-podman manifest rm $(FULL_PROXY_IMAGE) 2>/dev/null
	podman manifest create $(FULL_PROXY_IMAGE)
	podman build --platform $(PLATFORMS) --manifest $(FULL_PROXY_IMAGE) ./containers/proxy
	podman manifest push --all $(FULL_PROXY_IMAGE) $(FULL_PROXY_IMAGE)
	# Tag as latest
	-podman rmi -f $(LATEST_PROXY_IMAGE) 2>/dev/null
	-podman manifest rm $(LATEST_PROXY_IMAGE) 2>/dev/null
	podman manifest create $(LATEST_PROXY_IMAGE)
	podman build --platform $(PLATFORMS) --manifest $(LATEST_PROXY_IMAGE) ./containers/proxy
	podman manifest push --all $(LATEST_PROXY_IMAGE) $(LATEST_PROXY_IMAGE)
	@echo ""
	@echo "Published:"
	@echo "  $(FULL_IMAGE)"
	@echo "  $(FULL_PROXY_IMAGE)"
	@echo "  $(LATEST_IMAGE)"
	@echo "  $(LATEST_PROXY_IMAGE)"

check-version:
	@if [ "$(VERSION)" = "dev" ]; then \
		echo "Error: VERSION is 'dev'. Tag a release first or set VERSION=x.y.z"; \
		exit 1; \
	fi

# Full release process
release:
	@if [ "$(VERSION)" = "dev" ]; then \
		echo "Usage: make release VERSION=x.y.z"; \
		echo "Example: make release VERSION=0.2.0"; \
		exit 1; \
	fi
	@echo "=== Releasing v$(VERSION) ==="
	@echo ""
	# Update version in pyproject.toml
	sed -i.bak 's/^version = .*/version = "$(VERSION)"/' pyproject.toml && rm -f pyproject.toml.bak
	# Update version in src/paude/__init__.py
	sed -i.bak 's/^__version__ = .*/__version__ = "$(VERSION)"/' src/paude/__init__.py && rm -f src/paude/__init__.py.bak
	# Commit the version change (only if there are changes)
	git add pyproject.toml src/paude/__init__.py
	git diff --cached --quiet || git commit -m "Release v$(VERSION)"
	# Create git tag
	git tag -a "v$(VERSION)" -m "Release v$(VERSION)"
	@echo ""
	@echo "Version updated and tagged. Now run:"
	@echo "  make publish VERSION=$(VERSION)"
	@echo "  make pypi-build && make pypi-publish"
	@echo "  git push origin main --tags"
	@echo ""
	@echo "Then create a GitHub release at:"
	@echo "  https://github.com/bbrowning/paude/releases/new?tag=v$(VERSION)"

# Build Python package for PyPI
pypi-build:
	rm -rf dist/
	python -m build

# Upload Python package to PyPI
pypi-publish:
	python -m twine upload dist/*

# Remove local images
clean:
	-podman rmi $(IMAGE_NAME):latest 2>/dev/null
	-podman rmi $(PROXY_IMAGE_NAME):latest 2>/dev/null
	-podman manifest rm $(FULL_IMAGE) 2>/dev/null
	-podman manifest rm $(FULL_PROXY_IMAGE) 2>/dev/null
	-podman manifest rm $(LATEST_IMAGE) 2>/dev/null
	-podman manifest rm $(LATEST_PROXY_IMAGE) 2>/dev/null
	@echo "Cleaned up local images"
