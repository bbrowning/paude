# Security Model

The container intentionally restricts certain operations:

| Resource | Access | Purpose |
|----------|--------|---------|
| Network | proxy-filtered (Vertex AI, PyPI, GitHub, agent-specific) | Prevents data exfiltration |
| Proxy source IP | when the proxy has a fixed network IP (as on Podman), only the paired agent container's IP is accepted | Defense-in-depth: prevents other containers/hosts from riding the proxy's egress allowlist |
| Host working directory | not mounted | Code lives in a separate named volume, synced via git (see [Code Synchronization](SESSIONS.md#code-synchronization)) — the agent never touches host files directly |
| gcloud credentials | never enter the agent's container; a non-functional stub ADC file is injected instead | Real credentials live only on the network-filtering proxy sidecar, which signs Vertex AI requests on the container's behalf |
| Agent config and session state | copied in, then stored on the private session volume | Prevents host config poisoning while preserving settings and history across container upgrades |
| `~/.gitconfig` | host config is read-only; a writable copy is persisted at `/pvc/.gitconfig` for local and `--host` remote sessions. The author identity is additionally resolved on the host via `git config` (honoring global, XDG, system, and `includeIf` sources) and applied inside the container when the seeded config lacks one | Git identity |
| SSH keys | not mounted | Prevents git push via SSH |
| GitHub CLI config | not mounted | Prevents cached host credentials |
| `GH_TOKEN` (host) | never propagated | Set `PAUDE_GITHUB_TOKEN` before `create`/`start`; the real token goes only to the proxy sidecar, never the agent's container |
| Git credentials | not mounted | Prevents HTTPS git push |

## Verified Attack Vectors

These exfiltration paths have been tested and confirmed blocked:

| Attack Vector | Status | How |
|--------------|--------|-----|
| HTTP/HTTPS exfiltration | Blocked | Internal network has no external DNS; proxy allowlists only approved domains |
| Git push via SSH | Blocked | No `~/.ssh` mounted; DNS resolution fails anyway |
| Git push via HTTPS | Blocked | No credential helpers; no stored git credentials (`github.com` itself is reachable by default, but there's nothing to authenticate a push with) |
| GitHub CLI write ops | Relies on token scope — use a read-only fine-grained PAT | Use a read-only PAT via `PAUDE_GITHUB_TOKEN`; host `GH_TOKEN` never propagated |
| Modify cloud credentials | Blocked | Real credentials never reach the agent's container (see gcloud credentials, above); stored only on the proxy sidecar — see [Remote Hosts & Docker Backend](REMOTE.md) for how this differs between Podman and Docker |
| Escape container | Blocked | Non-root user; standard Podman isolation |

## Root-Privileged Helper Operations

The long-running agent container itself always runs as the non-root `paude` user (see "Escape container" above) — the agent's shell and every session command execute as `paude`, never root. A small number of short-lived, narrowly-scoped helper operations do run as root; they're listed here for transparency, and tracked as [RUNTIME-006](../KNOWN_ISSUES.md#runtime-006-root-is-required-for-backup-volume-ownership-reconciliation-and-in-container-config-writes) for further reduction.

| Operation | Why root is needed | Scope limits |
|-----------|--------------------|---------------|
| Backup archive helper (`volume_archive.py`) | `/pvc` accumulates files from multiple owners over a session's life — root-owned `0600` agent state, nested-container files carrying another container's SELinux MCS category, pre-UID-pin drift artifacts. A non-root, SELinux-confined read fails partway through a multi-GB backup with "Permission denied"; `paude backup` is designed to fail loudly rather than silently produce an incomplete archive | Read-only (`:ro`) volume mount; throwaway container destroyed after use; no capabilities added beyond `--user root` and disabling SELinux confinement |
| Volume ownership reconciliation (`reconcile_volume_ownership`) | Migrates volumes created before the runtime UID was pinned to `1000:0` (2026-08-10) onto the current pinned identity | Runs inside a container the invoking user already controls; target UID/GID is resolved at runtime via `id -u/-g paude`, never hardcoded; the actual `chown -R` only fires when ownership has actually drifted |
| Config/credential file writes (`inject_file`, `replace_file`, `ConfigSyncer`) | `podman exec` only runs as the container's default user unless overridden; writing a file the agent doesn't yet own (proxy CA cert, `/credentials/` staging) needs root for one atomic write | Root scope lasts for a single `exec` call; file ownership is corrected (chowned back to `paude`) immediately after |

## When is `--yolo` Safe?

```bash
# SAFE: Network filtered, cannot exfiltrate data
paude create --yolo

# DANGEROUS: Full network access, can send files anywhere
paude create --yolo --allowed-domains all
```

The `--yolo` flag enables autonomous execution (no confirmation prompts). This is safe when network filtering is active because the agent cannot exfiltrate files or secrets even if it reads them.

**Do not combine `--yolo` with `--allowed-domains all`** unless you fully trust the task.

`--allowed-endpoints HOST:PORT` is narrower than a global port exception: the
proxy requires both an allowed-domain host match and the exact endpoint match.
Endpoint rules never authorize a host on their own or grant that port elsewhere.

## Workspace Protection

The agent operates on its own copy of your code, not your host files. **Your protection is git itself.** Push important work to a remote before running in autonomous mode:

```bash
git push origin main
```

If something goes wrong inside the container, recovery is a clone away, and your host files were never at risk in the first place.

## Residual Risks

These risks are accepted by design:

1. **Workspace destruction**: The agent can delete files (including `.git`) in the container's own copy of the code. Mitigation: push to remote before autonomous sessions, so a destroyed container's copy is always recoverable.
2. **Secrets readable**: Any `.env` file synced into the container — via `--git` or `paude cp` — is readable there. Mitigation: network filtering prevents exfiltration; avoid `--allowed-domains all` with sensitive workspaces, and don't sync in `.gitignore`d secrets.
3. **No audit logging**: Commands executed aren't logged. This is a forensics gap, not a security breach vector.
4. **Port-forward exposure**: `--forward-port HOST_IP:HOST:CONTAINER` with a non-loopback `HOST_IP` (e.g. `0.0.0.0`) exposes the forwarded container port to your LAN with no additional authentication — anyone who can reach that host/port gets the same access a local loopback connection would. Mitigation: only bind non-loopback addresses on trusted networks; the default forms (bare `PORT` / `HOST:CONTAINER`) bind loopback only.
