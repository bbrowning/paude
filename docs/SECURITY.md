# Security Model

The container intentionally restricts certain operations:

| Resource | Access | Purpose |
|----------|--------|---------|
| Network | proxy-filtered (Vertex AI, PyPI, GitHub, agent-specific) | Prevents data exfiltration |
| Current directory | read-write | Working files |
| gcloud credentials | injected (Podman secret / oc cp) | Vertex AI auth |
| Agent config | copied in, not mounted | Prevents host config poisoning |
| `~/.gitconfig` | read-only | Git identity |
| SSH keys | not mounted | Prevents git push via SSH |
| GitHub CLI config | not mounted (uses /tmp/gh-config) | Prevents cached host credentials |
| `GH_TOKEN` (host) | never propagated | Use `PAUDE_GITHUB_TOKEN` or `--github-token` on start/connect |
| Git credentials | not mounted | Prevents HTTPS git push |

## Verified Attack Vectors

These exfiltration paths have been tested and confirmed blocked:

| Attack Vector | Status | How |
|--------------|--------|-----|
| HTTP/HTTPS exfiltration | Blocked | Internal network has no external DNS; proxy allowlists only approved domains |
| Git push via SSH | Blocked | No `~/.ssh` mounted; DNS resolution fails anyway |
| Git push via HTTPS | Blocked | No credential helpers; no stored credentials; DNS blocked |
| GitHub CLI write ops | Relies on token scope — use a read-only fine-grained PAT | Use read-only PAT via `PAUDE_GITHUB_TOKEN`; host `GH_TOKEN` never propagated |
| Modify cloud credentials | Blocked | Credentials injected via Podman secret (not mounted); read-only inside container |
| Escape container | Blocked | Non-root user; standard Podman isolation |

## When is `--yolo` Safe?

```bash
# SAFE: Network filtered, cannot exfiltrate data
paude create --yolo

# DANGEROUS: Full network access, can send files anywhere
paude create --yolo --allowed-domains all
```

The `--yolo` flag enables autonomous execution (no confirmation prompts). This is safe when network filtering is active because the agent cannot exfiltrate files or secrets even if it reads them.

**Do not combine `--yolo` with `--allowed-domains all`** unless you fully trust the task.

## Workspace Protection

The container has full read-write access to your working directory. **Your protection is git itself.** Push important work to a remote before running in autonomous mode:

```bash
git push origin main
```

If something goes wrong, recovery is a clone away.

## Residual Risks

These risks are accepted by design:

1. **Workspace destruction**: The agent can delete files including `.git`. Mitigation: push to remote before autonomous sessions.
2. **Secrets readable**: `.env` files in workspace are readable. Mitigation: network filtering prevents exfiltration; don't use `--allowed-domains all` with sensitive workspaces.
3. **No audit logging**: Commands executed aren't logged. This is a forensics gap, not a security breach vector.

## Unsupported devcontainer Properties (Security)

These properties are ignored for security reasons:
- `mounts` - paude controls mounts
- `runArgs` - paude controls run arguments
- `privileged` - never allowed
- `capAdd` - never allowed
- `forwardPorts` - paude controls networking
- `remoteUser` - paude controls user
