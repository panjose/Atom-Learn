# Opt-in cross-course user profiles

Use a User Profile only when the learner explicitly wants stable presentation preferences to cross course boundaries. Workspace-local adaptation remains the default and continues to work without a user data directory.

## Enable and inspect

```text
atomlearn profile enable <workspace> [--profile default]
atomlearn profile status <workspace>
atomlearn profile show <workspace>
atomlearn policy effective <workspace> --context teaching
```

Enabling creates an enum-only profile in the platform user-data directory and a small `.atomlearn/profile-binding.yaml` in that workspace. It does not import prior workspace signals. A second workspace can bind the same profile explicitly.

## Record scope

Add `scope: user` to an `adapt observe-session` payload only after opt-in:

```yaml
session_id: opaque-session-7
context: teaching
scope: user
signals:
  - dimension: explanation.order
    value: example_first
    direction: prefer
    evidence: explicit
    reason_code: explicit_request
    confidence: 0.95
    turn_refs: [opaque-turn-3]
```

Use `scope: workspace` or omit `scope` for course-local adaptation. One observation belongs to exactly one scope, preventing double weighting. Explicit user-scope preferences activate immediately; behavioral and outcome signals still require distinct-session corroboration.

Promote an already active explicit workspace preference only after the learner confirms it should be global:

```text
atomlearn profile promote-preference <workspace> explanation.order --expected-profile-revision <revision>
```

Never auto-promote an inferred workspace preference.

## Control and recovery

```text
atomlearn profile disable <workspace>
atomlearn profile retire <workspace> <dimension> --reason-code user_rejection
atomlearn profile export <workspace> --output <new-file>
atomlearn profile reset <workspace> --confirmed
```

`disable` stops one workspace from reading the global profile. Add `--all` to disable the profile itself. `retire` and `reset` append tombstones instead of deleting history; reset disables the profile and retires every active dimension. Export refuses to overwrite an existing file.

The profile stores no message text, summary, course path, source content, sensitive trait, or workspace identifier. Use opaque session and turn IDs. All mutations use a profile revision and a workspace-binding revision independently from course and adaptation revisions.
