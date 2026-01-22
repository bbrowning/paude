# Python Port Task List

This document contains the comprehensive task list for porting paude from Bash to Python.
It is designed to be followed by Claude Code in a Ralph Wiggum-style loop until completion.

## Prerequisites (Host Setup)

Before the implementation loop begins, these tasks must be completed **on the host machine**
(not inside a paude container) because they require changes to how paude containers are built.

### PREREQ-1: Update paude.json for Python Development

**Location**: `/Volumes/SourceCode/paude/paude.json`

**Current state**:
```json
{
    "base": "node:22-slim",
    "packages": [
        "git",
        "curl",
        "wget",
        "dnsutils",
        "iputils-ping",
        "jq",
        "perl",
        "make"
    ]
}
```

**Required changes**: Add Python 3.11+ and pip to support Python development:
```json
{
    "base": "node:22-slim",
    "packages": [
        "git",
        "curl",
        "wget",
        "dnsutils",
        "iputils-ping",
        "jq",
        "perl",
        "make",
        "python3",
        "python3-pip",
        "python3-venv"
    ]
}
```

**Why**: The implementation tasks require Python tooling (pytest, ruff, mypy) to run inside
the container. Without Python installed, the implementation loop cannot run tests or linting.

**Verification**: After updating, rebuild the container image:
```bash
# Force rebuild to pick up the new packages
PAUDE_DEV=1 ./paude --rebuild --dry-run
```

---

## Implementation Tasks

These tasks should be completed inside a paude container, in order. Each task has clear
acceptance criteria that must all pass before moving to the next task.

### Phase 1: Project Scaffolding

#### TASK-1.1: Create Python Project Structure

**Description**: Create the basic directory structure for the Python package.

**Files to create**:
```
src/paude/__init__.py           # Package with version
src/paude/__main__.py           # Entry point: python -m paude
src/paude/cli.py                # Typer CLI placeholder
src/paude/config/__init__.py    # Config subpackage marker
src/paude/container/__init__.py # Container subpackage marker
src/paude/features/__init__.py  # Features subpackage marker
tests/__init__.py               # Test package marker
tests/conftest.py               # Pytest fixtures placeholder
```

**Acceptance criteria**:
- [ ] `src/paude/__init__.py` exists with `__version__ = "0.4.0"`
- [ ] `src/paude/__main__.py` exists and imports from cli
- [ ] `src/paude/cli.py` exists with basic Typer app stub
- [ ] `tests/__init__.py` exists (can be empty)
- [ ] `tests/conftest.py` exists (can be empty or with placeholder fixture)

---

#### TASK-1.2: Create pyproject.toml

**Description**: Create the project configuration file with all metadata, dependencies, and tool configs.

**File**: `pyproject.toml`

**Content requirements**:
- Build backend: hatchling
- Project name: paude
- Version: 0.4.0
- Python requirement: >=3.11
- Dependencies: typer>=0.9.0, rich>=13.0.0
- Dev dependencies: pytest>=8.0.0, pytest-cov>=4.0.0, ruff>=0.3.0, mypy>=1.8.0
- Entry point: `paude = "paude.cli:app"`
- Ruff config: line-length=88, select rules E, F, I, B, UP, N, S, A, C4, PT
- Mypy config: python_version="3.11", strict=true
- Pytest config: testpaths=["tests"]

**Acceptance criteria**:
- [ ] `pyproject.toml` exists with all required sections
- [ ] `pip install -e .` completes without error
- [ ] `pip install -e ".[dev]"` installs dev dependencies
- [ ] Entry point script name is `paude`

---

#### TASK-1.3: Update .gitignore for Python

**Description**: Add Python-specific entries to .gitignore.

**File**: `.gitignore` (append to existing)

**Entries to add**:
```
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg
.mypy_cache/
.ruff_cache/
.pytest_cache/
.coverage
htmlcov/
.venv/
venv/
```

**Acceptance criteria**:
- [ ] `.gitignore` contains Python entries
- [ ] `__pycache__/` directories would be ignored
- [ ] `.mypy_cache/` would be ignored

---

#### TASK-1.4: Create Initial Test

**Description**: Create a placeholder test to verify pytest works.

**File**: `tests/test_version.py`

**Content**:
```python
from paude import __version__

def test_version():
    assert __version__ == "0.4.0"
```

**Acceptance criteria**:
- [ ] `pytest tests/test_version.py` passes
- [ ] Version matches pyproject.toml

---

#### TASK-1.5: Verify Tooling

**Description**: Verify all development tools work correctly.

**Commands to run**:
```bash
pip install -e ".[dev]"
ruff check src tests
ruff format --check src tests
mypy src
pytest
```

**Acceptance criteria**:
- [ ] `ruff check` passes with no errors
- [ ] `ruff format --check` passes (no formatting needed)
- [ ] `mypy src` passes with no errors
- [ ] `pytest` runs and passes

---

### Phase 2: CLI Skeleton

#### TASK-2.1: Implement CLI with Typer

**Description**: Create the full CLI interface matching the current bash implementation.

**File**: `src/paude/cli.py`

**Requirements**:
- Create Typer app
- Implement all flags: --help, --version, --yolo, --allow-network, --rebuild, --dry-run
- Handle `--` separator for Claude arguments
- Version callback shows version, dev mode, registry
- Help text matches current bash help output structure

**Reference**: Current help output from bash `paude --help`

**Acceptance criteria**:
- [ ] `paude --help` shows help matching bash structure
- [ ] `paude --version` shows "paude 0.4.0" and mode info
- [ ] `paude -V` works (short flag)
- [ ] `paude -h` works (short flag)
- [ ] Unknown args before `--` are passed to Claude
- [ ] Args after `--` are passed to Claude

---

#### TASK-2.2: Create CLI Tests

**Description**: Create comprehensive tests for CLI argument parsing.

**File**: `tests/test_cli.py`

**Test cases** (match tests/test_cli_args.sh):
1. --help shows help and exits 0
2. -h shows help and exits 0
3. --version shows version and exits 0
4. -V shows version and exits 0
5. --version shows "development" when PAUDE_DEV=1
6. --version shows "installed" when PAUDE_DEV=0
7. --version shows custom registry when PAUDE_REGISTRY is set
8. --dry-run works without podman
9. --dry-run shows "no config" when no config file
10. --dry-run shows flag states (--yolo: true, --allow-network: true)
11. --yolo flag is recognized
12. --allow-network flag is recognized
13. --rebuild flag is recognized
14. --help shows --dry-run option
15. Unknown flags are passed to claude_args
16. Arguments after -- are captured in claude_args
17. Multiple flags work together

**Acceptance criteria**:
- [ ] All 17 test cases pass
- [ ] Tests use CliRunner (no real containers)
- [ ] `pytest tests/test_cli.py` passes

---

### Phase 3: Configuration Module

#### TASK-3.1: Create Config Data Models

**Description**: Create dataclass models for configuration.

**File**: `src/paude/config/models.py`

**Classes**:
```python
@dataclass
class FeatureSpec:
    url: str
    options: dict[str, Any]

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

**Acceptance criteria**:
- [ ] Models import without error
- [ ] Type hints are complete
- [ ] `mypy src/paude/config/models.py` passes

---

#### TASK-3.2: Create Config Detector

**Description**: Implement config file detection matching bash behavior.

**File**: `src/paude/config/detector.py`

**Function**: `detect_config(workspace: Path) -> Path | None`

**Priority order**:
1. `.devcontainer/devcontainer.json`
2. `.devcontainer.json`
3. `paude.json`

**Acceptance criteria**:
- [ ] Returns correct path for each config type
- [ ] Returns None when no config exists
- [ ] Matches bash detect_config() behavior exactly

---

#### TASK-3.3: Create Config Parser

**Description**: Implement config file parsing matching bash behavior.

**File**: `src/paude/config/parser.py`

**Functions**:
- `parse_config(config_file: Path) -> PaudeConfig`
- `_parse_devcontainer(config_file: Path) -> PaudeConfig`
- `_parse_paude_json(config_file: Path) -> PaudeConfig`

**Requirements**:
- Handle devcontainer.json format (image, build.dockerfile, build.context, build.args)
- Handle paude.json format (base, packages, setup)
- Warn about unsupported properties
- Parse features array
- Parse containerEnv
- Parse postCreateCommand (string or array)

**Acceptance criteria**:
- [ ] Parses valid devcontainer.json correctly
- [ ] Parses valid paude.json correctly
- [ ] Invalid JSON raises appropriate error
- [ ] Unsupported properties generate warnings

---

#### TASK-3.4: Create Dockerfile Generator

**Description**: Generate workspace Dockerfiles matching bash output exactly.

**File**: `src/paude/config/dockerfile.py`

**Function**: `generate_workspace_dockerfile(config: PaudeConfig) -> str`

**Requirements**:
- Output must match bash `generate_workspace_dockerfile()` byte-for-byte
- Include user packages from paude.json
- Include standard paude requirements (git, curl, node, claude-code)
- Create paude user
- Copy entrypoint

**Acceptance criteria**:
- [ ] Generated Dockerfile matches bash output exactly
- [ ] Handles both image-based and dockerfile-based configs
- [ ] Packages are included when present

---

#### TASK-3.5: Create Config Tests

**Description**: Port all config tests from test/test_config.sh to pytest.

**File**: `tests/test_config.py`

**Test cases** (match test/test_config.sh):
1. detect_config finds .devcontainer/devcontainer.json
2. detect_config finds .devcontainer.json
3. detect_config finds paude.json
4. detect_config respects priority order
5. detect_config returns None when no config
6. parse_config handles devcontainer with image
7. parse_config handles devcontainer with dockerfile and context
8. parse_config resolves relative dockerfile paths correctly
9. parse_config resolves relative build context paths correctly
10. parse_config handles paude.json with packages
11. parse_config handles paude.json with setup command
12. parse_config handles invalid JSON (returns error)
13. warn_unsupported_properties logs warnings for mounts, runArgs, etc.
14. generate_workspace_dockerfile matches expected output
15. generate_workspace_dockerfile includes packages when present

**Acceptance criteria**:
- [ ] All 15 test cases pass
- [ ] Tests use temp directories (no real filesystem side effects)
- [ ] `pytest tests/test_config.py` passes

---

### Phase 4: Hash Module

#### TASK-4.1: Implement Hash Computation

**Description**: Port hash computation to Python with identical output.

**File**: `src/paude/hash.py`

**Functions**:
- `compute_config_hash(config_file: Path | None, dockerfile: Path | None, base_image: str | None, entrypoint: Path) -> str`
- `is_image_stale(image_tag: str) -> bool`

**Requirements**:
- Return 12-character SHA256 hash
- Same input order as bash: config_file + dockerfile + base_image + entrypoint
- Read files as text (not binary) to match bash `cat` behavior
- Hash the string using SHA256, take first 12 characters

**CRITICAL**: The hash computation must match the bash implementation exactly:
```bash
# Bash version concatenates: config_file + dockerfile + base_image + entrypoint
hash_input+=$(cat "$PAUDE_CONFIG_FILE")       # file contents
hash_input+=$(cat "$PAUDE_DOCKERFILE")        # file contents
hash_input+="$PAUDE_BASE_IMAGE"               # string
hash_input+=$(cat "$entrypoint")              # file contents
echo "$hash_input" | sha256sum | cut -c1-12   # hash and truncate
```

**IMPORTANT**: The bash `echo "$hash_input"` adds a trailing newline before hashing!
The Python implementation must also add this newline to match:
```python
import hashlib
hash_input = config_content + dockerfile_content + base_image + entrypoint_content
# Add trailing newline to match bash echo behavior
hash_bytes = (hash_input + "\n").encode("utf-8")
hash_hex = hashlib.sha256(hash_bytes).hexdigest()[:12]
```

The Python equivalent must produce the same hash for the same inputs.

**Acceptance criteria**:
- [ ] Same inputs produce identical hash as bash
- [ ] Hash is exactly 12 characters
- [ ] Function handles missing optional files (None)
- [ ] Uses text mode file reading to match bash cat

---

#### TASK-4.2: Create Hash Tests

**Description**: Port hash tests from test/test_hash.sh.

**File**: `tests/test_hash.py`

**Test cases**:
1. compute_config_hash returns 12 chars
2. Same inputs produce same hash
3. Different inputs produce different hash
4. Handles missing config_file (None)
5. Handles missing dockerfile (None)
6. Hash includes entrypoint content
7. Verify hash matches expected value for known inputs

**Note**: Include a test with known input values and expected hash output. This can be
generated once using the bash implementation, then hardcoded in the Python test to
verify compatibility. Example:
```python
def test_hash_matches_bash():
    # Known inputs that produce a specific hash in bash
    # Generated by running: echo "test content" | sha256sum | cut -c1-12
    config_content = '{"base": "python:3.11"}'
    expected_hash = "..."  # Fill in from bash test
    # Verify Python produces same hash
```

**Acceptance criteria**:
- [ ] All 7 test cases pass
- [ ] Tests verify hash length is 12
- [ ] At least one test verifies hash matches a known bash-generated value
- [ ] `pytest tests/test_hash.py` passes

---

### Phase 5: Features Module

#### TASK-5.1: Create Feature Downloader

**Description**: Port feature downloading from lib/features.sh.

**File**: `src/paude/features/downloader.py`

**Functions**:
- `download_feature(feature_url: str) -> Path`
- `clear_feature_cache() -> None`

**Requirements**:
- Cache in `~/.cache/paude/features/`
- Support ORAS, skopeo, or curl fallback
- Hash-based cache directory naming

**Acceptance criteria**:
- [ ] Features can be downloaded (with appropriate tooling)
- [ ] Cache is reused on repeat downloads
- [ ] Cache clear removes cached features

---

#### TASK-5.2: Create Feature Installer

**Description**: Generate Dockerfile layers for features.

**File**: `src/paude/features/installer.py`

**Functions**:
- `generate_feature_install_layer(feature_path: Path, options: dict) -> str`
- `generate_features_dockerfile(features: list[FeatureSpec]) -> str`

**Requirements**:
- Convert options to environment variables (uppercase)
- Generate COPY and RUN commands
- Match bash output format

**Acceptance criteria**:
- [ ] Generated Dockerfile layers match bash format
- [ ] Options are converted to env vars correctly
- [ ] Cleanup step is included

---

#### TASK-5.3: Create Features Tests

**Description**: Create tests for feature handling.

**File**: `tests/test_features.py`

**Test cases**:
1. generate_feature_install_layer creates correct COPY command
2. generate_feature_install_layer creates correct RUN command
3. Options are converted to uppercase env vars
4. generate_features_dockerfile includes cleanup step
5. Cache directory path is correct

**Acceptance criteria**:
- [ ] All 5 test cases pass
- [ ] Tests don't require network access
- [ ] `pytest tests/test_features.py` passes

---

### Phase 6: Mount Builder

#### TASK-6.1: Implement Mount Builder

**Description**: Port setup_mounts() to Python.

**File**: `src/paude/mounts.py`

**Function**: `build_mounts(workspace: Path, home: Path) -> list[str]`

**Mounts** (in order):
1. Workspace at same path (rw)
2. gcloud config (ro, if exists)
3. Claude seed directory (ro, if exists)
4. Plugins at original host path (ro, if exists)
5. gitconfig (ro, if exists)
6. claude.json seed (ro, if exists)

**Requirements**:
- Return list of `-v source:dest:mode` strings
- Handle missing optional directories gracefully
- Resolve symlinks for workspace

**Acceptance criteria**:
- [ ] All mount types handled correctly
- [ ] Missing optional paths don't cause errors
- [ ] Symlinks are resolved for workspace

---

#### TASK-6.2: Create Mount Tests

**Description**: Port mount tests from tests/test_mounts.sh.

**File**: `tests/test_mounts.py`

**Test cases** (match tests/test_mounts.sh):
1. Workspace mount is always present with rw mode
2. gcloud mount is read-only when .config/gcloud exists
3. gcloud mount skipped when directory missing
4. Claude seed mount (.claude -> /tmp/claude.seed) is read-only when present
5. Plugins mounted at original host path (for hardcoded plugin references)
6. gitconfig mount is read-only when present
7. claude.json mount (/tmp/claude.json.seed) is read-only when present

**Acceptance criteria**:
- [ ] All 7 test cases pass
- [ ] Tests use mock home directories
- [ ] `pytest tests/test_mounts.py` passes

---

### Phase 7: Environment Builder

#### TASK-7.1: Implement Environment Builder

**Description**: Port setup_environment() to Python.

**File**: `src/paude/environment.py`

**Functions**:
- `build_environment() -> dict[str, str]`
- `build_proxy_environment(proxy_name: str) -> dict[str, str]`

**Environment variables**:
- CLAUDE_CODE_USE_VERTEX (passthrough)
- ANTHROPIC_VERTEX_PROJECT_ID (passthrough)
- GOOGLE_CLOUD_PROJECT (passthrough)
- CLOUDSDK_AUTH_* (passthrough all matching)
- HTTP_PROXY, HTTPS_PROXY, http_proxy, https_proxy (when using proxy)

**Acceptance criteria**:
- [ ] Vertex AI vars are passed through
- [ ] CLOUDSDK_AUTH vars are passed through
- [ ] Proxy env vars are set correctly

---

#### TASK-7.2: Create Environment Tests

**Description**: Create tests for environment building.

**File**: `tests/test_environment.py`

**Test cases**:
1. CLAUDE_CODE_USE_VERTEX passed through when set
2. ANTHROPIC_VERTEX_PROJECT_ID passed through when set
3. CLOUDSDK_AUTH variables are collected
4. Missing env vars are not included
5. Proxy environment includes all 4 proxy vars

**Acceptance criteria**:
- [ ] All 5 test cases pass
- [ ] Tests use monkeypatch for environment
- [ ] `pytest tests/test_environment.py` passes

---

### Phase 8: Platform Handling

#### TASK-8.1: Implement Platform Module

**Description**: Port macOS-specific logic to Python.

**File**: `src/paude/platform.py`

**Functions**:
- `is_macos() -> bool`
- `check_macos_volumes(workspace: Path, image: str) -> bool`
- `get_podman_machine_dns() -> str | None`
- `show_macos_volume_help(workspace: Path) -> None`

**Requirements**:
- Detect macOS via platform module
- Check if workspace is outside /Users on macOS
- Get DNS from podman machine for proxy
- Show helpful error message for volume issues

**Acceptance criteria**:
- [ ] is_macos() returns correct value
- [ ] Volume check logic matches bash
- [ ] Help message matches bash format

---

#### TASK-8.2: Create Platform Tests

**Description**: Create tests for platform handling.

**File**: `tests/test_platform.py`

**Test cases**:
1. is_macos returns boolean
2. check_macos_volumes skipped on Linux
3. show_macos_volume_help output includes podman commands
4. get_podman_machine_dns returns None when not on macOS

**Acceptance criteria**:
- [ ] All 4 test cases pass
- [ ] Tests mock platform detection as needed
- [ ] `pytest tests/test_platform.py` passes

---

### Phase 9: Utilities

#### TASK-9.1: Implement Utilities

**Description**: Port utility functions from main script.

**File**: `src/paude/utils.py`

**Functions**:
- `resolve_path(path: Path) -> Path` - Resolve symlinks to physical path
- `check_requirements() -> None` - Verify podman is installed
- `check_git_safety(workspace: Path) -> None` - Warn if no git repo or remotes

**Acceptance criteria**:
- [ ] resolve_path resolves symlinks
- [ ] check_requirements raises if podman missing
- [ ] check_git_safety warns appropriately

---

#### TASK-9.2: Create Utility Tests

**Description**: Port utility tests from tests/test_functions.sh.

**File**: `tests/test_utils.py`

**Test cases** (from tests/test_functions.sh - env/arg tests are in other modules):
1. resolve_path resolves symlinks to physical path
2. resolve_path returns empty/None for non-existent path
3. check_requirements raises when podman missing (mock shutil.which)
4. check_git_safety warns when no .git directory
5. check_git_safety warns when no remotes configured

**Note**: The bash test_functions.sh also tests environment setup and arg parsing,
but those are covered in test_environment.py and test_cli.py respectively.

**Acceptance criteria**:
- [ ] All 5 test cases pass
- [ ] Tests mock shutil.which for podman check
- [ ] `pytest tests/test_utils.py` passes

---

### Phase 10: Container Management

#### TASK-10.1: Create Podman Wrapper

**Description**: Create a wrapper for podman subprocess calls.

**File**: `src/paude/container/podman.py`

**Functions**:
- `run_podman(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess`
- `image_exists(tag: str) -> bool`
- `network_exists(name: str) -> bool`

**Requirements**:
- Capture stdout/stderr by default
- Raise on non-zero exit when check=True
- Return CompletedProcess for inspection

**Acceptance criteria**:
- [ ] run_podman calls podman with correct args
- [ ] image_exists returns bool
- [ ] network_exists returns bool

---

#### TASK-10.2: Create Image Manager

**Description**: Implement image building and pulling.

**File**: `src/paude/container/image.py`

**Class**: `ImageManager`

**Methods**:
- `ensure_default_image() -> str`
- `ensure_custom_image(config: PaudeConfig, force_rebuild: bool) -> str`
- `ensure_proxy_image() -> str`
- `build_image(dockerfile: Path, tag: str, context: Path, build_args: dict | None = None) -> None`
- `pull_image(image: str) -> None`

**Acceptance criteria**:
- [ ] Default image is pulled in installed mode
- [ ] Default image is built in dev mode
- [ ] Custom images use hash-based tags
- [ ] Force rebuild bypasses cache

---

#### TASK-10.3: Create Network Manager

**Description**: Implement network management.

**File**: `src/paude/container/network.py`

**Class**: `NetworkManager`

**Methods**:
- `create_internal_network(name: str) -> None`
- `remove_network(name: str) -> None`
- `network_exists(name: str) -> bool`

**Acceptance criteria**:
- [ ] Creates internal network
- [ ] Handles existing network gracefully
- [ ] Removes network on cleanup

---

#### TASK-10.4: Create Container Runner

**Description**: Implement container execution.

**File**: `src/paude/container/runner.py`

**Class**: `ContainerRunner`

**Methods**:
- `run_claude(image: str, mounts: list[str], env: dict[str, str], args: list[str], network: str | None = None) -> int`
- `run_proxy(image: str, network: str, dns: str | None = None) -> str` (returns container name)
- `run_post_create(image: str, mounts: list[str], env: dict[str, str], command: str, network: str | None = None) -> bool`
- `stop_container(name: str) -> None`

**Requirements**:
- Handle YOLO mode warning
- Handle combined YOLO + allow-network warning
- Execute podman run with correct arguments
- Return exit code from Claude

**Acceptance criteria**:
- [ ] Claude container runs with correct mounts
- [ ] Proxy container starts with correct network
- [ ] Warnings are printed for YOLO mode

---

#### TASK-10.5: Create Container Tests

**Description**: Create tests for container management (mocked).

**File**: `tests/test_container.py`

**Test cases**:
1. image_exists returns True for existing image
2. image_exists returns False for missing image
3. build_image calls podman build with correct args
4. pull_image calls podman pull
5. run_claude includes all mounts
6. run_claude includes all env vars
7. run_proxy creates container with correct network
8. YOLO mode adds --dangerously-skip-permissions

**Acceptance criteria**:
- [ ] All 8 test cases pass
- [ ] Tests mock subprocess.run
- [ ] `pytest tests/test_container.py` passes

---

### Phase 11: Dry-Run Mode

#### TASK-11.1: Implement Dry-Run Output

**Description**: Port show_dry_run() to Python.

**File**: `src/paude/dry_run.py`

**Function**: `show_dry_run(config: PaudeConfig, flags: dict, workspace: Path) -> None`

**Output sections**:
1. Workspace path
2. Configuration file and type (or "none")
3. Base image or Dockerfile
4. Additional packages (if any)
5. Setup command (if any)
6. Would build: image:hash (if custom)
7. Generated Dockerfile (if custom)
8. Flags summary

**Acceptance criteria**:
- [ ] Output matches bash show_dry_run format
- [ ] Shows Dockerfile for custom configs
- [ ] Shows all flags with values

---

#### TASK-11.2: Create Dry-Run Tests

**Description**: Create tests for dry-run output.

**File**: `tests/test_dry_run.py`

**Test cases**:
1. Shows workspace path
2. Shows "none" when no config
3. Shows config file when present
4. Shows packages when present
5. Shows generated Dockerfile for custom config
6. Shows all flags

**Acceptance criteria**:
- [ ] All 6 test cases pass
- [ ] Tests capture stdout
- [ ] `pytest tests/test_dry_run.py` passes

---

### Phase 12: Main Orchestration

#### TASK-12.1: Wire Everything Together

**Description**: Implement the main CLI flow connecting all modules.

**File**: `src/paude/cli.py` (update existing)

**Flow**:
1. Parse arguments
2. Detect and parse config
3. If dry-run, show config and exit
4. Check requirements (podman)
5. Ensure images (default or custom)
6. Build mounts and environment
7. Check macOS volumes (if applicable)
8. Check git safety
9. Setup proxy (if not allow-network)
10. Run Claude container
11. Cleanup on exit

**Requirements**:
- Use atexit for cleanup
- Handle SIGINT/SIGTERM for cleanup
- Print helpful error messages

**Acceptance criteria**:
- [ ] Full flow works with --dry-run
- [ ] Cleanup happens on interrupt
- [ ] Error messages are helpful

---

#### TASK-12.2: Create Integration Tests

**Description**: Create end-to-end tests with mocked podman.

**File**: `tests/test_integration.py`

**Test cases**:
1. Full flow with default config
2. Full flow with devcontainer.json
3. Full flow with paude.json
4. Dry-run mode shows correct output
5. Allow-network skips proxy setup
6. YOLO mode adds skip-permissions flag
7. Rebuild flag forces image rebuild

**Acceptance criteria**:
- [ ] All 7 test cases pass
- [ ] Tests mock all subprocess calls
- [ ] `pytest tests/test_integration.py` passes

---

### Phase 13: Documentation

#### TASK-13.1: Update README.md

**Description**: Update README with Python installation and usage.

**Changes**:
1. Add Python installation section:
   ```
   ## Installation

   ### Using pipx (recommended)
   pipx install paude

   ### Using pip
   pip install paude

   ### From source
   git clone https://github.com/bbrowning/paude
   cd paude
   pip install -e .
   ```

2. Update prerequisites section to include Python 3.11+

3. Add development setup section:
   ```
   ## Development

   pip install -e ".[dev]"
   make test     # Run tests
   make lint     # Check code style
   make format   # Format code
   make typecheck # Type checking
   ```

4. Keep container-related sections unchanged (they still apply)

5. Add migration notes from bash version (brief note about v0.4.0 being Python-based)

**Acceptance criteria**:
- [ ] Installation instructions include pipx, pip, and from-source methods
- [ ] Python version requirement (3.11+) is clearly stated
- [ ] Development workflow is documented with make commands

---

#### TASK-13.2: Update CLAUDE.md

**Description**: Update coding assistant instructions for Python codebase.

**Changes**:
1. Update Architecture section to describe Python package structure:
   ```
   src/paude/
   ├── cli.py          # Typer CLI
   ├── config/         # Configuration parsing
   ├── container/      # Podman interaction
   ├── features/       # Dev container features
   ├── mounts.py       # Volume mount builder
   ├── environment.py  # Environment variables
   ├── hash.py         # Config hashing
   ├── platform.py     # Platform-specific code
   ├── utils.py        # Utilities
   └── dry_run.py      # Dry-run output
   ```

2. Update Testing Changes section:
   - `make test` runs pytest
   - Tests are in `tests/` directory
   - No live podman required (all mocked)

3. Add linting/type checking info:
   - `make lint` for ruff
   - `make typecheck` for mypy
   - Code style: ruff format

4. Keep container-related sections (Dockerfiles, security model) unchanged

**Acceptance criteria**:
- [ ] CLAUDE.md reflects Python package structure
- [ ] Testing commands are updated (pytest, not bash)
- [ ] Module descriptions are accurate

---

#### TASK-13.3: Create CONTRIBUTING.md

**Description**: Create comprehensive contributor guide.

**Sections**:
1. Development setup
2. Running tests
3. Code style (ruff, mypy)
4. Submitting changes
5. Release process

**Acceptance criteria**:
- [ ] CONTRIBUTING.md exists
- [ ] All dev commands are documented
- [ ] Code style expectations are clear

---

### Phase 14: Makefile Updates

#### TASK-14.1: Update Makefile for Python

**Description**: Update Makefile to support both bash and Python workflows.

**New targets**:
```makefile
# Python targets
.PHONY: install lint format typecheck test-python

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests

format:
	ruff format src tests

typecheck:
	mypy src

test-python:
	pytest --cov=paude --cov-report=term-missing

# Update existing test target to run both
test: test-python
	@echo "Python tests passed"
```

**Acceptance criteria**:
- [ ] `make install` works
- [ ] `make lint` runs ruff
- [ ] `make format` formats code
- [ ] `make typecheck` runs mypy
- [ ] `make test` runs Python tests

---

### Phase 15: Final Verification

#### TASK-15.1: Run Full Test Suite

**Description**: Verify all tests pass.

**Commands**:
```bash
make install
make lint
make typecheck
make test
```

**Acceptance criteria**:
- [ ] All lint checks pass
- [ ] All type checks pass
- [ ] All tests pass
- [ ] Coverage is reported

---

#### TASK-15.2: Manual Verification

**Description**: Manually verify key functionality.

**Checks**:
1. `paude --help` shows expected output
2. `paude --version` shows version
3. `paude --dry-run` works without podman
4. All flags are recognized

**Acceptance criteria**:
- [ ] Help output is correct
- [ ] Version output is correct
- [ ] Dry-run works without errors

---

## Task Execution Order

For the Ralph Wiggum loop, execute tasks in this order:

### Bootstrap (must be done on host first)
1. PREREQ-1: Update paude.json

### Phase 1: Scaffolding (TASK-1.1 through TASK-1.5)
### Phase 2: CLI (TASK-2.1 through TASK-2.2)
### Phase 3: Config (TASK-3.1 through TASK-3.5)
### Phase 4: Hash (TASK-4.1 through TASK-4.2)
### Phase 5: Features (TASK-5.1 through TASK-5.3)
### Phase 6: Mounts (TASK-6.1 through TASK-6.2)
### Phase 7: Environment (TASK-7.1 through TASK-7.2)
### Phase 8: Platform (TASK-8.1 through TASK-8.2)
### Phase 9: Utilities (TASK-9.1 through TASK-9.2)
### Phase 10: Container (TASK-10.1 through TASK-10.5)
### Phase 11: Dry-Run (TASK-11.1 through TASK-11.2)
### Phase 12: Orchestration (TASK-12.1 through TASK-12.2)
### Phase 13: Documentation (TASK-13.1 through TASK-13.3)
### Phase 14: Makefile (TASK-14.1)
### Phase 15: Verification (TASK-15.1 through TASK-15.2)

## Completion Criteria

The Python port is complete when:

1. All tasks above are marked complete
2. `make test` passes (all Python tests)
3. `make lint` passes (ruff check)
4. `make typecheck` passes (mypy)
5. `paude --dry-run` works
6. README.md is updated
7. CLAUDE.md is updated
8. CONTRIBUTING.md exists

## Notes for Implementation Loop

- Each task should be completed fully before moving to the next
- Run tests after each task to catch regressions early
- Use `git diff` to review changes before moving on
- If stuck on a task, add notes to this file for the next iteration
- Mark completed tasks with [x] in the acceptance criteria

## Implementation Notes

Important details for the implementation loop:

### 1. Module Imports Structure
Ensure all modules have proper `__init__.py` files that export their public APIs:
- `src/paude/config/__init__.py` should export `detect_config`, `parse_config`, `PaudeConfig`, etc.
- `src/paude/container/__init__.py` should export `ImageManager`, `ContainerRunner`, etc.
- `src/paude/features/__init__.py` should export `download_feature`, `generate_features_dockerfile`, etc.

### 2. Type Annotations
Use modern Python 3.11+ type syntax throughout:
- Use `list[str]` not `List[str]`
- Use `dict[str, Any]` not `Dict[str, Any]`
- Use `Path | None` not `Optional[Path]`
- Use `Literal["default", "devcontainer", "paude"]` for config_type

### 3. Error Handling
- Use `typer.Exit(code=1)` for CLI errors, not `sys.exit()`
- Use `typer.echo()` for output, which respects --quiet flags
- Raise `PaudeError` (custom exception) for internal errors

### 4. Testing Best Practices
- Use `pytest.fixture` for reusable test setup
- Use `tmp_path` fixture (built-in) for temporary directories
- Use `monkeypatch` for environment variable manipulation
- Use `capsys` to capture stdout/stderr
- Mock `subprocess.run` to avoid running real podman commands

### 5. Subprocess Mocking Pattern
```python
import subprocess
from unittest.mock import MagicMock

def test_something(monkeypatch):
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(
        args=["podman", "..."],
        returncode=0,
        stdout="",
        stderr=""
    ))
    monkeypatch.setattr(subprocess, "run", mock_run)

    # Test code that calls subprocess.run

    # Verify the call
    mock_run.assert_called_once_with(["podman", "..."], ...)
```

### 6. CLI Testing Pattern
```python
from typer.testing import CliRunner
from paude.cli import app

runner = CliRunner()

def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout
```

### 7. Environment Variable Handling
Use a helper function to safely get environment variables:
```python
import os

def get_env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)
```

### 8. Path Handling
Always use `pathlib.Path` for path manipulation:
```python
from pathlib import Path

workspace = Path.cwd()
config_file = workspace / ".devcontainer" / "devcontainer.json"
if config_file.exists():
    content = config_file.read_text()
```

---

## Progress Tracking

As tasks are completed, update this section:

- [x] PREREQ-1: Update paude.json (MUST BE DONE ON HOST)
- [x] TASK-1.1: Create Python Project Structure
- [x] TASK-1.2: Create pyproject.toml
- [x] TASK-1.3: Update .gitignore for Python
- [x] TASK-1.4: Create Initial Test
- [x] TASK-1.5: Verify Tooling
- [x] TASK-2.1: Implement CLI with Typer
- [x] TASK-2.2: Create CLI Tests
- [x] TASK-3.1: Create Config Data Models
- [x] TASK-3.2: Create Config Detector
- [x] TASK-3.3: Create Config Parser
- [x] TASK-3.4: Create Dockerfile Generator
- [x] TASK-3.5: Create Config Tests
- [x] TASK-4.1: Implement Hash Computation
- [x] TASK-4.2: Create Hash Tests
- [x] TASK-5.1: Create Feature Downloader
- [x] TASK-5.2: Create Feature Installer
- [x] TASK-5.3: Create Features Tests
- [x] TASK-6.1: Implement Mount Builder
- [x] TASK-6.2: Create Mount Tests
- [x] TASK-7.1: Implement Environment Builder
- [x] TASK-7.2: Create Environment Tests
- [x] TASK-8.1: Implement Platform Module
- [x] TASK-8.2: Create Platform Tests
- [x] TASK-9.1: Implement Utilities
- [x] TASK-9.2: Create Utility Tests
- [x] TASK-10.1: Create Podman Wrapper
- [x] TASK-10.2: Create Image Manager
- [x] TASK-10.3: Create Network Manager
- [x] TASK-10.4: Create Container Runner
- [x] TASK-10.5: Create Container Tests
- [x] TASK-11.1: Implement Dry-Run Output
- [x] TASK-11.2: Create Dry-Run Tests
- [x] TASK-12.1: Wire Everything Together
- [x] TASK-12.2: Create Integration Tests
- [x] TASK-13.1: Update README.md
- [x] TASK-13.2: Update CLAUDE.md
- [x] TASK-13.3: Update CONTRIBUTING.md
- [x] TASK-14.1: Update Makefile for Python
- [x] TASK-15.1: Run Full Test Suite
- [x] TASK-15.2: Manual Verification
