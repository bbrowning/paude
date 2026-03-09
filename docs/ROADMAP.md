# Paude Roadmap

## Vision

Using `paude` in a repository feels just like using `claude`, but:
- **Secure by default** - Network filtering, credential protection, config isolation
- **Flexible execution** - Local container or remote Kubernetes/OpenShift
- **Zero friction** - One-time setup, then seamless daily use

---

## Current State (v0.9.0)

- Python implementation with Typer CLI
- Podman and OpenShift backends with shared Backend protocol
- Squid proxy for network filtering with configurable allowlists (`--allowed-domains`)
- Vertex AI authentication via gcloud ADC
- devcontainer.json/paude.json configuration support
- Session management: `create`, `start`, `stop`, `connect`, `delete`, `list`
- Git-based code sync with `--git` flag and `paude remote`
- Orchestration workflow: `status`, `harvest`, `reset`
- GitHub CLI (`gh`) installed with GitHub domains in default allowlist
- Network filtering via NetworkPolicy (OpenShift) and squid proxy (both backends)

---

## Completed Milestones

- **Python rewrite** — Replaced shell scripts with typed Python CLI
- **BYOC (Bring Your Own Container)** — devcontainer.json and paude.json support for custom environments
- **Native installer** — Dev container features installed natively via `claude_layer.py`
- **OpenShift backend** — StatefulSets, PVCs, credential sync, Binary Build image pipeline
- **Session management** — Full lifecycle (create/start/stop/connect/delete/list) across both backends
- **Network filtering** — Configurable domain allowlists, blocked-domain diagnostics, live domain management
- **Git-based sync** — `--git` flag for one-step code push, `paude remote` for manual setup
- **Orchestration workflow** — `paude status`, `paude harvest`, `paude reset` for fire-and-forget usage

---

## Open Items

### Claude Config Isolation

Project `.claude/` changes currently persist to the host. Need to shadow or isolate project-level Claude config to prevent a malicious project from modifying trust settings.

### CLI Refactoring (REFACTOR-002)

`cli.py` is ~1,900 lines and needs splitting into a `cli/` package with per-command modules.

---

## Future Ideas

Unprioritized ideas for future exploration: OpenTelemetry integration for usage metrics, additional cloud provider support (AWS Bedrock, Azure OpenAI, Anthropic Direct API), audit logging, Docker backend, plugin isolation, and IDE integration documentation.

---

## Not Planned

Things we're explicitly NOT doing:

1. **Building our own LLM runtime** - Use Claude as provided
2. **IDE-first approach** - CLI is primary interface
3. **Windows support** - Focus on Linux/macOS (WSL works)
4. **Docker Compose orchestration** - Keep it simple
5. **Multi-container workloads** - One container per session
6. **Custom model hosting** - Vertex AI / Bedrock / Direct only
