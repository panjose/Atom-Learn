---
name: atom-learn
description: Resolve and use the currently active signed AtomLearn Core for atomic source-grounded learning, research reading, exam preparation, RAG, knowledge maps, and persistent adaptation.
---

# AtomLearn Signed Bridge

1. Run `atomlearn-manager codex resolve --json`.
2. Require `ok: true`; never fall back to a repository checkout or an unverified copied Skill.
3. Read the returned absolute `skill_path` completely, then follow that signed Core Skill and its referenced resources.
4. If resolution fails, explain that the signed Core or bridge needs repair and stop before reading or changing course state.

This bridge stores no token, private key, workspace path, learner data, or version-specific teaching protocol.
