# Native Installer Migration Feature

## Overview

Migrate paude's container from the deprecated npm installer to Claude Code's official native installer.

## The Problem

Claude Code's npm installer (`npm install -g @anthropic-ai/claude-code`) is **deprecated**. Anthropic now recommends the native installer, which provides:

- Automatic updates (outside containers)
- Bundled dependencies (ripgrep included)
- No Node.js runtime dependency
- Simpler installation paths

Continuing with npm means:
- Missing future improvements prioritized for native
- Larger container images (Node.js required)
- Potential compatibility issues as npm support winds down

## The Solution

Replace npm installation with native installer in the Dockerfile:

**Before (npm, deprecated)**:
```dockerfile
FROM node:22-slim
RUN npm install -g @anthropic-ai/claude-code
```

**After (native)**:
```dockerfile
FROM debian:bookworm-slim
USER paude
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/home/paude/.local/bin:$PATH"
ENV DISABLE_AUTOUPDATER=1
```

## Key Changes

| Aspect | npm (current) | Native (target) |
|--------|---------------|-----------------|
| Base image | `node:22-slim` (~200MB) | `debian:bookworm-slim` (~80MB) |
| Install method | `npm install -g` | `curl \| bash` |
| Binary location | `/usr/local/bin/claude` | `~/.local/bin/claude` |
| ripgrep | Separate apt install | Bundled |
| Node.js | Required | Not required |
| Auto-updates | N/A | Disabled (DISABLE_AUTOUPDATER=1) |

## Benefits

1. **Smaller image**: No Node.js runtime = smaller container
2. **Aligned with Anthropic**: Following official recommendations
3. **Future-proof**: Won't be affected by npm deprecation
4. **Bundled ripgrep**: One less dependency to manage

## Effort Estimate

**T-shirt size**: **Small (S)**

- Dockerfile changes: ~2 hours
- Testing and validation: ~3 hours
- Documentation: ~1 hour
- Total: ~1 day

## Risk Assessment

**Low risk**:
- Well-documented migration path from Anthropic
- Easy rollback to npm if issues arise
- Native installer is mature and widely used

## Priority

**Highest** - npm is deprecated; should migrate before potential breaking changes.

## Verification Checklist

After implementation:

- [ ] Container builds successfully
- [ ] `claude --version` works
- [ ] `claude` can respond to prompts
- [ ] Network filtering still works
- [ ] ripgrep available (bundled)
- [ ] Image size same or smaller
- [ ] All tests pass
- [ ] No auto-update delays on startup

## Files Changed

- `containers/paude/Dockerfile` - Main changes
- `README.md` - Documentation updates

## Documentation

- [RESEARCH.md](./RESEARCH.md) - Background research, alternatives considered
- [PLAN.md](./PLAN.md) - Design decisions and rationale
- [TASKS.md](./TASKS.md) - Detailed implementation tasks
