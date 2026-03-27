---
name: audit-docs
description: Audit all documentation under docs/ against the actual codebase and fix any inaccuracies
user_invocable: true
---

Review all of our documentation under docs/ and identify places where the documentation is outdated, incorrect, or generally doesn't match with how paude currently works. Every example, every command, every detail needs to match what the actual code does.

Split this work up into a few subagents across the doc files to explore them in parallel. For each doc file:

1. Read the entire doc file
2. For every specific claim made (config keys, default values, file paths, CLI flags, class names, method names, environment variables, commands, example output), search the codebase to verify it's accurate
3. Check if important features/options exist in the code but are missing from the docs

For each discrepancy found, report:
- The specific claim in the doc (quote it, with line number)
- What the code actually shows
- The relevant source file and line number

After gathering all findings, fix every confirmed issue by editing the doc files directly. Then run `make lint` and `make typecheck` to verify nothing is broken.
