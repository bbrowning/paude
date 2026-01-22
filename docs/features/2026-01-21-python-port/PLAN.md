# Plan: Migrating Paude from Bash to Python

This document outlines the comprehensive plan for migrating paude from Bash to Python while maintaining identical functionality.

## Goals

1. **Functional parity** - All existing features work identically
2. **Python best practices** - Type hints, proper structure, linting
3. **Improved maintainability** - Easier to extend and debug
4. **Better testing** - pytest with fixtures and coverage
5. **Standard distribution** - PyPI package with pip/pipx install

## Non-Goals

1. Adding new features during migration
2. Changing CLI interface or flags
3. Changing container behavior
4. Changing security model

---

## Phase 1: Project Scaffolding

### Objective
Set up Python project structure with all tooling configured.

### Tasks

1. **Create directory structure**
   ```
   src/paude/__init__.py
   src/paude/__main__.py
   src/paude/cli.py
   tests/__init__.py
   tests/conftest.py
   ```

2. **Create pyproject.toml**
   - Hatchling build backend
   - Project metadata (name, version, description)
   - Dependencies: typer, rich
   - Dev dependencies: pytest, pytest-cov, ruff, mypy
   - Entry point: `paude = "paude.cli:app"`
   - Tool configs: ruff, mypy, pytest

3. **Set up tooling**
   - `.pre-commit-config.yaml` for ruff
   - Update `.gitignore` for Python artifacts
   - Create Makefile targets for Python

4. **Verify setup**
   - `pip install -e .` works
   - `paude --help` runs (stub)
   - `make test` runs pytest
   - `make lint` runs ruff and mypy

### Acceptance Criteria
- [ ] Project installs with `pip install -e .`
- [ ] `paude --help` shows basic help
- [ ] `ruff check src tests` passes
- [ ] `mypy src` passes
- [ ] `pytest` runs (with placeholder test)

---

## Phase 2: CLI Skeleton

### Objective
Implement CLI argument parsing with Typer matching current interface.

### Tasks

1. **Create cli.py with Typer app**
   ```python
   @app.command()
   def main(
       allow_network: bool = Option(False, "--allow-network"),
       yolo: bool = Option(False, "--yolo"),
       dry_run: bool = Option(False, "--dry-run"),
       force_rebuild: bool = Option(False, "--force-rebuild"),
       version: bool = Option(False, "-V", "--version", callback=version_callback),
       claude_args: list[str] = Argument(None),
   ):
   ```

2. **Implement version callback**
   - Show version with dev mode detection
   - Show registry if custom

3. **Implement help formatting**
   - Match current help output style
   - Include examples section
   - Include security section

4. **Create __main__.py**
   ```python
   from paude.cli import app
   app()
   ```

### Acceptance Criteria
- [ ] `paude --help` matches bash output structure
- [ ] `paude --version` shows version
- [ ] `PAUDE_DEV=1 paude --version` shows dev mode
- [ ] `PAUDE_REGISTRY=foo paude --version` shows registry
- [ ] Unknown args captured in claude_args

---

## Phase 3: Configuration Module

### Objective
Port lib/config.sh to Python with identical behavior.

### Tasks

1. **Create config data models** (`src/paude/config/models.py`)
   ```python
   @dataclass
   class PaudeConfig:
       config_file: Path | None
       config_type: Literal["default", "devcontainer", "paude"]
       base_image: str | None
       dockerfile: Path | None
       build_context: Path | None
       features: list[FeatureSpec]
       post_create_command: str | None
       container_env: dict[str, str]
       packages: list[str]
       build_args: dict[str, str]
   ```

2. **Create config detector** (`src/paude/config/detector.py`)
   - `detect_config(workspace: Path) -> Path | None`
   - Priority: `.devcontainer/devcontainer.json` > `.devcontainer.json` > `paude.json`

3. **Create config parser** (`src/paude/config/parser.py`)
   - `parse_config(config_file: Path) -> PaudeConfig`
   - Parse devcontainer.json format
   - Parse paude.json format
   - Warn about unsupported properties

4. **Create Dockerfile generator** (`src/paude/config/dockerfile.py`)
   - `generate_workspace_dockerfile(config: PaudeConfig) -> str`
   - Match exact Dockerfile output from bash

5. **Port helper functions**
   - `has_custom_config(config: PaudeConfig) -> bool`
   - `needs_custom_build(config: PaudeConfig) -> bool`

### Acceptance Criteria
- [ ] Config detection finds same files as bash
- [ ] JSON parsing handles all fields
- [ ] Generated Dockerfile matches bash output byte-for-byte
- [ ] Unsupported property warnings match
- [ ] All test/test_config.sh tests pass equivalent

---

## Phase 4: Hash Module

### Objective
Port lib/hash.sh to Python with identical hash output.

### Tasks

1. **Create hash module** (`src/paude/hash.py`)
   ```python
   def compute_config_hash(
       config_file: Path | None,
       dockerfile: Path | None,
       base_image: str | None,
       entrypoint: Path,
   ) -> str:
       """Return 12-char SHA256 hash of inputs."""
   ```

2. **Ensure byte-identical hashing**
   - Read files in binary mode
   - Same concatenation order as bash
   - Trim to 12 characters

3. **Create staleness check**
   ```python
   def is_image_stale(image_tag: str) -> bool:
       """Check if image with hash tag exists."""
   ```

### Acceptance Criteria
- [ ] Same inputs produce identical hash as bash
- [ ] Hash length is 12 characters
- [ ] `is_image_stale` correctly checks podman images

---

## Phase 5: Features Module

### Objective
Port lib/features.sh for dev container feature support.

### Tasks

1. **Create feature downloader** (`src/paude/features/downloader.py`)
   - `download_feature(feature_url: str) -> Path`
   - ORAS fallback to skopeo fallback to curl
   - Cache in `~/.cache/paude/features/`

2. **Create feature installer** (`src/paude/features/installer.py`)
   - `generate_feature_install_layer(feature_path: Path, options: dict) -> str`
   - Convert options to environment variables
   - Generate COPY and RUN commands

3. **Create cache management**
   - `clear_feature_cache() -> None`
   - Hash-based cache directory naming

### Acceptance Criteria
- [ ] Features download from ghcr.io
- [ ] Fallback chain works (oras → skopeo → curl)
- [ ] Generated Dockerfile layers match bash
- [ ] Cache is reused on repeat downloads

---

## Phase 6: Mount Builder

### Objective
Port setup_mounts() to Python.

### Tasks

1. **Create mount builder** (`src/paude/mounts.py`)
   ```python
   def build_mounts(
       workspace: Path,
       home: Path,
   ) -> list[str]:
       """Return list of -v mount arguments."""
   ```

2. **Handle all mount types**
   - Workspace at same path (rw)
   - gcloud config (ro, if exists)
   - Claude seed directories (ro)
   - Plugins at original path (ro)
   - gitconfig (ro, if exists)
   - claude.json seed (ro, if exists)

3. **Path resolution**
   - Resolve symlinks for workspace
   - Handle missing optional paths

### Acceptance Criteria
- [ ] All mounts match bash output
- [ ] Missing optional paths handled correctly
- [ ] Symlinks resolved for workspace
- [ ] All tests/test_mounts.sh tests pass equivalent

---

## Phase 7: Container Management

### Objective
Port container building and running.

### Tasks

1. **Create image manager** (`src/paude/container/image.py`)
   ```python
   class ImageManager:
       def ensure_default_image(self) -> str: ...
       def ensure_custom_image(self, config: PaudeConfig) -> str: ...
       def ensure_proxy_image(self) -> str: ...
       def build_image(self, dockerfile: Path, tag: str, context: Path) -> None: ...
       def pull_image(self, image: str) -> None: ...
       def image_exists(self, tag: str) -> bool: ...
   ```

2. **Create network manager** (`src/paude/container/network.py`)
   ```python
   class NetworkManager:
       def create_network(self, name: str) -> None: ...
       def remove_network(self, name: str) -> None: ...
       def network_exists(self, name: str) -> bool: ...
   ```

3. **Create container runner** (`src/paude/container/runner.py`)
   ```python
   class ContainerRunner:
       def run_claude(
           self,
           image: str,
           mounts: list[str],
           env: dict[str, str],
           args: list[str],
           network: str | None = None,
       ) -> int: ...

       def run_proxy(
           self,
           image: str,
           network: str,
           dns: str | None = None,
       ) -> str:  # Returns container ID
   ```

4. **Implement subprocess wrapper**
   ```python
   def run_podman(*args: str, check: bool = True) -> subprocess.CompletedProcess: ...
   ```

### Acceptance Criteria
- [ ] Default image pulled/built correctly
- [ ] Custom image built with hash tag
- [ ] Proxy container starts on internal network
- [ ] Claude container runs with correct mounts/env
- [ ] Container cleanup on exit

---

## Phase 8: Environment Setup

### Objective
Port setup_environment() for Vertex AI.

### Tasks

1. **Create environment builder** (`src/paude/environment.py`)
   ```python
   def build_environment() -> dict[str, str]:
       """Build environment variables for container."""
   ```

2. **Handle Vertex AI variables**
   - CLAUDE_CODE_USE_VERTEX=1
   - ANTHROPIC_VERTEX_PROJECT_ID
   - GOOGLE_CLOUD_PROJECT
   - CLOUDSDK_AUTH_* passthrough

3. **Handle proxy environment**
   - HTTP_PROXY, HTTPS_PROXY when using proxy

### Acceptance Criteria
- [ ] All Vertex AI env vars set correctly
- [ ] Proxy env vars set when network restricted
- [ ] CLOUDSDK variables passed through

---

## Phase 9: Platform-Specific Handling

### Objective
Port macOS-specific logic.

### Tasks

1. **Create platform module** (`src/paude/platform.py`)
   ```python
   def is_macos() -> bool: ...
   def check_macos_volumes(workspace: Path) -> bool: ...
   def get_podman_machine_dns() -> str | None: ...
   def show_macos_volume_help(workspace: Path) -> None: ...
   ```

2. **Handle Podman machine detection**
   - Check if workspace outside /Users
   - Suggest volume mount configuration

3. **Handle DNS for proxy**
   - Get DNS from Podman machine config
   - Inject into squid container

### Acceptance Criteria
- [ ] macOS detection works
- [ ] Volume mount warnings trigger correctly
- [ ] DNS injection for proxy works

---

## Phase 10: Utilities

### Objective
Port utility functions.

### Tasks

1. **Create utils module** (`src/paude/utils.py`)
   ```python
   def resolve_path(path: Path) -> Path:
       """Resolve symlinks to physical path."""

   def check_requirements() -> None:
       """Verify podman is installed."""

   def check_git_safety(workspace: Path) -> None:
       """Warn if no git repo or remotes."""
   ```

2. **Port all helper functions from main script**

### Acceptance Criteria
- [ ] Path resolution matches bash
- [ ] Requirement check fails gracefully
- [ ] Git safety warnings match

---

## Phase 11: Dry-Run Mode

### Objective
Implement --dry-run with identical output.

### Tasks

1. **Create dry-run formatter** (`src/paude/dry_run.py`)
   ```python
   def show_dry_run(
       config: PaudeConfig,
       flags: Flags,
       mounts: list[str],
       env: dict[str, str],
   ) -> None:
   ```

2. **Format all sections**
   - Configuration summary
   - Generated Dockerfile (if custom)
   - Flags and their states
   - Image to be used

### Acceptance Criteria
- [ ] Output matches bash --dry-run
- [ ] Shows Dockerfile for custom configs
- [ ] Shows all flags correctly

---

## Phase 12: Main Orchestration

### Objective
Wire everything together in cli.py main function.

### Tasks

1. **Implement main flow**
   ```python
   def main(...):
       # 1. Parse config
       config = detect_and_parse_config(workspace)

       # 2. Dry-run check
       if dry_run:
           show_dry_run(config, ...)
           return

       # 3. Check requirements
       check_requirements()

       # 4. Ensure images
       image = ensure_images(config, force_rebuild)

       # 5. Setup mounts and env
       mounts = build_mounts(workspace, home)
       env = build_environment()

       # 6. Platform checks
       check_macos_volumes(workspace)
       check_git_safety(workspace)

       # 7. Setup proxy if needed
       if not allow_network:
           setup_proxy()

       # 8. Run Claude
       run_claude(image, mounts, env, claude_args)
   ```

2. **Handle signal cleanup**
   - SIGINT/SIGTERM cleanup of proxy container
   - Network cleanup

3. **Error handling**
   - Graceful podman errors
   - Missing config file errors

### Acceptance Criteria
- [ ] Full flow works end-to-end
- [ ] Cleanup on interrupt
- [ ] Error messages are helpful

---

## Phase 13: Test Migration

### Objective
Port all tests to pytest.

### Tasks

1. **Create test fixtures** (`tests/conftest.py`)
   - `workspace` - temp directory
   - `devcontainer_workspace` - with devcontainer.json
   - `paude_json_workspace` - with paude.json
   - `mock_podman` - subprocess mock
   - `mock_home` - temp home directory

2. **Port CLI tests** (`tests/test_cli.py`)
   - All tests from test_cli_args.sh
   - Use CliRunner for invocation

3. **Port config tests** (`tests/test_config.py`)
   - All tests from test/test_config.sh
   - Detection, parsing, Dockerfile generation

4. **Port hash tests** (`tests/test_hash.py`)
   - All tests from test/test_hash.sh
   - Verify hash equivalence with bash

5. **Port mount tests** (`tests/test_mounts.py`)
   - All tests from test_mounts.sh
   - Verify mount string output

6. **Port function tests** (`tests/test_utils.py`)
   - All tests from test_functions.sh
   - Path resolution, env setup

7. **Create integration tests** (`tests/test_integration.py`)
   - End-to-end with mock podman
   - Real podman (marked slow)

### Acceptance Criteria
- [ ] All bash tests have Python equivalents
- [ ] Test coverage ≥ 80%
- [ ] `make test` runs all tests
- [ ] CI runs tests on push

---

## Phase 14: Documentation

### Objective
Update documentation for Python version.

### Tasks

1. **Update README.md**
   - Installation: pip/pipx instructions
   - Python version requirement
   - Development setup

2. **Update CONTRIBUTING.md**
   - Python development workflow
   - Testing instructions
   - Linting/formatting

3. **Create migration guide**
   - Changes from bash version
   - Breaking changes (if any)
   - Upgrade instructions

4. **Update show_help()**
   - Verify help text matches README

### Acceptance Criteria
- [ ] README has Python install instructions
- [ ] CONTRIBUTING documents Python workflow
- [ ] Migration guide covers all changes

---

## Phase 15: CI/CD Updates

### Objective
Update automation for Python.

### Tasks

1. **Update GitHub Actions**
   - Python setup
   - pip install
   - pytest with coverage
   - ruff lint
   - mypy type check

2. **Update Makefile**
   ```makefile
   .PHONY: install test lint format typecheck

   install:
       pip install -e ".[dev]"

   test:
       pytest --cov=paude

   lint:
       ruff check src tests

   format:
       ruff format src tests

   typecheck:
       mypy src
   ```

3. **PyPI publishing**
   - Build workflow
   - Publish on tag

### Acceptance Criteria
- [ ] CI runs on PRs
- [ ] All checks pass
- [ ] PyPI publish works

---

## Phase 16: Release

### Objective
Release Python version as v0.4.0.

### Tasks

1. **Version bump**
   - Set version to 0.4.0 in pyproject.toml
   - Update __init__.py version

2. **Final testing**
   - Full test suite
   - Manual testing on Linux and macOS
   - Integration test with real Vertex AI

3. **Release notes**
   - Document Python port
   - Highlight any behavior changes
   - Migration instructions

4. **Publish**
   - Tag v0.4.0
   - PyPI upload
   - GitHub release

5. **Deprecation notice**
   - Mark bash script as deprecated
   - Keep available for one release cycle

### Acceptance Criteria
- [ ] v0.4.0 on PyPI
- [ ] `pipx install paude` works
- [ ] All functionality identical to bash
- [ ] Release notes published

---

## Risk Mitigation

### Hash Compatibility

**Risk**: Python produces different hash than bash.

**Mitigation**:
- Create test that runs both bash and Python hash functions
- Compare outputs on identical inputs
- Run before merging Phase 4

### macOS Compatibility

**Risk**: Platform-specific code behaves differently.

**Mitigation**:
- Test on both Linux and macOS
- CI includes macOS runner
- Manual testing on macOS before release

### Subprocess Behavior

**Risk**: podman subprocess calls differ from bash.

**Mitigation**:
- Capture and compare actual podman commands
- Integration tests with real podman
- Mock tests verify command structure

### Signal Handling

**Risk**: Container cleanup fails on interrupt.

**Mitigation**:
- Test SIGINT handling explicitly
- Use atexit and signal handlers
- Test container cleanup

---

## Timeline Estimate

| Phase | Dependency | Complexity |
|-------|------------|------------|
| 1. Scaffolding | None | Low |
| 2. CLI Skeleton | Phase 1 | Low |
| 3. Config Module | Phase 1 | Medium |
| 4. Hash Module | Phase 1 | Low |
| 5. Features Module | Phase 3 | Medium |
| 6. Mount Builder | Phase 1 | Low |
| 7. Container Mgmt | Phase 3, 4 | High |
| 8. Environment | Phase 1 | Low |
| 9. Platform | Phase 1 | Medium |
| 10. Utilities | Phase 1 | Low |
| 11. Dry-Run | Phase 3, 6, 8 | Low |
| 12. Orchestration | Phase 2-11 | High |
| 13. Test Migration | Phase 12 | Medium |
| 14. Documentation | Phase 12 | Low |
| 15. CI/CD | Phase 13 | Low |
| 16. Release | Phase 14, 15 | Low |

### Suggested Order

1. Phase 1: Scaffolding
2. Phase 2: CLI Skeleton
3. Phases 3, 4, 6, 8, 10 (in parallel - no deps)
4. Phase 5: Features (needs config)
5. Phase 7: Container Management
6. Phase 9: Platform
7. Phase 11: Dry-Run
8. Phase 12: Orchestration
9. Phase 13: Tests
10. Phases 14, 15 (in parallel)
11. Phase 16: Release

---

## Success Metrics

1. **Functional Parity**
   - All bash tests pass as Python tests
   - Manual testing shows identical behavior

2. **Code Quality**
   - 100% type coverage (mypy strict)
   - Ruff passes with no ignores (except subprocess)
   - Test coverage ≥ 80%

3. **Performance**
   - Startup time ≤ bash version
   - No noticeable slowdown

4. **Distribution**
   - PyPI package works
   - pipx install works
   - Works on Python 3.11, 3.12

---

## Open Questions

1. **Minimum Python version**: 3.11 or 3.12?
   - Recommendation: 3.11 (wider compatibility)

2. **Keep bash script in repo?**
   - Recommendation: Yes, deprecate but keep for one release

3. **Version number**: 0.4.0 or 1.0.0?
   - Recommendation: 0.4.0 (indicates evolution, not rewrite)

4. **Pydantic vs dataclasses for models?**
   - Recommendation: dataclasses (no extra dependency)
