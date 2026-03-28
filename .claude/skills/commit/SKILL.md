---
name: commit
description: >
  Write git commit messages using the Sonnet model.
  TRIGGER when: user asks to commit, make a commit, commit changes, commit this,
  create a commit, save changes, or similar commit-related requests.
  DO NOT TRIGGER when: user is asking about git history, viewing diffs, or
  discussing commits without requesting one be created.
user_invocable: false
---

Spawn an Agent with `model: "sonnet"` to handle this commit. Pass along the user's request as-is. Additional instructions for the agent: the Co-Authored-By line must reflect the actual Sonnet model writing the commit (e.g. `Claude Sonnet 4.6 <noreply@anthropic.com>`), not the parent Opus model. Wrap the commit message body at 72 characters per line.

After the agent completes, relay the result back to the user.
