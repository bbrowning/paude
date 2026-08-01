# Known Issues

Tracking known issues that need to be fixed. Each bug includes enough context for someone without prior knowledge to identify, reproduce, and solve the issue.

## Refactoring Backlog

Technical debt identified during codebase analysis. Address these before adding significant new functionality to affected files.

### REFACTOR-003: Oversized files, methods, and classes

**Status**: Partially resolved
**Priority**: Medium (address before adding significant new functionality to affected files)
**Discovered**: 2026-03-24 during v0.13.0 pre-release audit

**Resolved (2026-07-23):**
- `backends/podman/backend.py` — decomposed from 729 lines / 38 methods to 402 lines / 20 public methods (+ 3 small private helpers). Extracted `SessionSetup` (session_setup.py), `CACertDistributor` (ca_cert.py), `ProxyCredentialManager` (proxy_credentials.py). Validation helpers moved to helpers.py.
- `backends/podman/proxy.py` — reduced from 641 lines to 400 lines. Deleted dead code, extracted collaborator classes.
- `backends/podman/backend.py` — `create_session()` reduced from 143 lines to ~37 lines.

**Still open — files exceeding 400-line limit:**
- `cli/commands.py` — 580 lines
- `workflow.py` — 467 lines
- `container/runner.py` — 442 lines

**Still open — methods exceeding 50-line limit:**
- `workflow.py` — `harvest_session()` (~102 lines), `status_sessions()` (~84), `reset_session()` (~72)
- `cli/commands.py` — `session_cp()` (~75 lines)

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

### REFACTOR-005: SSH transport construction (`ssh_host` string → `SshTransport`) duplicated in three places

**Status**: Open
**Priority**: Low (small duplication, but drift-prone since all three must stay in sync)
**Discovered**: 2026-07-30 while fixing SEC-003 (`harvest` SSH remote resolution)

The same "parse a `user@host[:port]` string with `parse_ssh_host()`, then construct a matching `SshTransport`" logic is independently implemented in three places:

1. `_build_transport()` in `src/paude/cli/remote_git_setup.py:57-66`
2. Inline in `build_ssh_backend()` in `src/paude/backends/ssh.py:31-36`
3. Inline in `resolve_session_remote()` in `src/paude/git_remote/utils.py:60-63`

The third occurrence was added while fixing SEC-003 rather than reusing `_build_transport()`, because `_build_transport()` lives in `src/paude/cli/` and importing it from `src/paude/git_remote/` (a lower-level package) would invert the existing dependency direction. Consolidating all three requires first giving this logic a home that both `cli/` and `git_remote/`/`backends/` can depend on without a layering inversion (e.g. a small helper in `src/paude/transport/ssh.py` itself, alongside `parse_ssh_host()`). Until then, any change to `SshTransport`'s constructor or `parse_ssh_host()`'s contract needs to be checked against all three call sites.

### REFACTOR-006: `get_agents()` multi-agent composition has no production caller

**Status**: Open
**Priority**: Low (scaffolding, not a bug — nothing currently calls it)
**Discovered**: 2026-08-01 while rebasing/cleaning up the `bundled_agents` composite-agent PR

`get_agents(names, providers)` and `AgentComposition` in `src/paude/agents/__init__.py` expand a list of explicitly requested agent names (plus each one's `bundled_agents`) into a deduplicated, ordered install set with a primary agent — but nothing in production calls it yet. The already-wired composition path (`get_agent_composition()` / `dockerfile_install_lines_for_agent()`) only expands a *single* primary agent's own bundled agents (e.g. gascity → claude+gemini); it doesn't handle a user explicitly requesting multiple independent agents.

The intended end state (per product decision 2026-08-01): `--agents gascity,codex` should install every requested + bundled agent into the image, while only the primary (first) agent drives session launch, network access, and credentials. `src/paude/config/resolver.py`'s `ResolvedCreateOptions` already resolves `agents`/`agent_providers` lists from `--agents`/`--providers` (added in #223/#225), but `src/paude/cli/create.py` explicitly warns and drops everything but the primary on a real (non-dry-run) create.

Wiring this up for real is bigger than swapping in a `get_agents()` call in `cli/create.py`. Every one of these currently re-resolves a *single* agent name/provider and would need to become multi-agent-aware:

- `src/paude/container/image.py` (`ImageManager`) — Dockerfile generation and image cache-key hashing. `ensure_custom_image()`'s cache key currently derives `agent_name` from the primary agent only; installing extra agents without extending that key would serve a stale cached image missing the extra agents.
- `src/paude/config/claude_layer.py`, `src/paude/config/dockerfile.py`, `src/paude/container/build_context.py` — Dockerfile generators take a single `Agent`, not an install list.
- `src/paude/backends/base.py` (`SessionConfig.agent`), `src/paude/backends/session_env.py`, `src/paude/backends/proxy_config.py`, `src/paude/backends/podman/session_setup.py`, `src/paude/backends/podman/backend.py` — all re-resolve `get_agent(config.agent, provider=config.provider)` from one stored name/provider to derive secret env vars, exposed ports, sandbox config, and proxy credential injection.
- `src/paude/mounts.py` (`build_mounts`) — only mounts host config for one agent.
- `src/paude/cli/helpers.py` (`_prepare_session_create`) — builds env vars and allowed-domains from one agent's `extra_domain_aliases`/`secret_env_vars`.

Until this is done, `get_agents()` remains dead code from the product's perspective (fully unit-tested, but unreachable from any CLI path).

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

## Runtime Hardening Backlog

### RUNTIME-001: persist_config_dir failures are invisible to the user

**Status**: Open
**Priority**: Low
**Discovered**: 2026-07-30 while diagnosing a Codex sqlite "database is damaged" bug (root cause fixed for the current call sites: `ContainerRunner.inject_file()` now also chowns the immediate parent directory it creates, not just the leaf file — but only one level deep, so a future caller whose `mkdir -p` creates more than one new directory level would reproduce this bug)

`persist_config_dir()` in `containers/paude/entrypoint-lib-config.sh` swallows every failure from `mkdir`, `chmod`, `chcon`, and `rm -rf` via `2>/dev/null || true`. Its only diagnostic — `"persist_config_dir: cannot replace $home_dir with symlink; using PVC copy at $pvc_dir"` — is printed to the exec session's stderr, but `entrypoint-session.sh` runs `clear` immediately before launching the agent, wiping that line from the terminal before the user ever sees it. When the self-heal fails (e.g. a pre-existing directory under `$HOME` that `paude` can't write into), the user only sees an opaque downstream error from the agent itself (in the Codex case, a cryptic sqlite `CANTOPEN` error), with zero indication that paude's own volume-persistence step failed first.

Consider making this diagnostic durable — e.g. write it to a log file under `$HOME` that survives `clear`, or print it after `clear` runs, or fail loudly instead of silently falling back — so any future recurrence of this class of bug is diagnosable from the user's own terminal instead of requiring a live `podman exec` investigation.

## Test Suite

### TEST-001: `test_proxy_shuts_down_on_sigterm` is flaky under full-suite load

**Status**: Open
**Priority**: Low
**Discovered**: 2026-08-01 during a review-fix pass on the `--agents/--providers` branch

`tests/test_port_forward_proxy.py::TestPortForwardProxy::test_proxy_shuts_down_on_sigterm`
intermittently fails when run as part of the full `make test` suite but passes
reliably in isolation (`uv run pytest tests/test_port_forward_proxy.py::TestPortForwardProxy::test_proxy_shuts_down_on_sigterm`).
The failure is timing/resource-contention related (SIGTERM delivery and socket
teardown racing under concurrent full-suite load), not a product defect. Consider
adding an explicit wait/retry on the shutdown assertion or isolating the test's
port/socket allocation so it is deterministic under load.

## Documentation Gaps

Found during a 2026-07-22 audit to add OpenCode support to README/docs/CLI help.

### DOCS-001: OpenCode `chatgpt` provider produces an empty opencode.json provider block

**Status**: Open (needs verification)
**Severity**: Medium

`AGENT_PROVIDERS["opencode"]["chatgpt"]` in `src/paude/providers/agent_providers.py` accepts `--agent opencode --provider chatgpt` as valid, but `_PROVIDER_CONFIGS` in `src/paude/agents/opencode.py` has no `"chatgpt"` entry. `_provider_config_json()` returns an empty string for unknown providers, so the generated `opencode.json` gets an empty `"provider": {}` block. It's unverified whether OpenCode's own auth (e.g. `opencode auth login`) works fine without any provider config, or whether this silently breaks ChatGPT-plan auth for OpenCode. This was intentionally left out of the README's "ChatGPT plan login" documentation until verified — documenting an unverified flow as working would be misleading.

### DOCS-002: Gas City and OpenClaw missing from agent-specific-defaults docs

**Status**: Resolved (2026-07-30 during docs audit — added Gas City and OpenClaw bullets to `docs/CONFIGURATION.md`'s "Agent-specific defaults" list)
**Severity**: Low

`docs/CONFIGURATION.md`'s "Agent-specific defaults" list (network domain aliases) and README's "Your First Session" example list both enumerate Claude Code, Codex CLI, Cursor CLI, Gemini CLI (and now OpenCode), but omit Gas City and OpenClaw entirely. Pre-existing gap, not introduced by the OpenCode work — left alone to keep that change scoped.

Note: README's agent table/examples were not re-audited as part of the 2026-07-30 pass (scope was `docs/` only) and may still need the same treatment.

### DOCS-003: `cli/__init__.py` docstring uses agent-specific language

**Status**: Open
**Severity**: Low

`src/paude/cli/__init__.py:46` has `"""Run Claude Code in an isolated container."""` as the top-level Typer app docstring, which violates the AGENTS.md rule to use agent-agnostic language ("the agent", not "Claude") in user-facing text. Likely shows up in `paude --help` output.

### DOCS-004: Agent names are hand-maintained in three separate places

**Status**: Open
**Severity**: Low

`src/paude/agents/__init__.py`'s `_REGISTRY` dict is the single source of truth for supported agents, and `list_agents()` returns them dynamically — but `src/paude/cli/help.py`'s "Agents & Providers" panel and `src/paude/cli/create.py`'s `--agent` option help string both hardcode the same agent list as free text. This diff had to hand-patch `create.py`'s copy because it had already drifted (missing `codex` and `opencode`). Consider generating both help strings from `list_agents()` so they can't drift again.

### DOCS-005: `create` docstrings claim "does not start it" but the command auto-starts the container

**Status**: Open
**Severity**: Low
**Discovered**: 2026-07-31 during docs/ audit (`docs/SESSIONS.md`, `docs/ORCHESTRATION.md`)

`src/paude/cli/create.py:160` (`"""Create a new persistent session (does not start it)."""`) and the backend `create_session` docstrings (`src/paude/backends/base.py:106`, `src/paude/backends/podman/backend.py:83`, both "Create a new session (does not start it).") contradict actual runtime behavior: the CLI `create` path calls `start_session_no_attach` (`src/paude/cli/create_podman.py:122-123`), which starts the containers and agent, and `_finalize_session_create` prints "created and running" for local backends. The user-facing docs (`docs/SESSIONS.md` line 19, `docs/ORCHESTRATION.md`) correctly say `create` starts the container; only these code docstrings are stale. Update the three docstrings to reflect that local `create` also starts the session.

### DOCS-006: Gas City agent adds a bogus `"gascity"` domain alias with no matching alias definition

**Status**: Open (needs verification)
**Severity**: Low
**Discovered**: 2026-07-31 during docs/ audit (`docs/CONFIGURATION.md`)

`src/paude/agents/gascity.py:55-60` lists `"gascity"` in `extra_domain_aliases`, but there is no `"gascity"` key in `DOMAIN_ALIASES` (`src/paude/domains.py`). `expand_domains` (`src/paude/domains.py:167-171`) therefore treats it as a literal domain string, so the session allowlist ends up containing a meaningless entry `gascity` rather than an expanded set of real domains. Verify whether Gas City needs a real domain-alias definition (add a `"gascity"` entry to `DOMAIN_ALIASES`) or whether the literal was unintended and should be removed from `extra_domain_aliases`. `docs/CONFIGURATION.md` correctly omits it, so no doc change is warranted until the code intent is confirmed.

