# Paude

A Podman wrapper that runs [Claude Code](https://claude.ai/code) inside a container for isolated, secure usage with Google Vertex AI authentication.

## Features

- Runs Claude Code in an isolated container
- Authenticates via Google Vertex AI (gcloud Application Default Credentials)
- Read-write access to current working directory only
- Git read operations work (clone, pull, local commits) - push blocked by design
- Persists Claude Code settings between sessions

## Requirements

- [Podman](https://podman.io/getting-started/installation) installed
- Google Cloud SDK configured (`gcloud auth application-default login`)
- Vertex AI environment variables set:
  ```bash
  export CLAUDE_CODE_USE_VERTEX=1
  export ANTHROPIC_VERTEX_PROJECT_ID=your-project-id
  export GOOGLE_CLOUD_PROJECT=your-project-id
  ```

## Usage

```bash
# Run Claude Code interactively in current directory
./paude

# Pass arguments to Claude Code
./paude --help
./paude -p "explain this code"
```

The container image is built automatically on first run.

## macOS Setup

On macOS, Podman runs in a Linux VM that only mounts `/Users` by default. If your working directory is outside `/Users` (e.g., on a separate volume), you need to configure the Podman machine:

```bash
podman machine stop
podman machine rm
podman machine init \
  --volume /Users:/Users \
  --volume /private:/private \
  --volume /var/folders:/var/folders \
  --volume /Volumes/YourVolume:/Volumes/YourVolume
podman machine start
```

## Security Model

The container intentionally restricts certain operations:

| Resource | Access | Purpose |
|----------|--------|---------|
| Current directory | read-write | Working files |
| `~/.config/gcloud` | read-only | Vertex AI auth |
| `~/.claude` | read-write | Claude Code config |
| `~/.gitconfig` | read-only | Git identity |
| SSH keys | not mounted | Prevents git push via SSH |
| GitHub CLI config | not mounted | Prevents gh operations |

## License

MIT
