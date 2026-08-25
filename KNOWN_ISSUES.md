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

**Still open — files exceeding 400-line limit** (counts refreshed 2026-08-25
after the session-rebuild consolidation):
- `cli/upgrade.py` — 715 lines (was 802)
- `cli/backup.py` — 682 lines (was 703; still needs splitting into a package)
- `workflow.py` — 534 lines
- `container/image.py` — 531 lines
- `cli/helpers.py` — 515 lines
- `container/runner.py` — 460 lines
- `backends/podman/backend.py` — 448 lines (was 479; two teardowns moved to
  `backends/podman/resources.py`)

`cli/commands.py` is no longer listed: it is now a package (`cli/commands/`)
whose largest module is 168 lines.

**Still open — methods exceeding 50-line limit:**
- `workflow.py` — `harvest_session()` (~102 lines), `status_sessions()` (~84), `reset_session()` (~72)
- `cli/commands/cp.py` — `session_cp()` (~75 lines)
- `cli/create_podman.py` — `create_podman_session()` (65 lines, down from 157).
  Nearly all of what remains is keyword arguments being forwarded from a
  22-parameter signature to three collaborators; an attempt to extract a
  fourth helper needed 10 forwarded parameters to remove 4 lines and was
  reverted as parameter-plumbing rather than decomposition. The real fix is
  fewer parameters, not another helper.

### REFACTOR-004: Duplicated Dockerfile between static file and generated code

**Status**: Open
**Priority**: High (has now caused two real build failures; every new container script requires updates in four places)
**Discovered**: 2026-03-28 while adding entrypoint-lib-openclaw.sh
**Recurred**: 2026-08-25 with patch-gemini-otel-proxy.sh (see AGENT-003)

The static `containers/paude/Dockerfile` and the programmatic Dockerfile generator in `src/paude/config/dockerfile.py` must be kept in sync manually. Adding a new script to the container requires changes in four places:

1. `containers/paude/Dockerfile` — static COPY line (used by local Podman/Docker builds)
2. `src/paude/config/dockerfile.py` — generated COPY line
3. `src/paude/container/build_context.py` — `copy_entrypoints()` file list
4. `pyproject.toml` — `force-include` for production wheel packaging

This caused a bug where the new OpenClaw helper script was added to the static Dockerfile but not to generated images. **It has since recurred**: `patch-gemini-otel-proxy.sh` is present in locations 1, 3 and 4 but missing from location 2, so `generate_workspace_dockerfile` emits a `RUN` referencing a script it never COPYs and the build fails (see AGENT-003 for the reproduction and trigger conditions).

Two recurrences of the same defect mean the manual-sync approach has been falsified. Fix by giving the list of container scripts a single source of truth (e.g. a constant in `build_context.py`) that all four locations derive from, or by generating the static Dockerfile from the same code path.

Whatever the fix, add a guard test — none exists today. The cheapest one that would have caught both bugs: assert that every `/usr/local/bin/*.sh` referenced by a `RUN` line in each generator's output also has a matching `COPY` in that same output. `tests/test_build_context.py:141,156,177` only checks that scripts reach the *build context*, which both bugs passed.

### REFACTOR-005: SSH transport construction (`ssh_host` string → `SshTransport`) duplicated in three places

**Status**: Open
**Priority**: Low (small duplication, but drift-prone since all three must stay in sync)
**Discovered**: 2026-07-30 while fixing SEC-003 (`harvest` SSH remote resolution)

The same "parse a `user@host[:port]` string with `parse_ssh_host()`, then construct a matching `SshTransport`" logic is independently implemented in three places:

1. `_build_transport()` in `src/paude/cli/remote_git_setup.py:57-66`
2. Inline in `build_ssh_backend()` in `src/paude/backends/ssh.py:31-36`
3. Inline in `resolve_session_remote()` in `src/paude/git_remote/utils.py:60-63`

The third occurrence was added while fixing SEC-003 rather than reusing `_build_transport()`, because `_build_transport()` lives in `src/paude/cli/` and importing it from `src/paude/git_remote/` (a lower-level package) would invert the existing dependency direction. Consolidating all three requires first giving this logic a home that both `cli/` and `git_remote/`/`backends/` can depend on without a layering inversion (e.g. a small helper in `src/paude/transport/ssh.py` itself, alongside `parse_ssh_host()`). Until then, any change to `SshTransport`'s constructor or `parse_ssh_host()`'s contract needs to be checked against all three call sites.

### REFACTOR-007: BackupManifest and UpgradeManifest duplicate the label-derived config block

**Status**: Resolved (2026-08-25)
**Discovered**: 2026-08-10 during a cleanup review of the `paude backup` work

`BackupManifest` and `UpgradeManifest` embedded an identical ~9-field
"label-derived session config" block, and both loaders re-implemented the same
JSON-list-to-tuple normalization for `agent_providers`, so a new label meant
editing four places.

Resolved by the session-rebuild consolidation. Both manifests now inherit
`SessionSpec` (`backends/labels.py`), which declares those nine fields once
beside the label constants they come from, along with the codec that produces
them. Inheritance rather than an embedded `spec` field is what keeps
`asdict()` flat, so existing `upgrades.json` files and backup bundles are
unchanged -- guarded by tests that parse a literal written the way 0.20.2 wrote
it and assert the serialized key set.

Inheritance dedupes the *schema*, not the *copying*: `_manifest_from_state` and
`_build_manifest` still assign the fields explicitly so mypy can check them, so
a tenth label could still be silently dropped. Both now have a drift guard that
loops over `dataclasses.fields(SessionSpec)` and fails by name if either
manifest does not carry one.

The layering violation behind it went with it: `src/paude/cli` no longer
imports a `PAUDE_LABEL_*` constant or touches a `PodmanBackend` private.

### REFACTOR-008: streaming-subprocess lifecycle is duplicated between engine and SSH transport

**Status**: Open
**Priority**: Low (drift-prone; three copies of the same failure-translation idiom)
**Discovered**: 2026-08-11 during a `/simplify` review of the streaming-backup work

`ContainerEngine.stream_run` (`src/paude/container/engine.py:75-115`) owns a full
piped-subprocess lifecycle: drain stderr on a background thread (so it can't
fill its pipe and deadlock the stdout read), reap/kill on exception, and
translate a non-zero exit into `RuntimeError(stderr or fallback)`. The
`SshTransport` already implements that same drain/reap/`RuntimeError(detail or
"…")` pattern twice internally — `copy_from_host` (`src/paude/transport/ssh.py:157-183`)
and `_pipe_tar` (`ssh.py:194-208`) — so the "run a piped subprocess and turn
failure+stderr into a RuntimeError" idiom now exists in **three** places, and
the `bytes.decode(errors="replace").strip() or <fallback>` construction in **four**.

Compounding it, the transport exposes `popen_binary` (`transport/base.py:29`,
`local.py:41-52`, `ssh.py:94-108`) as a **raw** `Popen` whose stderr pipe will
deadlock the stdout read unless the caller drains it concurrently — i.e. the
transport publishes the *unsafe* primitive while keeping its *safe* version
(the lifecycle above) private to two methods. A future streaming caller (restore
stream, log tail) re-learns the drain/reap dance or deadlocks.

Deeper fix (deferred — reaches well outside the backup diff into stable transfer
code): have the **transport** own a managed binary-streaming context manager
(it already knows the local-vs-SSH `ssh_base`/`shlex.join` wrapping and already
implements the lifecycle), collapse `stream_run` to a thin `self.binary`-prepending
pass-through symmetric with how `run()` delegates to `transport.run()`, and
rebuild `_pipe_tar`/`copy_from_host` on the same primitive — retiring two of the
three copies and removing the raw-`Popen` leak. Left out of the `/simplify` pass
because rewiring `_pipe_tar`/`copy_from_host` changes pre-existing, well-tested
SSH transfer paths untouched by the streaming-backup work.

### REFACTOR-006: Credential-provider reconciliation duplicated between upgrade and resolver

**Status**: Open
**Priority**: Low (drift-prone; identical user-facing error string in two places)
**Discovered**: 2026-08-08 during a cleanup review of the `paude upgrade --add-agent` work

`_apply_overrides()` in `src/paude/cli/upgrade.py` and `_resolve_agents_and_providers()` in `src/paude/config/resolver.py` independently implement the same "reconcile credential providers against the agent→provider mapping" logic:

1. Deriving the mapped-provider set from a composition (`mapped_providers = list(dict.fromkeys(a.config.provider for a in composition.agents if a.config.provider))` in `upgrade.py` vs `_dedupe(provider for _, provider in result.agent_providers)` at `resolver.py:315`). The same idiom also appears as the fallback in `get_session_credential_providers()` at `src/paude/backends/podman/helpers.py:306-312`.
2. Validating an explicit `--providers` set covers every mapped provider, including the **verbatim** error string `"Credential providers must include every mapped provider; missing: "` (`upgrade.py` ↔ `resolver.py:324`).

This predates the `--add-agent` work (the `--providers` validation branch was already in `upgrade.py`); the cleanup pass left it in place because a proper fix reaches outside the changed code and is entangled with the resolver's `SettingValue`/provenance tracking. Consider extracting a shared `reconcile_credential_providers(agent_providers, explicit_providers) -> list[str]` (plus a `providers_from_composition(composition)` helper) next to `_derive_agent_providers` in `resolver.py`, and calling it from both `create` and `upgrade` so the invariant and error wording can't drift.

### REFACTOR-009: Proxy IP is inspected back from an auto-allocated subnet instead of being chosen deterministically

**Status**: Open
**Priority**: Low (bounded retry makes it reliable today; deterministic subnet is the deeper fix)
**Discovered**: 2026-08-14 during a `/simplify` review of the proxy-IP race fix

`PodmanProxyManager.create_proxy` (`src/paude/backends/podman/proxy.py`) lets
Podman auto-allocate the session network's subnet (`create_internal_network`
with no `--subnet`), then discovers the gateway/proxy IP by running `podman
network inspect` (`_get_proxy_ip` → `NetworkManager.get_network_gateway`) and
deriving `gateway + 1`. The inspect can race the create — on a `--disable-dns`
network run under CI load, `network inspect` occasionally returns before the
subnet/IPAM is populated, so the proxy IP comes back `None`. The current fix is
a bounded retry poll in `_get_proxy_ip` (5 attempts, 0.25s apart) plus a
Podman-gated hard error when the IP still can't be determined.

The retry is a reasonable, low-risk interim measure, but it is a workaround for
a design that could be deterministic: passing an explicit `--subnet` at network
create time makes the gateway/proxy IP known *without inspecting at all*,
eliminating the whole race class — and with it the retry poll, the `None`
return, the hard-error gate, and the Docker hostname-fallback branch in
`session_setup.py`. The catch (why it's deferred): an explicit subnet trades the
inspect race for a subnet-allocation/collision problem — concurrent sessions
would each need a distinct, non-colliding subnet within a private range, which
Podman's auto-allocator currently handles for free. That's a meaningful new
subsystem. If this race keeps recurring, the deterministic subnet is the actual
fix and should be prioritized over widening the retry budget.

### PERF-001: the same container labels are fetched up to four times per command

**Status**: Open
**Priority**: Medium (every fetch is an SSH round trip on a `--host` session)
**Discovered**: 2026-08-25 during a `/simplify` review of the session-rebuild
consolidation

Measured by instrumenting `Transport.run` and driving the real paths.
`ControlPersist` is off (`transport/ssh.py`), so sequential calls do not share
an SSH master -- each is a full handshake.

- `backend.start_session_no_attach` issues 21 engine round trips, **four** of
  them the byte-identical `podman ps --format json -a --filter label=app=paude`.
  Two pairs cause it: `SessionSetup.sync_sandbox_config` calls
  `get_session_labels` then `get_session_composition` (which re-fetches), and
  `start_session_containers` calls `get_session_composition` then
  `get_session_credential_providers`. `connect_session` and
  `update_allowed_domains` repeat the pattern.
- `paude backup` pays three `inspect`-style calls (`resources.exists`,
  `.running`, `.image`) before the `ps` that `resources.labels` makes, for
  facts that one `ps` record already carries (`State`, `Image`).

The consolidation built exactly the pieces that fix the first one --
`spec_from_labels` + `composition_for_spec` + `credential_providers_for_spec`
are pure and I/O-free -- and applied them to `upgrade` and `backup` only.
One `spec = spec_from_labels(get_session_labels(runner, name))` per command,
then the two pure derivations, takes `start`/`connect`/`domains --set` from
four fetches to one. Left out of the consolidation because those call sites are
outside its diff.

The backup half needs care rather than a mechanical swap: `resources.exists`
keys off the *container name* while `resources.labels` keys off the *session
label*, so they are not the same predicate, and `get_container_image` uses
`ContainerEngine.image_name_format` (`{{.ImageName}}` vs `{{.Config.Image}}`),
which is not verified to equal `ps`'s `Image` field on both engines. Collapsing
them wants a `describe()` returning existence, state, image and labels from one
fetch, checked against both engines.

Separately, `build_session_images` runs the agent and proxy builds
sequentially though they share no inputs; `paude upgrade` always rebuilds both.
Parallelising is the largest raw saving here, but both stream progress with
`capture=False`, so it is only worth doing alongside capturing and replaying
the proxy pull's output.

### RESTORE-001: `paude restore` is still a stub, and needs a volume-import primitive

**Status**: Open
**Priority**: Medium (the command exists, is documented as planned, and exits 2)
**Discovered**: 2026-08-25 while sequencing the session-rebuild consolidation

`session_restore` (`cli/backup.py`) validates a bundle and prints the restore it
would perform. The consolidation was sequenced to make the implementation small:
it can now reuse `build_session_images` / `prepare_session_mounts` /
`session_config_from_spec` (`cli/session_rebuild.py`) plus
`SessionResources.teardown_for_rebuild`, and read its configuration straight off
`BackupManifest`'s inherited `SessionSpec`.

The blocker is that `VolumeArchiver` (`backends/podman/volume_archive.py`) has
`export_volume` / `export_volume_to_remote_file` / `volume_size_bytes` and **no
inverse**. Building one means a helper container running `tar xzf -` from stdin
under the same `--user root --security-opt label=disable` escape hatch
RUNTIME-006 already flags -- except *writing* attacker-influenced tar content
rather than reading. Path traversal, symlink escape, device nodes, and
verifying `archive_sha256` **before** extracting are all open questions that
want their own review. Note also that `--confirm` (restore over an existing
session) has to call the destructive `delete_session`, which is exactly the
operation `teardown_for_rebuild` was kept separate from.

`--host` retargeting and `--name` renaming raise product questions the
consolidation does not answer: whether a rename rewrites `PAUDE_LABEL_WORKSPACE`,
whether `--host` re-syncs configs and registers `remote_config_dir` (see
UPGRADE-002), and whether the bundle's recorded `image` is reused or rebuilt.

## Correctness Backlog

Lower-severity correctness/robustness issues surfaced during code review.

### TEST-005: the rebuild suites patch definition sites, which pins production import style

**Status**: Open
**Priority**: Low
**Discovered**: 2026-08-25 during a `/simplify` review of the session-rebuild
consolidation

`tests/test_create_podman.py`, `tests/test_upgrade.py` and
`tests/test_session_rebuild.py` patch `paude.container.ImageManager`,
`paude.mounts.build_mounts` and `paude.transport.config_sync.*` at the modules
that *define* them rather than where they are looked up. That works only
because `cli/session_rebuild.py` imports them inside the functions that use
them, so the module docstring has to warn future editors not to hoist those
imports -- a test artifact constraining production code.

Two fixes, ascending: patch the lookup site
(`paude.cli.session_rebuild.ImageManager`), which frees the imports; or, better
and in line with the project's own "wrap external commands in testable classes"
rule, inject the `ImageManager` into `build_session_images` the way `engine`
already is, so there is nothing to patch. The second touches all three suites,
which the consolidation had just rewritten, so it was deferred.

Related, and larger: `SessionSpec`'s natural sibling is `SessionConfig` in
`backends/base.py` -- they are the same domain family and
`session_config_from_spec` is the conversion between them, currently owned by a
third package (`cli/`). Moving the spec there and making the conversion
`SessionConfig.from_spec` would put two durable JSON schemas in a module named
for domain types rather than one named for container-label constants. Deferred:
a rename touching many imports, with no behaviour change to show for it.
`cli/helpers._detect_dev_script_dir` is similarly misplaced -- after the
consolidation its only caller is `cli/session_rebuild.py`, and it is an
`ImageManager` concern, not a CLI one, yet its path arithmetic is hardcoded to
living in `cli/`.

### UPGRADE-002: upgrading an SSH session leaks its remote config directory

**Status**: Open
**Severity**: Medium (leaks a directory per upgrade; registry actively lies)
**Discovered**: 2026-08-25 during the session-rebuild consolidation

A `--host` session's config files are synced to a temp directory on the remote
host, and the registry records where (`remote_config_dir`). Upgrade re-syncs
them -- it needs the new paths to remap its bind mounts -- but discards the new
`remote_base` and never updates the registry.
`SessionRegistry.refresh_from_session` (`registry.py`) *deliberately* preserves
`remote_config_dir`, so after an upgrade the entry still names the pre-upgrade
directory.

Two consequences: every upgrade of a remote session leaves one
`/tmp/paude-config-*` tree behind on the remote host, and
`_cleanup_remote_config_dir` (`cli/commands/delete.py`) then deletes the
*stale* directory on `paude delete` while leaking the live one.

The plumbing is now in place -- `prepare_session_mounts` returns the
`RemoteConfigPaths` that upgrade drops on the floor -- so the fix is small, but
it needs a new `SessionRegistry` method (`refresh_from_session` cannot carry the
field, by design) and it is a real behaviour change with a visible effect on
`paude delete`, so it wants its own commit and test rather than riding along
with a refactor.

### UPGRADE-003: an upgraded session silently loses its `--agent-args`

**Status**: Open
**Severity**: Low
**Discovered**: 2026-08-25 during the session-rebuild consolidation

`SessionConfig.args` is live: `backends/session_env.py` turns it into the
primary agent's `args_env_var`. `create` passes the parsed `--agent-args`;
upgrade passes nothing, so a rebuilt container comes back without them. Upgrade
even computes `parsed_args` (it calls `_prepare_session_create` with
`claude_args=None`) and discards the result.

Preserved deliberately through the consolidation, because passing the computed
value would change nothing -- it is always empty -- and *fixing* it properly
means persisting the agent args somewhere durable (a label, or the upgrade
manifest), which is a feature rather than a refactor.

### UPGRADE-004: `Path.cwd()` is a poor workspace fallback for a session with no workspace label

**Status**: Open
**Severity**: Low (only reachable for sessions predating the workspace label)
**Discovered**: 2026-08-25 during the session-rebuild consolidation

`_resolve_base_from_view` (`cli/upgrade.py`) falls back to `Path.cwd()` when the
container has no `paude.io/workspace` label, so the rebuilt session records
whatever directory the user happened to run `paude upgrade` from.
`build_session_from_container` uses `Path("/")` for the same missing label, and
a test pins that, so the two disagree.

Preserved on both sides by the consolidation --
`labels.workspace_from_labels` returns `Path | None` and each caller applies its
own fallback, so the disagreement is at least visible now instead of buried in a
shared default. `SessionConfig.workspace` only reaches labels and the `Session`
object (never a bind mount), so this is metadata, not a mount bug.

### CREATE-001: a failed `create_session` performs no rollback

**Status**: Open
**Severity**: Low (leaves a half-built session that `paude delete` can clear)
**Discovered**: 2026-08-25 while characterizing the create pipeline

`_create_session_or_exit` (`cli/create_podman.py`) cleans up on failure by
calling `delete_session(session.name, ...)`, but `session` is only bound by the
`create_session` call itself. When *that* is what failed, the cleanup raises
`UnboundLocalError` into its own best-effort `except Exception: pass`, so
nothing is rolled back. A failure in `start_session_no_attach` (where `session`
*is* bound) rolls back correctly.

The container itself is cleaned up by `PodmanBackend`'s own
`rollback_create`, so the leak is bounded, but the outer cleanup is dead code on
the path it most needs to run. Both behaviours are characterized in
`tests/test_create_podman.py` so the fix cannot be made accidentally. The fix is
to use `session_config.name` (or the generated name) instead.

### CONFIG-001: three `SessionConfig` fields are never read

**Status**: Open
**Severity**: Low (dead weight, and one of them is misleading)
**Discovered**: 2026-08-25 during the session-rebuild consolidation

`SessionConfig.workdir`, `.network` and `.wait_for_ready` (`backends/base.py`)
are set by callers and read by nobody. `workdir` is the misleading one: `create`
sets it to the *host* workspace path, while `SessionSetup` hardcodes
`workdir="/pvc"` when it creates the container, so the field reads like a knob
that does something. `SessionConfig`'s docstring is also stale -- it documents
through `ports` and omits `proxy_image`, the `agent*` fields, `gpu`,
`reuse_volume` and the `otel_*` fields.

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

### NET-001: `NetworkManager.remove_network` silently swallows failures

**Status**: Open
**Severity**: Low
**Discovered**: 2026-08-24 while fixing the `start_if_needed` stale-network bug (see git history for the fix that stopped the risky call site from invoking this)

`remove_network()` in `src/paude/container/network.py` runs `podman network rm <name>` with `check=False` and doesn't inspect the `CompletedProcess` result at all, so a failed removal (e.g. the network still has a container attached) is indistinguishable from success. The one call site that removed a network still in active use by a live agent container (`PodmanProxyManager.start_if_needed`'s recreate-missing-proxy branch) has been fixed to no longer call this in that situation, but the underlying swallow-everything behavior in `remove_network` itself is still present for any future caller. A fix would capture the result and at least log a warning on non-zero exit (mirroring the pattern already used for `echo_captured_stderr`).

## Agent Limitations

Issues caused by upstream agent behavior, not paude bugs.

### AGENT-003: Remove the Gemini agent entirely (deprecated upstream)

**Status**: Open
**Priority**: Medium (supersedes AGENT-001 and AGENT-002, and resolves a live build bug)
**Discovered**: 2026-08-25 during a codebase maintenance review

Google has deprecated the Gemini CLI, so paude should drop the agent rather than
keep carrying workarounds for it. This supersedes **AGENT-001** (idle-session
OAuth token expiry) and **AGENT-002** (proxy support broken in 0.36.0+, which is
why `src/paude/agents/gemini.py` has been pinned to `@google/gemini-cli@0.35.3`
since 2026-05-12); both entries can be deleted along with the agent.

**Decide this first — it is the actual blocker.** `src/paude/agents/gascity.py`
declares `bundled_agents=["claude", "gemini"]`, so removing gemini forces a
decision about what gascity bundles. Everything else is mechanical.

**Removal surface (~34 files).** Source: `agents/gemini.py`, `agents/__init__.py`
(import + `_AGENTS`), `agents/gascity.py` (`bundled_agents`),
`providers/agent_providers.py` (`AGENT_PROVIDERS` **and** `DEFAULT_PROVIDER` —
the default provider is stored in both), `domains.py` (`DOMAIN_ALIASES`),
`otel.py` (`builders`), `hash.py`, `registry.py`, `cli/create.py`,
`cli/help.py`, `container/build_context.py`. Containers:
`containers/paude/Dockerfile` (COPY line) and
`containers/paude/patch-gemini-otel-proxy.sh` (delete). Packaging:
`pyproject.toml` `force-include`. Docs: `README.md`, `docs/SESSIONS.md`. Plus
~19 test files, of which `tests/test_gascity.py` and `tests/test_agents.py` need
real edits rather than deletions.

Note that existing sessions created with gemini will still carry a
`paude.io/agent=gemini` label, so `paude list` / `paude upgrade` need to degrade
sensibly (or fail with a clear message) rather than raising a bare `ValueError`
from `get_agent()`.

**A live bug that removal also fixes.** `generate_workspace_dockerfile`
(`src/paude/config/dockerfile.py:219-231`) COPYs `patch-proxy-fetch.sh` and the
two OpenClaw patch scripts, but **not** `patch-gemini-otel-proxy.sh` — while
`agents/gemini.py:59-61` emits

```
RUN npm install -g @google/gemini-cli@latest && /usr/local/bin/patch-gemini-otel-proxy.sh --force 2>&1
```

so the generated Dockerfile references a file it never copied and the build
fails. Reproduced against `generate_workspace_dockerfile(cfg, agent=get_agent("gemini"))`:
`REFERENCED BUT NEVER COPIED: ['patch-gemini-otel-proxy.sh']`.

Trigger: a `paude.json` setting `base_image` or `dockerfile` (so
`_resolve_custom_base` returns `using_default=False`, `container/image.py:357-366`)
plus gemini in the agent set — directly or via gascity. The default-image path
(`generate_pip_install_dockerfile`) is unaffected only because its base was built
from the *static* `containers/paude/Dockerfile:101`, which does have the COPY.
This is REFACTOR-004 recurring on a third script; see that entry.

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

### SEC-004: Gemini and Cursor persist real credentials onto the `/pvc` volume

**Status**: Open
**Severity**: Medium
**Discovered**: 2026-08-10 while designing `paude backup`

paude's credential model keeps real provider secrets on the proxy sidecar; the agent container and its `/pvc` volume normally hold only stubs or proxy-synthesized auth (e.g. `/pvc/.codex/auth.json` contains synthetic values — the real ChatGPT tokens live on the proxy-only auth volume). Two agents deviate and land a **real** token on `/pvc`:

- **Gemini** (`google` OAuth) writes a real `refresh_token` to `~/.gemini/oauth_creds.json` → `/pvc/.gemini/oauth_creds.json` (the same file referenced by AGENT-001).
- **Cursor**'s `auth.json` (access/refresh tokens) is persisted to `/pvc/.config/cursor/auth.json` — host-seeded on `--host` sessions (`src/paude/agents/cursor.py:117-126`) or written by an in-container `agent login`.

Because `/pvc` is the durable session volume, these tokens sit at rest on disk, and on remote sessions the Cursor token is copied to the remote host. `paude backup` strips them (`AgentConfig.credential_file_names` → `credential_exclude_globs()` in `src/paude/cli/upgrade_persistence.py`) so bundles never carry a live token — but that is a stopgap for the backup path only. The real fix is to keep these tokens off the volume entirely (proxy-mediated OAuth like codex's ChatGPT flow, or a proxy-only auth volume), so the agent container never holds a real token. Until then, treat `/pvc` for Gemini/Cursor sessions as containing live credentials.

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

### RUNTIME-003: Agent install scripts have no curl timeout — headless start can hang on a blocked network

**Status**: Open
**Priority**: Low
**Discovered**: 2026-08-10 while designing podman integration tests for `paude upgrade`

The codex install script (`src/paude/agents/codex.py` — the two `curl -fsSL`
GitHub-release fetches) sets no `--connect-timeout`/`--max-time`. `install_agent`
(`containers/paude/entrypoint-lib-install.sh`) runs the *primary* agent's install
script synchronously at headless start. On a host where the target domain is not
merely refused but silently dropped (e.g. a restrictive egress policy), curl can
block on the TCP connect for a long OS-default timeout instead of failing fast,
stalling `start_session_no_attach`. This surfaced while designing
`tests/integration/test_upgrade_podman.py`: making codex the *primary* agent
risked hanging CI, so those tests keep codex a secondary agent (only the primary
is installed at headless start). Add `--connect-timeout`/`--max-time` to the
agent install curls (codex, and any other curl-based installer) so a blocked
network fails fast with a clear error rather than hanging startup.

### RUNTIME-004: `paude upgrade` aborted on an un-copyable migration source, and stranded pre-pin `/pvc` ownership

**Status**: Resolved (2026-08-11)
**Discovered**: 2026-08-11 while upgrading a remote codex/ChatGPT session
(`0.20.0a8` → `0.20.0a10`)

Two independent problems, both hit during one upgrade:

1. **Crash (opaque):** the pre-upgrade state migration (`_MIGRATE_SCRIPT` in
   `src/paude/cli/upgrade_persistence.py`) ran under `set -e` with an
   **unguarded** `cp`, so the *first* un-copyable path aborted the whole
   upgrade. In the reported case the runtime UID matched the `/pvc` owner
   (both `997`), so it was **not** a `/pvc` write/EACCES problem — the copy
   died trying to **read the source** `cp: cannot open '/home/paude/.gitconfig'
   for reading: Permission denied`. For a `--host` (remote) session
   `~/.gitconfig` is a **read-only host file bind-mounted** into the container,
   owned by `root` (and, on an SELinux host, unreadable by the `997` runtime
   user regardless of the `644` bits). The real error never reached the user
   because the handler printed only `str(CalledProcessError)`, which drops
   `.stderr`. Same class of swallowed-error as RUNTIME-001.

2. **Stranded ownership (second-order):** pinning the runtime user to `1000:0`
   (commit `8edd527`) left volumes created by the earlier `useradd --system`
   UID (`~997`) owned by that stale UID. `paude upgrade` reuses the `/pvc`
   named volume, and `fix_volume_permissions` never reconciled ownership on
   podman (it early-returned, was non-recursive, and chowned to the *name*
   `paude`, not `uid:gid`). So after upgrading to a `1000:0` image the recreated
   container couldn't read `/pvc/.codex/auth.json` (mode `0600`, owner `~997`)
   → Codex ChatGPT OAuth silently broke. Related to SEC-004 / AGENT-001
   (auth on `/pvc`).

**Fix**:
- `_MIGRATE_SCRIPT` guards each `cp`/`mkdir` (warn-and-continue via a `warn`
  helper), mirroring the runtime entrypoint's hardened `persist_config_dir`, so
  one un-copyable path can no longer abort the upgrade. The copy runs as the old
  container's default user (best-effort salvage) and leaves `cp`'s own error
  visible.
- Migration failures now re-raise with the captured `stderr`, and the generic
  upgrade handler surfaces `CalledProcessError.stderr` instead of the opaque
  exit-status string.
- `ContainerRunner.reconcile_volume_ownership()` chowns `/pvc` to the image's
  actual `paude` user — resolved at runtime with `id -u paude`/`id -g paude`,
  **not** a hardcoded UID (a wrong hardcoded value would itself lock the
  container out of its volume), guarded by a `stat` probe so the already-correct
  case skips the recursive walk. It runs on the **recreated** container via the
  now podman-capable `fix_volume_permissions`, before `configure_codex` and the
  agent entrypoint — the sole, correctly-placed reconcile. (An earlier draft
  reconciled *before* the migration copy too; that was removed because the old
  container can own the volume at a pre-pin UID, so chowning it there would only
  remove its own write access.)

### RUNTIME-005: Remote sessions bind-mount `~/.gitconfig`, which SELinux can make unreadable

**Status**: Open
**Priority**: Medium (a fresh remote session on an SELinux host can silently end
up with no global git config / identity)
**Discovered**: 2026-08-11 while debugging a remote (`--host`) codex upgrade

Config sync is engine-split: local engines copy host config into `/credentials/`
via `podman cp` (`ConfigSyncer`, `src/paude/backends/podman/sync.py`), while SSH
remotes skip that and rely on bind mounts instead (`sync.py:25`, early
`return`). So on a remote session `build_mounts(include_config=True)`
(`src/paude/mounts.py:73-79`) bind-mounts the host gitconfig read-only at
`$HOME/.gitconfig` (the source is transferred to the remote's `/tmp` by
`sync_configs_to_remote` and remapped). On an SELinux-enforcing host that
bind-mounted file is owned by root (the host user maps to container-root under
rootless podman) and carries a host SELinux label the container's confined
`paude` process cannot read — despite `0644` mode bits. (The `podman cp` copy
path used for local engines exists precisely to avoid this, per
`mounts.py:48-50`.)

The consequence surfaces on a **fresh** remote session: `setup_gitconfig`
(`containers/paude/entrypoint-lib-credentials.sh:88-128`) seeds `/pvc/.gitconfig`
by `cp`-ing from `$HOME/.gitconfig` when `/pvc/.gitconfig` doesn't yet exist. If
SELinux blocks that read, the `cp` fails silently (`2>/dev/null || true`),
`/pvc/.gitconfig` is never created, `GIT_CONFIG_GLOBAL` is never exported, and git
falls back to the unreadable `~/.gitconfig` — leaving the session with no global
git config or identity. Sessions that already have a populated `/pvc/.gitconfig`
(e.g. an existing session being upgraded) are unaffected, because the seed step
is skipped and `GIT_CONFIG_GLOBAL` still points at the readable PVC copy. Same
class of silently-swallowed setup failure as RUNTIME-001.

Fix directions: make the remote path copy gitconfig into `/credentials/` like the
local path (so the entrypoint reads a readable, correctly-labeled source), or
relabel the bind mount (`:z`/`:Z`), or have `setup_gitconfig` fall back to
`touch`-ing `/pvc/.gitconfig` and filling identity from
`PAUDE_GIT_USER_NAME`/`PAUDE_GIT_USER_EMAIL` when the source is unreadable, so
`GIT_CONFIG_GLOBAL` is always set even when the seed copy fails.

### RUNTIME-006: Root is required for backup, volume-ownership reconciliation, and in-container config writes

**Status**: Open
**Priority**: Medium (contradicts paude's non-root security posture; no known exploit, but the largest gap between documented and actual privilege)
**Discovered**: 2026-08-12 during a security-model audit of `paude backup`'s root usage

The agent's own runtime process always executes as the non-root `paude` user — every session command runs unprivileged. But three helper code paths invoke podman/docker `--user root` (or `exec --user root`) against volumes/containers that are otherwise never touched as root:

1. `volume_archive.py`'s `_helper_run_args()` — `--user root` **and** `--security-opt label=disable` on a throwaway container that `tar`/`du`s the entire `/pvc` volume for `paude backup`/`volume_size_bytes`. Needed because the volume can hold root-owned `0600` agent state, nested-container files with foreign SELinux MCS categories, and pre-UID-pin drift artifacts — a non-root, SELinux-confined read fails partway through with "Permission denied," and `paude backup` is designed to fail loudly rather than silently produce an incomplete archive. This is the single most privileged operation in the codebase (root plus SELinux confinement disabled), though scoped by a read-only mount and a throwaway container.
2. `runner.py`'s `reconcile_volume_ownership()` — `exec --user root` to migrate volumes created before the 2026-08-10 UID pin (`8edd527`) onto the current pinned `1000:0` identity. Not itself a security-relevant read/write escalation (it acts on a volume the user's own container already controls, and the actual `chown -R` only fires when ownership has drifted), but the root `exec` call runs unconditionally on every container start/upgrade — forever, not just for the legacy volumes it exists to fix.
3. `container/files.py`'s `replace_file()`, `runner.py`'s `inject_file()`, and `backends/podman/sync.py`'s `ConfigSyncer` — `exec --user root` for one-shot config/credential writes (CA cert, `/credentials/` staging), immediately chowned back to `paude`.

Ideas worth investigating for closing this out (none attempted yet — this is a goal, not a plan):
- Replace full `--user root` on the backup helper with a narrower Linux capability (e.g. `--cap-drop=all --cap-add=DAC_READ_SEARCH`) if that's sufficient to bypass DAC permission checks for a read-only `tar`/`du` without full root. The SELinux/MAC side (foreign MCS categories) would still need `label=disable` or an equivalent relabel regardless — capabilities don't touch MAC enforcement.
- Gate `reconcile_volume_ownership()` behind a one-time marker (e.g. a stamp file or manifest field) so the root `exec` itself stops running once a volume is confirmed to already be on the pinned UID, instead of checking on every start/upgrade indefinitely.
- For the config-write helpers, investigate execing as the file's existing owner when known, falling back to root only when the owner is unknown or the file doesn't yet exist.

No path forward here is trivial — SELinux MAC enforcement and `podman exec`'s user model are real constraints, not oversights — but the goal is for paude to never invoke any podman/docker operation as root.

## Test Suite

### TEST-004: Remaining test-suite duplication and untestable-by-design seams

**Status**: Open
**Priority**: Medium (this is what makes refactoring `src/` expensive)
**Discovered**: 2026-08-25 during the test-infrastructure pass that added
`tests/fakes.py`'s `make_backend`/`make_runner`/`FakeTransport`

The shared doubles now exist and the poll-sleep tax is gone, but three larger
items were deliberately left:

1. **`git_remote/utils.py` calls `subprocess.run` 17 times directly**
   (`workflow.py` adds 5 more), which is why
   `@patch("paude.git_remote.subprocess.run")` is the single most-patched
   target in the suite at 79 sites. This violates the project's own rule in
   `docs/CODING_STANDARDS.md` ("Wrap external commands ... in testable classes
   rather than calling subprocess directly"). Routing git through an injectable
   runner would delete those 79 patches and let a `FakeTransport`-style double
   record git invocations. Deferred because it is a `src/` change, and the
   test-infrastructure work was kept behaviour-preserving.

2. **Half-resolved (2026-08-25).** The 15 `backend._runner` assignments in
   `tests/test_upgrade.py` are gone: the session-rebuild consolidation
   (REFACTOR-007) gave `PodmanBackend` a `resources` collaborator, `cli/` moved
   onto it, and the tests moved onto `tests/fakes.py`'s `make_backend` via one
   `_upgrade_backend` helper. Assertions now land on the doubles the test
   injected rather than on attributes read back out of the system under test.

   The other 15 -- `MagicMock()` with `__class__ = PodmanBackend` -- remain, and
   the original entry was wrong to lump them together. They are in the
   `session_upgrade` tests, where `_upgrade_podman` is patched out entirely;
   they exist only to satisfy `isinstance(backend_obj, PodmanBackend)` while
   controlling `get_session`/`stop_session`. No layering change reaches them.
   Fixing them means either making `make_backend` cheap enough to use where a
   bare mock is wanted, or having `session_upgrade` dispatch on something other
   than `isinstance`.

3. **`tests/test_cli.py` (2041 lines) is a grab-bag** whose classes duplicate
   topics that already have dedicated files (`TestBlockedDomainsCLI` vs
   `test_blocked_domains.py`, `TestParseCopyPath` vs `test_remote_copy.py`,
   `TestAgentSpecificDomainExpansion` vs `test_domains.py`). Splitting it along
   its existing class boundaries is mechanical. Separately,
   `tests/test_agents.py` (2000 lines, 45 classes, zero mocks) is a
   6-agents x 6-concerns copy-paste grid that wants parametrizing -- best done
   after the Gemini removal in AGENT-003, which deletes one row of it.

For reference, the pass that opened this entry took `make test` from 26.9s to
6.8s, mock/patch constructions from 2028 to 1937, and put `tests/` under mypy.

### TEST-003: Test signatures are unannotated, so mypy runs relaxed over `tests/`

**Status**: Open
**Priority**: Low (the checks that catch real bugs are already on)
**Discovered**: 2026-08-25 while extending `make typecheck` to cover `tests/`

`tests/` is now type-checked (it is the larger half of the repo's Python), but
`[[tool.mypy.overrides]]` for `tests.*` disables `disallow_untyped_defs`,
`disallow_incomplete_defs` and `disallow_untyped_calls`. Without those three,
enabling mypy on tests surfaced 81 real errors, all since fixed; with them it
is 782, of which 701 are purely "this signature has no annotations".

The relaxation is deliberate: the value of checking tests is catching wrong
argument types, stale attribute names and str/bytes confusion, and strict mode
still enforces all of that. Annotating ~1900 test signatures buys little and
would churn every test file.

To tighten later, drop the override keys one at a time — `disallow_untyped_calls`
first (47 errors, mostly calls into unannotated test helpers), then
`disallow_incomplete_defs` (a further ~81), then `disallow_untyped_defs`
(~654). Note `tests/*` also waives `E501` in the ruff per-file ignores, so test
code has one fewer guardrail than `src` either way.

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

### TEST-003: In-place agent-set / provider upgrades — integration coverage

**Status**: Partially resolved (2026-08-10)
**Priority**: Low
**Discovered**: 2026-08-08 while adding `--add-agent`/`--agents` to `paude upgrade`

**Resolved (2026-08-10):** `tests/integration/test_upgrade_podman.py`
(`TestPodmanUpgradeReconfigure`) now covers the in-place reconfiguration paths
end-to-end on a real container (gated by the `integration`/`podman` markers; run
in CI via `make test-podman`): adding an agent in place (`--add-agent`, primary
preserved, credential providers unioned), swapping a provider in place
(chatgpt→openai and the reverse via `--agent-provider`), that `configure_codex`
rewrites `config.toml` and clears `auth.json` at start, that the `/pvc` volume
and existing agent state survive the recreate, and that labels reflect the new
set. The image build is mocked (the CI base image ships no agents), and codex is
kept a *secondary* agent to avoid its no-timeout install curl at headless start
(see RUNTIME-003).

**Still open (deliberately deferred):** verifying the *physical* agent-binary
install from a real image rebuild — that a rebuilt layer actually places `codex`
+ `codex-code-mode-host` on `$PATH`. This needs an unmocked
`ensure_default_image(force_rebuild=True)`, which downloads from GitHub's
`latest` release (multi-minute, network/rate-limit flaky) — too costly for the
PR-blocking `podman-integration` job. It is already enforced at image-build time
by codex's own `test -x` verification (`agents/codex.py`, `agents/base.py`) and
covered by image-build unit tests, so this is low-value to add to CI. If ever
added, gate it behind an opt-in env var (e.g. `PAUDE_TEST_REAL_REBUILD=1`) so it
stays out of the default `-m podman` run.

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

