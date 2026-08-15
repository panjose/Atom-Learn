# Core version and deterministic migrations

Treat the installed Core Skill as read-only during learning. User and workspace state may outlive one Core version, so inspect compatibility before an update instead of editing schema numbers manually.

## Inspect

```text
atomlearn version
atomlearn migrate status
atomlearn migrate plan --workspace <workspace>
atomlearn migrate validate --workspace <workspace>
```

`status` is read-only and does not create the user data directory. `plan` classifies each discovered namespace as `compatible`, `migrated`, `needs_review`, or `forbidden`. Phase 1 exposes planning and validation only; course runtime cannot apply migrations.

## Version rules

- Keep schema version independent from state revision.
- Use the Core manifest's namespace-specific `read` range and `write` version.
- Respect `min_reader_core_version`; an older Core must not write newer state.
- Advance a migration exactly one schema version per registered pure function.
- Reject a missing migration path instead of asking the model to rewrite state.
- Run migration on a recoverable copy, validate every namespace, and switch only after all checks pass.
- Keep old state available for release-manager recovery.

Use `ATOMLEARN_DATA_DIR` only with an explicit absolute path for tests or portable installations. Normal runs use the platform user-data directory through `platformdirs`. Merely checking status must not create that directory.

Every user-level mutation must hold the namespace lock, re-read the current revision, require the expected revision, and use an atomic replacement. A stale writer must fail without changing current state.
