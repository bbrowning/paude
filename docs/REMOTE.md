# Remote Hosts & Docker Backend

## Docker Backend

Paude supports Docker as an alternative to Podman for local container execution. Docker and Podman are interchangeable for all local features, with one internal difference: Podman injects proxy credentials (API keys, `GH_TOKEN`) via Podman secrets, which are hidden from `podman inspect`. Docker has no non-Swarm secrets support, so the same credentials fall back to plain environment variables on the proxy sidecar, visible via `docker inspect`. Either way, credentials go only to the proxy sidecar, never the agent's own container.

```bash
paude create my-project --backend=docker
```

Set Docker as your default backend in `~/.config/paude/defaults.json`:

```json
{
  "defaults": {
    "backend": "docker"
  }
}
```

## Remote Host Execution

Run containers on a remote machine via SSH using the `--host` flag. This works with both `podman` and `docker` backends.

```bash
# Basic usage
paude create my-project --host user@gpu-box

# With Docker on the remote host
paude create my-project --backend=docker --host user@gpu-box

# With explicit SSH key
paude create my-project --host user@hostname --ssh-key ~/.ssh/id_ed25519

# With custom SSH port
paude create my-project --host user@hostname:2222
```

### Requirements

- SSH key-based authentication to the remote host
- Podman or Docker installed on the remote host
- The remote host must be able to pull container images

### How It Works

1. Paude validates SSH connectivity and that the container engine is available on the remote host
2. Paude detects the remote host's CPU architecture (via `uname -m` over SSH) so images are built/pulled for that architecture rather than your local machine's — use `--platform` to override if detection is unavailable or wrong
3. Container images are built or pulled on the remote host
4. The container runs on the remote host with the same isolation and network filtering as local sessions
5. `paude connect` tunnels the session back to your terminal via SSH

File copies with `paude cp` use the same SSH connection. A session path is
resolved inside the remote container while the local path is resolved on the
machine where `paude` is invoked:

```bash
paude cp user@gpu-box-session:/pvc/workspace/output.log ./output.log
paude cp ./input.txt user@gpu-box-session:/pvc/workspace/input.txt
```

Paude stages the transfer briefly on the remote host and removes the staging
directory after the copy completes.

### Limitations

- `--host` and `--ssh-key` are CLI-only flags (not stored in user defaults)

## Combining Remote Hosts with GPU

Remote hosts are commonly used for GPU-accelerated workloads. See the [GPU Passthrough](CONFIGURATION.md#gpu-passthrough) section in the configuration docs.

```bash
# All GPUs on a remote host
paude create my-project --gpu all --host user@gpu-box

# Specific GPUs on a remote host
paude create my-project --gpu=device=0,1 --host user@gpu-box
```
