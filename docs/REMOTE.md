# Remote Hosts & Docker Backend

## Docker Backend

Paude supports Docker as an alternative to Podman for local container execution. Docker and Podman are interchangeable for all local features.

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

Run containers on a remote machine via SSH using the `--host` flag. This works with both `podman` and `docker` backends (not `openshift`).

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
2. Container images are built or pulled on the remote host
3. The container runs on the remote host with the same isolation and network filtering as local sessions
4. `paude connect` tunnels the session back to your terminal via SSH

### Limitations

- `--host` and `--ssh-key` are CLI-only flags (not stored in user defaults)
- Not compatible with `--backend=openshift` (use the [OpenShift backend](OPENSHIFT.md) for remote Kubernetes execution)

## Combining Remote Hosts with GPU

Remote hosts are commonly used for GPU-accelerated workloads. See the [GPU Passthrough](CONFIGURATION.md#gpu-passthrough) section in the configuration docs.

```bash
# All GPUs on a remote host
paude create my-project --gpu all --host user@gpu-box

# Specific GPUs on a remote host
paude create my-project --gpu=device=0,1 --host user@gpu-box
```
