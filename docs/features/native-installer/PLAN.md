# Native Installer Migration - Design Plan

## Goal

Migrate paude's container from the deprecated npm installer to Claude Code's native installer, ensuring:

- Alignment with Anthropic's recommended installation method
- Smaller container image (no Node.js runtime dependency)
- Future-proof against npm package deprecation
- Maintained security and isolation properties

## Design Decisions

### 1. Use Official Installer Script

**Decision**: Use `curl -fsSL https://claude.ai/install.sh | bash` in Dockerfile.

**Rationale**:
- Official, supported installation method
- Handles architecture detection automatically
- Includes all bundled dependencies (ripgrep)
- Non-interactive, suitable for container builds

**Alternative considered**: Direct binary download. Rejected because:
- Binary URLs may change without notice
- Would need to replicate installer logic
- May miss bundled components

### 2. Switch Base Image

**Decision**: Change from `node:20-slim` to `debian:bookworm-slim`.

**Rationale**:
- Node.js no longer required for Claude Code
- Smaller base image
- More control over installed packages
- Consistent with typical container best practices

**Size comparison (estimated)**:
- `node:20-slim`: ~200MB
- `debian:bookworm-slim`: ~80MB

### 3. Disable Auto-Updates

**Decision**: Set `DISABLE_AUTOUPDATER=1` environment variable.

**Rationale**:
- Container is ephemeral; auto-updates waste startup time
- Version controlled by image rebuild
- Predictable behavior across sessions
- Avoids network requests during startup

### 4. Installation as Non-Root User

**Decision**: Run installer as `paude` user, install to `/home/paude/.local/bin`.

**Rationale**:
- Container runs as non-root (security)
- Native installer installs to user's `~/.local/bin`
- Consistent with native installer design

**Implementation**:
```dockerfile
USER paude
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/home/paude/.local/bin:$PATH"
```

### 5. Preserve Existing entrypoint.sh

**Decision**: Minimal changes to entrypoint.sh; it should continue to work.

**Rationale**:
- entrypoint.sh handles config copying, environment setup
- Only change needed: ensure claude binary is in PATH
- Keep separation of concerns

### 6. Remove ripgrep Installation

**Decision**: Remove explicit `apt-get install ripgrep` as it's bundled.

**Rationale**:
- Native installer bundles ripgrep
- Reduces package dependencies
- One less thing to keep updated

**Verification needed**: Confirm ripgrep is actually bundled and works.

## Security Considerations

### Installer Script Trust

The native installer downloads and executes code from Anthropic. This is:
- **Same trust level** as current npm installation
- Standard practice for developer tools
- Executed at build time, not runtime

Mitigation:
- Build images in trusted environments
- Pin Dockerfile to specific base image versions
- Consider checksum verification if Anthropic provides one

### Network Access During Build

The installer requires network access to download Claude Code:
- Same as current npm install
- Acceptable for container builds
- For air-gapped environments, consider pre-built images

### Binary Verification

**Open question**: Does the installer verify binary checksums?

If not, consider:
- Building images in secure, audited pipelines
- Using signed/verified base images
- Documenting the trust model

## User Experience

### For Image Builders

**Before (npm)**:
```dockerfile
FROM node:20-slim
RUN npm install -g @anthropic-ai/claude-code@latest
```

**After (native)**:
```dockerfile
FROM debian:bookworm-slim
USER paude
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/home/paude/.local/bin:$PATH"
```

No change to how `paude` CLI is used.

### For End Users

**Transparent**: Users run `paude` the same way. The only difference:
- Potentially faster container startup (smaller image)
- `claude --version` output may differ slightly

## Implementation Phases

### Phase 1: Dockerfile Migration

1. Update base image to `debian:bookworm-slim`
2. Add required system dependencies
3. Replace npm install with native installer
4. Update PATH environment variable
5. Add DISABLE_AUTOUPDATER=1
6. Remove ripgrep from apt-get install

### Phase 2: Testing and Validation

1. Build new image successfully
2. Verify claude binary works
3. Verify ripgrep works
4. Run existing test suite
5. Manual testing of common workflows
6. Compare image sizes

### Phase 3: Documentation Updates

1. Update README with new image details
2. Update CLAUDE.md if needed
3. Document version update process (rebuild image)

### Phase 4: Legacy Bash Script (if still maintained)

1. Update bash script to handle new binary location
2. Or deprecate bash script in favor of Python CLI

## Rollback Plan

If issues arise:
1. Revert to npm-based Dockerfile
2. Pin to last known working npm version
3. Document issues encountered

npm package will continue to work for the foreseeable future, just without new features.

## Timeline Considerations

**Priority**: High - npm installer is deprecated, should migrate soon.

**Effort estimate**: Small (S)
- Dockerfile changes are straightforward
- Most work is testing and validation
- No Python code changes expected

**Risk**: Low
- Well-documented migration path
- Easy rollback if issues
- Native installer is stable

## Success Criteria

- [ ] Container builds successfully with native installer
- [ ] `claude --version` works inside container
- [ ] `claude` can complete a simple task (e.g., help, version)
- [ ] All existing tests pass
- [ ] Image size is equal or smaller than before
- [ ] `paude` CLI works end-to-end
- [ ] No startup delays from auto-updater
