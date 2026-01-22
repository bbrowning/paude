# Research: Migrating Paude from Bash to Python

This document captures research findings for migrating the paude CLI tool from Bash to Python.

## Current Codebase Analysis

### Overview

Paude is a Podman wrapper (v0.3.0) that runs Claude Code inside a container for isolated, secure usage with Google Vertex AI authentication. The current implementation is bash-based with structured configuration parsing, container orchestration, and security features.

### File Structure

```
paude/
├── paude                    # Main bash script (~500 lines)
├── lib/                     # Bash library modules
│   ├── config.sh           # Configuration detection/parsing (~250 lines)
│   ├── hash.sh             # Deterministic hash computation (~50 lines)
│   └── features.sh         # Dev container feature support (~200 lines)
├── containers/
│   ├── paude/              # Main container
│   │   ├── Dockerfile
│   │   └── entrypoint.sh
│   └── proxy/              # Squid proxy container
│       ├── Dockerfile
│       ├── entrypoint.sh
│       └── squid.conf
├── tests/                   # Integration tests
│   ├── run_tests.sh
│   ├── test_helpers.sh
│   ├── test_cli_args.sh
│   ├── test_functions.sh
│   ├── test_mounts.sh
│   └── test_entrypoints.sh
└── test/                    # Unit tests for lib modules
    ├── test_config.sh
    └── test_hash.sh
```

### Main Script Functions

| Function | Purpose | Python Equivalent |
|----------|---------|-------------------|
| `show_help()` | Display usage info | CLI framework auto-generates |
| `show_version()` | Show version and mode | Simple function |
| `show_dry_run()` | Preview config without running | Config dump function |
| `resolve_path()` | Resolve symlinks to physical paths | `pathlib.Path.resolve()` |
| `show_macos_volume_help()` | Guide for Podman machine volumes | Print helper |
| `check_requirements()` | Verify podman installed | `shutil.which()` check |
| `prepare_build_dir()` | Create temp build directory | `tempfile.mkdtemp()` |
| `cleanup_build_dir()` | Clean up artifacts | `shutil.rmtree()` |
| `ensure_images()` | Orchestrate image availability | Container manager class |
| `ensure_default_image()` | Pull/build default image | Image manager method |
| `ensure_custom_image()` | Build workspace image | Image manager method |
| `ensure_proxy_image()` | Pull/build proxy image | Image manager method |
| `setup_environment()` | Vertex AI env vars | Env builder function |
| `setup_mounts()` | Configure volume mounts | Mount builder function |
| `check_macos_volumes()` | Verify Podman machine config | macOS helper |
| `check_git_safety()` | Warn about missing remotes | Git safety check |
| `setup_proxy()` | Create network and start proxy | Network/proxy manager |
| `run_claude()` | Execute Claude in container | Container runner |

### Library Modules

#### config.sh
- Detects config files (`.devcontainer/devcontainer.json`, `.devcontainer.json`, `paude.json`)
- Parses JSON using `jq`
- Extracts: base image, dockerfile path, build args, features, post-create command, container env, packages
- Generates wrapper Dockerfiles
- Handles both devcontainer.json and paude.json formats

#### hash.sh
- Computes deterministic 12-char SHA256 hash
- Inputs: config file, dockerfile, base image, entrypoint.sh
- Used for image caching: `paude-workspace:<hash>`

#### features.sh
- Downloads OCI artifacts from ghcr.io
- Supports: ORAS, skopeo, or curl fallback
- Caches in `~/.cache/paude/features/`
- Generates Dockerfile layers for feature installation

### Container Architecture

**Main Container:**
- Based on `node:22-slim`
- Installs: git, curl, wget, jq, make, Claude Code
- Creates non-root `paude` user
- Entrypoint copies seed config files

**Proxy Container:**
- Based on `ubuntu/squid:latest`
- Allowlists: `*.googleapis.com`, `*.google.com`
- Blocks all other domains
- DNS injection for macOS Podman machine

### Volume Mounts

| Source | Destination | Mode | Purpose |
|--------|-------------|------|---------|
| CWD | Same path | rw | Workspace |
| `~/.config/gcloud` | `/home/paude/.config/gcloud` | ro | Vertex AI creds |
| `~/.claude` | `/tmp/claude.seed` | ro | Config (copied in) |
| `~/.claude/plugins` | Same host path | ro | Plugins |
| `~/.gitconfig` | `/home/paude/.gitconfig` | ro | Git identity |
| `~/.claude.json` | `/tmp/claude.json.seed` | ro | Global config |

### CLI Flags

| Flag | Purpose |
|------|---------|
| `-h`, `--help` | Show help |
| `-V`, `--version` | Show version |
| `--dry-run` | Preview without running |
| `--allow-network` | Full internet access |
| `--yolo` | Skip permission prompts |
| `--force-rebuild` | Force image rebuild |
| `--` | Separator for Claude args |

### Test Coverage

**Integration Tests (tests/):**
- 16 CLI argument tests
- 8 function tests
- 7 mount tests
- 5 entrypoint tests

**Unit Tests (test/):**
- 7 config detection/parsing tests
- 3 hash computation tests

---

## Python CLI Framework Comparison

### Options Evaluated

| Library | Dependencies | Best For |
|---------|-------------|----------|
| argparse | None (stdlib) | Simple scripts, no deps |
| Click | External | Complex CLIs, mature ecosystem |
| Typer | External (on Click) | Modern Python, type hints |

### Recommendation: Typer

**Rationale:**
1. **Type hints native** - Aligns with project's Python best practices goals
2. **Minimal boilerplate** - Parameters extracted from function signatures
3. **Built on Click** - Production-tested foundation
4. **Auto-generated help** - Rich help pages from docstrings and types
5. **Subcommand support** - Natural function separation
6. **Shell completion** - Built-in support
7. **Testing support** - `CliRunner` for testing

**Example Migration:**

Bash:
```bash
show_help() {
    cat <<EOF
Usage: paude [OPTIONS] [-- CLAUDE_ARGS...]
EOF
}
```

Python with Typer:
```python
import typer

app = typer.Typer()

@app.command()
def main(
    allow_network: bool = typer.Option(False, "--allow-network", help="Full internet access"),
    yolo: bool = typer.Option(False, "--yolo", help="Skip permission prompts"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without running"),
    force_rebuild: bool = typer.Option(False, "--force-rebuild", help="Force image rebuild"),
    claude_args: list[str] = typer.Argument(None, help="Arguments passed to Claude"),
):
    """Run Claude Code inside a container with Vertex AI authentication."""
    ...
```

---

## Python Project Structure

### Recommended Layout: src Layout

```
paude/
├── src/
│   └── paude/
│       ├── __init__.py
│       ├── __main__.py      # Entry point: python -m paude
│       ├── cli.py           # Typer CLI definitions
│       ├── config/
│       │   ├── __init__.py
│       │   ├── detector.py  # Config file detection
│       │   ├── parser.py    # JSON parsing
│       │   └── models.py    # Pydantic/dataclass models
│       ├── container/
│       │   ├── __init__.py
│       │   ├── image.py     # Image building/pulling
│       │   ├── runner.py    # Container execution
│       │   └── network.py   # Network/proxy setup
│       ├── features/
│       │   ├── __init__.py
│       │   ├── downloader.py
│       │   └── installer.py
│       ├── hash.py          # Config hashing
│       ├── mounts.py        # Volume mount builder
│       └── utils.py         # Path resolution, etc.
├── tests/
│   ├── __init__.py
│   ├── conftest.py          # Shared fixtures
│   ├── test_cli.py
│   ├── test_config.py
│   ├── test_container.py
│   ├── test_features.py
│   ├── test_hash.py
│   └── test_mounts.py
├── containers/              # Keep container definitions
│   ├── paude/
│   └── proxy/
├── pyproject.toml
├── README.md
└── LICENSE
```

### Benefits of src Layout
- Tests import installed package, not local files
- Catches import/packaging errors early
- Clear separation between source and tests
- Standard for distributable packages

---

## pyproject.toml Configuration

### Recommended Structure

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "paude"
version = "0.4.0"  # Increment for Python port
description = "Run Claude Code inside a container with Vertex AI authentication"
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
authors = [
    {name = "Ben Browning", email = "..."}
]
keywords = ["claude", "container", "podman", "vertex-ai"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Software Development",
]
dependencies = [
    "typer>=0.9.0",
    "rich>=13.0.0",  # Pretty printing (Typer dependency)
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=4.0.0",
    "ruff>=0.3.0",
    "mypy>=1.8.0",
]

[project.scripts]
paude = "paude.cli:app"

[tool.hatch.build.targets.wheel]
packages = ["src/paude"]

[tool.ruff]
line-length = 88
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N", "S", "A", "C4", "PT"]
ignore = ["S603", "S607"]  # subprocess security (expected for podman)

[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_configs = true

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = ["--import-mode=importlib"]
```

---

## Type Hints Best Practices

### Modern Syntax (Python 3.11+)

```python
# Use native generics, not typing.List/Dict
def get_mounts(workspace: Path, home: Path) -> list[str]:
    ...

# Use | for unions instead of Union
def get_config(path: Path | None = None) -> Config | None:
    ...

# Use dataclasses or Pydantic for config models
from dataclasses import dataclass

@dataclass
class ContainerConfig:
    base_image: str
    dockerfile: Path | None = None
    build_context: Path | None = None
    features: list[str] = field(default_factory=list)
    post_create_command: str | None = None
    container_env: dict[str, str] = field(default_factory=dict)
    packages: list[str] = field(default_factory=list)
```

### Gradual Adoption Strategy
1. Start with public API functions
2. Add types to data models first
3. Use `# type: ignore` sparingly
4. Run mypy in CI to catch regressions

---

## Testing Strategy

### Framework: pytest

**Advantages:**
- De facto Python testing standard
- Powerful fixtures system
- Excellent CLI testing support via `typer.testing.CliRunner`
- Parameterized tests reduce duplication

### Test Structure

```python
# tests/test_cli.py
from typer.testing import CliRunner
from paude.cli import app

runner = CliRunner()

def test_help_flag():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Run Claude Code" in result.stdout

def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.4.0" in result.stdout

@pytest.mark.parametrize("flag,expected", [
    ("--dry-run", "Configuration:"),
    ("--allow-network", "network"),
])
def test_flags(flag, expected):
    result = runner.invoke(app, [flag, "--dry-run"])
    assert expected in result.stdout
```

### Fixture Examples

```python
# tests/conftest.py
import pytest
from pathlib import Path
import tempfile
import shutil

@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    return tmp_path

@pytest.fixture
def devcontainer_workspace(workspace: Path) -> Path:
    """Workspace with .devcontainer/devcontainer.json."""
    devcontainer = workspace / ".devcontainer"
    devcontainer.mkdir()
    (devcontainer / "devcontainer.json").write_text('{"image": "python:3.11"}')
    return workspace

@pytest.fixture
def mock_podman(monkeypatch):
    """Mock podman command to avoid running real containers."""
    def mock_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", mock_run)
```

### Test Migration Mapping

| Bash Test File | Python Test File |
|----------------|------------------|
| tests/test_cli_args.sh | tests/test_cli.py |
| tests/test_functions.sh | tests/test_utils.py |
| tests/test_mounts.sh | tests/test_mounts.py |
| tests/test_entrypoints.sh | tests/test_entrypoints.py |
| test/test_config.sh | tests/test_config.py |
| test/test_hash.sh | tests/test_hash.py |

---

## Container Interaction Options

### Option 1: subprocess with podman CLI (Recommended)

```python
import subprocess
from pathlib import Path

def run_podman(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["podman", *args],
        capture_output=True,
        text=True,
        check=check,
    )

def build_image(dockerfile: Path, tag: str, context: Path) -> None:
    run_podman("build", "-t", tag, "-f", str(dockerfile), str(context))

def run_container(image: str, mounts: list[str], env: dict[str, str]) -> None:
    cmd = ["run", "--rm", "-it"]
    for mount in mounts:
        cmd.extend(["-v", mount])
    for key, value in env.items():
        cmd.extend(["-e", f"{key}={value}"])
    cmd.append(image)
    run_podman(*cmd)
```

**Advantages:**
- Exact parity with current bash implementation
- No additional dependencies
- Podman CLI is well-documented
- Easy to debug (can print commands)

### Option 2: Podman Python SDK

```python
from podman import PodmanClient

with PodmanClient() as client:
    client.containers.run(
        "paude:latest",
        volumes={"/workspace": {"bind": "/workspace", "mode": "rw"}},
        environment={"CLAUDE_CODE_USE_VERTEX": "1"},
    )
```

**Disadvantages:**
- Additional dependency
- Less parity with bash (harder to verify equivalence)
- Socket configuration complexity (rootless vs rootful)
- API may differ from docker-py

### Recommendation: subprocess

Use subprocess for direct podman CLI interaction. This ensures:
- Exact functional parity with bash version
- Easy migration verification
- No additional dependencies
- Simple debugging

---

## Linting and Formatting

### Tool: Ruff

**Why Ruff:**
- 10-100x faster than Flake8/Black
- Replaces: Flake8, Black, isort, pyupgrade
- Single tool, single config
- Written in Rust, actively maintained

### Configuration

```toml
[tool.ruff]
line-length = 88
target-version = "py311"

[tool.ruff.lint]
select = [
    "E",   # pycodestyle errors
    "F",   # Pyflakes
    "I",   # isort
    "B",   # flake8-bugbear
    "UP",  # pyupgrade
    "N",   # pep8-naming
    "S",   # flake8-bandit (security)
    "A",   # flake8-builtins
    "C4",  # flake8-comprehensions
    "PT",  # flake8-pytest-style
]

# Ignore subprocess security warnings (expected for podman)
ignore = ["S603", "S607"]

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S101"]  # Allow assert in tests

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

### Pre-commit Integration

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.3.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

---

## Installation Methods

### Current (Bash)

```bash
# Download script
curl -O https://github.com/.../paude
chmod +x paude
./paude
```

### Python Options

#### 1. pipx (Recommended for CLI tools)

```bash
pipx install paude
```

**Advantages:**
- Isolated environment
- No dependency conflicts
- Easy updates: `pipx upgrade paude`

#### 2. pip (User install)

```bash
pip install --user paude
```

#### 3. From source

```bash
git clone https://github.com/.../paude
cd paude
pip install -e .
```

### Distribution

**PyPI:**
- Build: `python -m build`
- Upload: `twine upload dist/*`

**GitHub Releases:**
- Source tarball
- Wheel file
- Keep script for legacy support during transition

---

## Compatibility Considerations

### Breaking Changes (Acceptable)

1. **Installation method** - pip/pipx instead of curl script
2. **Python requirement** - Python 3.11+ required
3. **Error message format** - May differ slightly

### Preserved Behavior (Required)

1. **All CLI flags** - Same flags, same behavior
2. **Config file detection** - Same priority order
3. **Volume mounts** - Identical mount points
4. **Security model** - Same restrictions
5. **Image building** - Same Dockerfile generation
6. **Hash computation** - Same cache keys
7. **Proxy behavior** - Same network filtering

### Migration Path

1. Release Python version as v0.4.0
2. Deprecate bash script (v0.3.x)
3. Document migration in README
4. Keep bash script available for one release cycle

---

## Key Dependencies

### Required

| Package | Purpose | Version |
|---------|---------|---------|
| typer | CLI framework | >=0.9.0 |
| rich | Terminal output | >=13.0.0 |

### Development

| Package | Purpose | Version |
|---------|---------|---------|
| pytest | Testing | >=8.0.0 |
| pytest-cov | Coverage | >=4.0.0 |
| ruff | Linting/Formatting | >=0.3.0 |
| mypy | Type checking | >=1.8.0 |

### System Requirements

| Tool | Purpose |
|------|---------|
| podman | Container runtime |
| git | Git operations |

---

## Risk Assessment

### Low Risk

- CLI argument parsing (well-defined, testable)
- Help/version output (trivial)
- Path resolution (stdlib pathlib)
- Environment variable handling (os.environ)

### Medium Risk

- JSON parsing (jq → json module, different error handling)
- Dockerfile generation (string manipulation)
- Hash computation (must produce identical results)
- Feature downloading (HTTP client differences)

### High Risk

- macOS Podman machine interaction (platform-specific)
- Network/proxy setup (multi-container orchestration)
- Subprocess error handling (podman exit codes)
- Signal handling (SIGINT/SIGTERM during container run)

### Mitigation

1. **Comprehensive test coverage** before migration
2. **Side-by-side testing** of bash vs Python outputs
3. **Integration tests** with real podman
4. **Beta testing period** before GA

---

## References

### CLI Frameworks
- [Typer Documentation](https://typer.tiangolo.com/)
- [Click Documentation](https://click.palletsprojects.com/)
- [Typer Alternatives Comparison](https://typer.tiangolo.com/alternatives/)

### Project Structure
- [Python Packaging Guide - src layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)
- [PyOpenSci Package Structure](https://www.pyopensci.org/python-package-guide/package-structure-code/python-package-structure.html)
- [pyproject.toml Guide](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)

### Type Hints
- [Python typing module](https://docs.python.org/3/library/typing.html)
- [mypy Documentation](https://mypy.readthedocs.io/)
- [Type Hints Best Practices](https://betterstack.com/community/guides/scaling-python/python-type-hints/)

### Testing
- [pytest Documentation](https://docs.pytest.org/)
- [Testing CLI Applications with pytest](https://pytest-with-eric.com/pytest-advanced/pytest-argparse-typer/)
- [Python CLI Testing Techniques](https://realpython.com/python-cli-testing/)

### Linting
- [Ruff Documentation](https://docs.astral.sh/ruff/)
- [Ruff Configuration Guide](https://docs.astral.sh/ruff/configuration/)
