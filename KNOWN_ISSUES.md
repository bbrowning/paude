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
- `container/image.py` — 531 lines

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

### REFACTOR-006: Credential-provider reconciliation duplicated between upgrade and resolver

**Status**: Open
**Priority**: Low (drift-prone; identical user-facing error string in two places)
**Discovered**: 2026-08-08 during a cleanup review of the `paude upgrade --add-agent` work

`_apply_overrides()` in `src/paude/cli/upgrade.py` and `_resolve_agents_and_providers()` in `src/paude/config/resolver.py` independently implement the same "reconcile credential providers against the agent→provider mapping" logic:

1. Deriving the mapped-provider set from a composition (`mapped_providers = list(dict.fromkeys(a.config.provider for a in composition.agents if a.config.provider))` in `upgrade.py` vs `_dedupe(provider for _, provider in result.agent_providers)` at `resolver.py:315`). The same idiom also appears as the fallback in `get_session_credential_providers()` at `src/paude/backends/podman/helpers.py:306-312`.
2. Validating an explicit `--providers` set covers every mapped provider, including the **verbatim** error string `"Credential providers must include every mapped provider; missing: "` (`upgrade.py` ↔ `resolver.py:324`).

This predates the `--add-agent` work (the `--providers` validation branch was already in `upgrade.py`); the cleanup pass left it in place because a proper fix reaches outside the changed code and is entangled with the resolver's `SettingValue`/provenance tracking. Consider extracting a shared `reconcile_credential_providers(agent_providers, explicit_providers) -> list[str]` (plus a `providers_from_composition(composition)` helper) next to `_derive_agent_providers` in `resolver.py`, and calling it from both `create` and `upgrade` so the invariant and error wording can't drift.

## Correctness Backlog

Lower-severity correctness/robustness issues surfaced during code review.

### HARVEST-001: `harvest` diff summary hardcodes `main` as the base ref

**Status**: Open
**Severity**: Low (cosmetic — harvest still succeeds)
**Discovered**: 2026-08-07 during code review of the `--container-path`/`--repo` harvest work

`harvest_session()` in `src/paude/workflow.py` prints a post-harvest change summary via `git_diff_stat("main", branch_name, cwd=workspace)` (workflow.py:~230). The base ref is hardcoded to `main`. With the new `--repo` option a user can harvest into a host repo whose default branch is `master`/`trunk`/etc.; there `git diff --stat main...<branch>` fails and `git_diff_stat()` returns `""` (`src/paude/git_remote/utils.py` — non-zero return yields empty string), so the summary line is silently omitted. Harvest itself still completes correctly. Fix by resolving the base branch the way `get_upstream_url()` does (iterate `DEFAULT_BRANCHES`, fall back to the tracking branch) instead of assuming `main`.

### HARVEST-002: container path with whitespace breaks the `ext::` remote URL

**Status**: Open
**Severity**: Low (exotic input; no injection risk)
**Discovered**: 2026-08-07 during code review of the `--container-path`/`--repo` harvest work

`build_podman_remote_url()` / `build_ssh_remote_url()` in `src/paude/git_remote/utils.py` interpolate `workspace_path` into the remote URL as `ext::… %S <workspace_path>`. Git's `ext::` helper splits its command line on whitespace and `exec`s the program directly (no shell is involved, so shell metacharacters are *not* an injection vector), but a `workspace_path` containing spaces would be word-split into separate argv entries and every fetch/push would fail. Now that `--container-path` accepts arbitrary paths (previously always the space-free `/pvc/workspace`), this is reachable, though container paths with spaces are highly unusual. Note that shell-quoting does not help here — there is no shell — so a fix would need `ext::`'s own (limited) escaping or a validation/rejection of whitespace in container paths.

### UPGRADE-001: pure provider remap prunes deliberately-provisioned credential-only providers

**Status**: Open
**Severity**: Low
**Discovered**: 2026-08-08 during code review of the `paude upgrade --add-agent` work

`_apply_overrides()` in `src/paude/cli/upgrade.py` treats a *pure* remap
(`--provider NEW` / `--agent-provider AGENT=NEW` with no `--add-agent`/`--agents`)
by replacing the credential set with only the providers still referenced by an
agent (the `elif remap` branch). This correctly drops the swapped agent's *old*
provider (the intended "swap in place" behavior asserted by
`test_swap_codex_provider_in_place`), but it also drops any **other**
credential-only provider that was deliberately provisioned and is not mapped to a
current agent (e.g. `--provider vertex` on a session created with
`--providers vertex,openai` where `openai` was reserved for later use). The
combined add+remap path was fixed to union instead (so it preserves such
providers), but the pure-remap path still over-prunes because it cannot
distinguish "displaced by this swap" from "provisioned on purpose." A fix would
track which provider each remap displaces and prune only that, rather than
pruning every unreferenced provider.

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

### RUNTIME-002: Remote config transfer failures silently degrade to empty mounts

**Status**: Open
**Priority**: Low
**Discovered**: 2026-08-06 while fixing the missing git-identity bug for `--host` sessions

In `src/paude/transport/config_sync.py`, `sync_configs_to_remote()` skips any
source whose `_transfer_path()` returns `False` (missing local file or a failed
`ssh cat`/`tar` pipe) — it simply never adds that source to `path_map`
(config_sync.py:141-142). `remap_mounts()` then only rewrites entries present in
`path_map` (config_sync.py:166), so a source that failed to transfer keeps its
**local** path in the `-v` mount spec. On the remote host that path usually does
not exist, and podman/Docker auto-creates an empty directory there when binding
the mount, with no error. The result is a silent degradation: e.g. a gitconfig
that failed to transfer becomes an empty bind mount and the user sees a missing
identity/config downstream rather than a transfer error. The git-identity fix
(resolve on host, inject `PAUDE_GIT_USER_NAME`/`EMAIL` env, fill in the
entrypoint) makes identity robust to this specific case, but the general
silent-degradation path remains. Consider surfacing a warning when a mount
source that existed locally fails to transfer, or when a remapped source is
missing on the remote.

## Test Suite

### TEST-002: Single-file branch of `_transfer_path` is untested

**Status**: Open
**Priority**: Low
**Discovered**: 2026-08-06 while fixing the missing git-identity bug for `--host` sessions

`tests/test_config_sync.py`'s `TestTransferPath` only exercises the directory
branch of `_transfer_path()` (`src/paude/transport/config_sync.py:72-96`, the
`tar | ssh tar` pipe). The single-file branch (config_sync.py:62-71, which reads
the file and pipes it through `ssh -- cat > remote_path`) has no coverage —
notably the path most relevant to gitconfig syncing, since `~/.gitconfig` is a
single file. Add a test that transfers a single file and asserts the correct
`ssh … cat > remote_path` invocation and return value on success/failure.

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

### TEST-003: No integration coverage for in-place agent-set / provider upgrades

**Status**: Open (investigation task)
**Priority**: Medium
**Discovered**: 2026-08-08 while adding `--add-agent`/`--agents` to `paude upgrade`

The new `paude upgrade` agent-set and provider-swap paths are covered by unit
tests in `tests/test_upgrade.py` (`TestUpgradePodmanAddAgent`), but those mock
the image build, container recreation, and start — so the *end-to-end* behavior
is unverified: that a rebuilt image actually installs the added agent's binaries
(e.g. `codex` + `codex-code-mode-host`), that `configure_codex` rewrites
`config.toml` / clears `auth.json` on a chatgpt→openai swap at start, that the
`/pvc` volume and existing agent state survive, and that labels/registry reflect
the new set on a live container. These require a real container engine and only
run in CI (`tests/integration/`, gated by the `integration`/`podman` markers;
see `make test-integration` / `make test-podman`). Investigate what the existing
podman integration suite covers for `upgrade` and add scenarios for: adding an
agent in place, swapping a provider in place, and verifying persisted state is
retained across the rebuild. (These cannot be run in the sandboxed dev
environment — no container engine — so they must be authored against CI.)

## Feature Backlog

### FEATURE-001: `paude upgrade` cannot remove agents (only add)

**Status**: Open (deferred follow-up)
**Priority**: Low
**Discovered**: 2026-08-08 while adding `--add-agent`/`--agents` to `paude upgrade`

`paude upgrade` can now add agents to an existing session in place
(`--add-agent AGENT`, or `--agents A,B` to redefine/reorder the set), but it
cannot *remove* an agent. `_apply_overrides()` in `src/paude/cli/upgrade.py`
rejects any `--agents` set that drops a currently-installed agent. Removal was
deferred because it needs decisions/guards that additive changes don't:

- Forbid removing the current primary agent (or require reassigning it), and
  forbid emptying the agent set entirely.
- Decide what happens to the removed agent's persisted state on `/pvc` (e.g.
  `.codex`, `.agents`). `persistent_state_paths()` in
  `src/paude/cli/upgrade_persistence.py` only enumerates agents still in the
  composition, so a removed agent's directory is left orphaned (harmless but
  confusing) rather than cleaned up.

Implement a `--remove-agent AGENT` option (additive delta, symmetric with
`--add-agent`) that enforces the guards above and decides the orphaned-state
policy.

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

**Status**: Resolved (2026-08-01)
**Severity**: Low
**Discovered**: 2026-07-31 during docs/ audit (`docs/CONFIGURATION.md`)

Gas City no longer contributes a standalone alias. Its resolved child-agent composition contributes the Claude/Gemini domains when those children are installed.

