# Known Issues

Tracking known issues that need to be fixed. Each bug includes enough context for someone without prior knowledge to identify, reproduce, and solve the issue.

## Refactoring Backlog

Technical debt identified during codebase analysis. Address these before adding significant new functionality to affected files.

### REFACTOR-003: Oversized files, methods, and classes

**Status**: Open
**Priority**: Medium (address before adding significant new functionality to affected files)
**Discovered**: 2026-03-24 during v0.13.0 pre-release audit

**Files exceeding 400-line limit:**
- `cli/commands.py` — 580 lines
- `backends/podman/backend.py` — 504 lines
- `workflow.py` — 467 lines

**Methods exceeding 50-line limit:**
- `workflow.py` — `harvest_session()` (~102 lines), `status_sessions()` (~84), `reset_session()` (~72)
- `cli/commands.py` — `session_cp()` (~75 lines)
- `backends/podman/backend.py` — `create_session()` (~95 lines)

**Classes exceeding 20-method limit:**
- `PodmanBackend` in `backends/podman/backend.py` — 26 methods

### REFACTOR-004: Duplicated Dockerfile between static file and generated code

**Status**: Open
**Priority**: Medium (every new container script requires updates in multiple places)
**Discovered**: 2026-03-28 while adding entrypoint-lib-openclaw.sh

The static `containers/paude/Dockerfile` and the programmatic Dockerfile generator in `src/paude/config/dockerfile.py` must be kept in sync manually. Adding a new script to the container requires changes in four places:

1. `containers/paude/Dockerfile` — static COPY line (used by local Podman/Docker builds)
2. `src/paude/config/dockerfile.py` — generated COPY line
3. `src/paude/container/build_context.py` — `copy_entrypoints()` file list
4. `pyproject.toml` — `force-include` for production wheel packaging

This caused a bug where the new OpenClaw helper script was added to the static Dockerfile but not to generated images. Consider having a single source of truth for the list of container scripts (e.g., a constant in `build_context.py`) that all four locations reference, or generating the static Dockerfile from the same code path.

## Agent Limitations

Issues caused by upstream agent behavior, not paude bugs.

### AGENT-001: Gemini CLI token expiry in long-running sessions

**Status**: Open (upstream limitation)
**Severity**: Low
**Discovered**: 2026-03-11 during Gemini CLI idle session testing

When a Gemini CLI session sits idle inside a paude container for ~1 hour, the OAuth access token expires. The already-running Gemini process does not gracefully refresh the token and instead prompts for browser-based re-authentication, which is not possible inside a container.

The container has everything needed to refresh tokens (oauth_creds.json with a valid refresh_token, network access to oauth2.googleapis.com). Starting a fresh `gemini` process inside the container works fine and refreshes the token automatically. The issue is specific to the long-running process not handling token expiry during idle periods.

**Workaround**: Kill the existing Gemini process and restart it inside the tmux session. The new process will pick up the refresh token and authenticate successfully.

### AGENT-002: Gemini CLI proxy support broken in 0.36.0+

**Status**: Open (upstream bug, pinned to 0.35.3)
**Severity**: Medium
**Discovered**: 2026-05-12
**Upstream**: https://github.com/google-gemini/gemini-cli/issues/24471

Gemini CLI 0.36.0 introduced a regression where `HttpsProxyAgent is not a constructor` is thrown when the CLI detects proxy environment variables (`HTTPS_PROXY` / `HTTP_PROXY`). This breaks all paude sessions that use the proxy container.

Paude pins to `@google/gemini-cli@0.35.3` (last working version) in `src/paude/agents/gemini.py`. Once the upstream issue is fixed, unpin and test with the proxy container.

## Security Hardening Backlog

Deferred items from the network egress security audit (2026-03-06).

### SEC-001: GitHub API allows POST/PUT through proxy

**Status**: Open (by design)
**Severity**: Low
**Discovered**: 2026-03-06 during network egress security audit

GitHub's GraphQL API uses POST for ALL operations, including reads (`gh pr list`, `gh issue list`). Blocking POST/PUT at the proxy level would break read-only `gh` CLI usage. The correct mitigation is using a read-only Personal Access Token (PAT) rather than proxy-level HTTP method filtering.

## Documentation Gaps

Found during a 2026-07-22 audit to add OpenCode support to README/docs/CLI help.

### DOCS-001: OpenCode `chatgpt` provider produces an empty opencode.json provider block

**Status**: Open (needs verification)
**Severity**: Medium

`AGENT_PROVIDERS["opencode"]["chatgpt"]` in `src/paude/providers/agent_providers.py` accepts `--agent opencode --provider chatgpt` as valid, but `_PROVIDER_CONFIGS` in `src/paude/agents/opencode.py` has no `"chatgpt"` entry. `_provider_config_json()` returns an empty string for unknown providers, so the generated `opencode.json` gets an empty `"provider": {}` block. It's unverified whether OpenCode's own auth (e.g. `opencode auth login`) works fine without any provider config, or whether this silently breaks ChatGPT-plan auth for OpenCode. This was intentionally left out of the README's "ChatGPT plan login" documentation until verified — documenting an unverified flow as working would be misleading.

### DOCS-002: Gas City and OpenClaw missing from agent-specific-defaults docs

**Status**: Open
**Severity**: Low

`docs/CONFIGURATION.md`'s "Agent-specific defaults" list (network domain aliases) and README's "Your First Session" example list both enumerate Claude Code, Codex CLI, Cursor CLI, Gemini CLI (and now OpenCode), but omit Gas City and OpenClaw entirely. Pre-existing gap, not introduced by the OpenCode work — left alone to keep that change scoped.

### DOCS-003: `cli/__init__.py` docstring uses agent-specific language

**Status**: Open
**Severity**: Low

`src/paude/cli/__init__.py:46` has `"""Run Claude Code in an isolated container."""` as the top-level Typer app docstring, which violates the AGENTS.md rule to use agent-agnostic language ("the agent", not "Claude") in user-facing text. Likely shows up in `paude --help` output.

### DOCS-004: Agent names are hand-maintained in three separate places

**Status**: Open
**Severity**: Low

`src/paude/agents/__init__.py`'s `_REGISTRY` dict is the single source of truth for supported agents, and `list_agents()` returns them dynamically — but `src/paude/cli/help.py`'s "Agents & Providers" panel and `src/paude/cli/create.py`'s `--agent` option help string both hardcode the same agent list as free text. This diff had to hand-patch `create.py`'s copy because it had already drifted (missing `codex` and `opencode`). Consider generating both help strings from `list_agents()` so they can't drift again.

