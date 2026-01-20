# Paude build and release automation
#
# Usage:
#   make build          - Build images locally (dev/testing)
#   make publish        - Build multi-arch and push to registry
#   make release VERSION=x.y.z - Tag, update version, build, and push
#   make clean          - Remove local images

REGISTRY ?= docker.io/bbrowning
IMAGE_NAME = paude
PROXY_IMAGE_NAME = paude-proxy

# Get version from git tag, or use 'dev' if not on a tag
VERSION ?= $(shell git describe --tags --exact-match 2>/dev/null || echo "dev")

FULL_IMAGE = $(REGISTRY)/$(IMAGE_NAME):$(VERSION)
FULL_PROXY_IMAGE = $(REGISTRY)/$(PROXY_IMAGE_NAME):$(VERSION)
LATEST_IMAGE = $(REGISTRY)/$(IMAGE_NAME):latest
LATEST_PROXY_IMAGE = $(REGISTRY)/$(PROXY_IMAGE_NAME):latest

# Architectures for multi-arch builds
PLATFORMS = linux/amd64,linux/arm64

.PHONY: build run publish release clean login help test test-config test-hash

help:
	@echo "Paude build targets:"
	@echo "  make build          - Build images locally for current arch"
	@echo "  make run            - Run paude in dev mode (builds locally)"
	@echo "  make test           - Run tests (no container daemon required)"
	@echo "  make publish        - Build multi-arch images and push to registry"
	@echo "  make release VERSION=x.y.z - Full release: tag git, update script, build, push"
	@echo "  make clean          - Remove local paude images"
	@echo "  make login          - Authenticate with container registry"
	@echo ""
	@echo "Current settings:"
	@echo "  REGISTRY=$(REGISTRY)"
	@echo "  VERSION=$(VERSION)"

# Build images locally (single arch, for development)
build:
	podman build -t $(IMAGE_NAME):latest ./containers/paude
	podman build -t $(PROXY_IMAGE_NAME):latest ./containers/proxy

# Run paude in dev mode (builds images locally)
run:
	PAUDE_DEV=1 ./paude

# Run tests (no container daemon required)
test:
	@bash tests/run_tests.sh

# Run BYOC config module tests
test-config:
	bash test/test_config.sh

# Run BYOC hash module tests
test-hash:
	bash test/test_hash.sh

# Login to container registry
login:
	@echo "Logging in to $(REGISTRY)..."
	podman login docker.io

# Build and push multi-arch images
publish: check-version
	@echo "Building and pushing $(FULL_IMAGE) and $(FULL_PROXY_IMAGE)..."
	@echo ""
	# Build and push paude image
	-podman manifest rm $(FULL_IMAGE) 2>/dev/null
	podman manifest create $(FULL_IMAGE)
	podman build --platform $(PLATFORMS) --manifest $(FULL_IMAGE) ./containers/paude
	podman manifest push $(FULL_IMAGE) $(FULL_IMAGE)
	# Tag as latest
	-podman manifest rm $(LATEST_IMAGE) 2>/dev/null
	podman manifest create $(LATEST_IMAGE)
	podman build --platform $(PLATFORMS) --manifest $(LATEST_IMAGE) ./containers/paude
	podman manifest push $(LATEST_IMAGE) $(LATEST_IMAGE)
	@echo ""
	# Build and push proxy image
	-podman manifest rm $(FULL_PROXY_IMAGE) 2>/dev/null
	podman manifest create $(FULL_PROXY_IMAGE)
	podman build --platform $(PLATFORMS) --manifest $(FULL_PROXY_IMAGE) ./containers/proxy
	podman manifest push $(FULL_PROXY_IMAGE) $(FULL_PROXY_IMAGE)
	# Tag as latest
	-podman manifest rm $(LATEST_PROXY_IMAGE) 2>/dev/null
	podman manifest create $(LATEST_PROXY_IMAGE)
	podman build --platform $(PLATFORMS) --manifest $(LATEST_PROXY_IMAGE) ./containers/proxy
	podman manifest push $(LATEST_PROXY_IMAGE) $(LATEST_PROXY_IMAGE)
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
	@SCRIPT_VERSION=$$(grep '^PAUDE_VERSION=' paude | cut -d'"' -f2); \
	if [ "$$SCRIPT_VERSION" != "$(VERSION)" ]; then \
		echo "Error: Script version ($$SCRIPT_VERSION) doesn't match VERSION ($(VERSION))"; \
		echo "Run 'make release VERSION=$(VERSION)' first to update the script"; \
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
	# Update version in paude script
	sed -i.bak 's/^PAUDE_VERSION=.*/PAUDE_VERSION="$(VERSION)"/' paude && rm -f paude.bak
	# Commit the version change
	git add paude
	git commit -m "Release v$(VERSION)"
	# Create git tag
	git tag -a "v$(VERSION)" -m "Release v$(VERSION)"
	@echo ""
	@echo "Version updated and tagged. Now run:"
	@echo "  make publish VERSION=$(VERSION)"
	@echo "  git push origin main --tags"
	@echo ""
	@echo "Then create a GitHub release at:"
	@echo "  https://github.com/bbrowning/paude/releases/new?tag=v$(VERSION)"
	@echo "and attach the 'paude' script."

# Remove local images
clean:
	-podman rmi $(IMAGE_NAME):latest 2>/dev/null
	-podman rmi $(PROXY_IMAGE_NAME):latest 2>/dev/null
	-podman manifest rm $(FULL_IMAGE) 2>/dev/null
	-podman manifest rm $(FULL_PROXY_IMAGE) 2>/dev/null
	-podman manifest rm $(LATEST_IMAGE) 2>/dev/null
	-podman manifest rm $(LATEST_PROXY_IMAGE) 2>/dev/null
	@echo "Cleaned up local images"
