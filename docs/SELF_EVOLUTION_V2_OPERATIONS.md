# Self-Evolution v2 Operations and Recovery

This runbook operates AtomLearn's three bounded evolution planes: explicit learner personalization, evidence-based strategy experiments, and signed Core releases. None of them permit a course session to edit Core code or `SKILL.md`.

## Rollout state

The Core manifest keeps every v2 capability default-off:

| Capability | Default | Activation boundary | Immediate safe stop |
| --- | --- | --- | --- |
| Cross-course personalization | Off | Learner runs `profile enable` for one workspace | `profile disable`, optionally `--all` |
| Strategy experiments | Off | Separate `strategy enable-experiments`; every experiment starts shadow-only | `strategy pause` for one or all |
| Evolution Capsule export | Off | Local build, successful privacy lint, complete preview, then one confirmed export | Do not export; there is no submit/telemetry command |
| Release Manager | Off and external | Operator initializes a separate trust root and confirms a signed update plan | Keep current Core; run `update recover` for an unfinished transaction |

The rollout sequence is enforced by contracts and tests:

1. Effective Policy is always explainable before any global state exists.
2. Workspace-local behavior remains the default.
3. Global profiles require explicit workspace binding and do not import old history.
4. Inferred profile values need distinct-session corroboration.
5. Strategy candidates must record a replayable shadow exposure before live assignment.
6. Promotion requires comparable strata, delayed outcomes, learning-quality improvement, and guardrails.
7. Capsule export remains local and one-time after an independent attack-fixture lint.
8. The release channel accepts only an exact signed tag after the cross-platform matrix passes.

## Inspect before changing anything

Run these read-only checks first:

```powershell
atomlearn version
atomlearn migrate status
atomlearn profile status <WORKSPACE>
atomlearn policy explain <WORKSPACE> response.detail --context teaching
atomlearn strategy status
atomlearn-manager update status
```

`atomlearn version` exposes `feature_defaults`; all four values must be `false` for this release line. `policy explain` includes the selected value, provenance, ignored candidates, fingerprint, and protected invariants.

## Personalization incident response

If a stored preference is wrong but the learner wants to keep global personalization, retire only that dimension:

```powershell
atomlearn profile retire <WORKSPACE> response.detail --reason-code user_rejection --expected-profile-revision <REVISION>
```

To detach one course, use `profile disable`. To stop the global profile too, add `--all` and the expected profile revision. `profile reset --confirmed` disables the profile and retires active preferences while retaining audit tombstones; it does not erase history.

Current-turn instructions always override stored presentation choices without silently becoming durable. Personalization cannot alter mastery Evidence, prerequisites, source grounding, privacy, or the one-Active-Atom rule.

## Strategy incident response

Use `strategy explain <EXPERIMENT_ID>` before intervention. If quality or a guardrail regresses:

```powershell
atomlearn strategy pause <EXPERIMENT_ID> --expected-strategy-revision <REVISION>
atomlearn strategy validate
```

Omit the experiment ID to disable experiments and pause all non-terminal experiments. Pausing removes the policy overlay but never rewrites exposures, outcomes, or learning Evidence. Do not promote from speed, engagement, satisfaction, a single Atom, unmatched strata, or missing delayed review.

## Capsule privacy incident response

Capsules are enum-only and bucketed. They cannot contain raw messages, summaries, source content, locators, paths, URLs, DOI values, precise timestamps, e-mail addresses, stable user identifiers, Atom IDs, or small unique combinations.

If lint fails, do not work around the field allowlist. Correct the upstream classification or keep the finding local. An exported Capsule is only a local file; it is never an upload. Maintainer ingestion deduplicates by content fingerprint and can produce only a `needs_reproduction` fixture with automatic patching disabled.

## Manager failure recovery

The update journal covers `planned`, `downloaded`, `verified`, `state_copied`, `installed`, `health_checked`, `state_applied`, and `activated`. A fault-injection test interrupts and recovers each stage.

After an unexpected stop:

1. Do not start another update.
2. Run `atomlearn-manager update status`.
3. Run `atomlearn-manager update recover`.
4. Confirm `atomlearn-manager version` and `atomlearn-core version` agree.
5. Run `atomlearn-core migrate validate` against selected user/workspace state.

Recovery restores state only when its compare-before-write hash proves there has been no later learner write. It restores the pointer only when no later transaction moved it. Otherwise it reports `needs_manual_recovery` and preserves both sides for an operator decision.

Rollback is limited to the paired previous version and its matching pre-migration state:

```powershell
atomlearn-manager rollback <PREVIOUS_VERSION> --confirmed
```

Arbitrary downgrade and release purging are intentionally absent. See [Signed Release Manager](../atom-learn/references/RELEASE_MANAGER.md) for the full trust and archive boundary.

## Stable release procedure

Stable publication requires an exact `v<package-version>` tag. Configure the repository secret `ATOMLEARN_RELEASE_PRIVATE_KEY` with an Ed25519 PEM or raw base64 private key, and the repository variable `ATOMLEARN_RELEASE_KEY_ID` with the matching trusted key ID. Keep the private key outside source control and distribute the public trust root separately.

The tag workflow runs fast and integration suites on Windows and Linux with Python 3.10–3.13. It also runs property, migration, upgrade, fault-injection, privacy-attack, replay, backward-compatibility, CLI, documentation, and Skill/Core contract gates. Only after every matrix cell passes does it create the gate report, Manager wheel, deterministic Core ZIP, and immutable GitHub Release assets. The Manager wheel's identity and hash are covered by the same signed release manifest.

The workflow refuses branch publication. The release builder refuses tag/package/Core disagreement, a prerelease stable version, a mutable asset URL, a mismatched commit gate report, an existing output, or a missing signing identity.

## Verification inventory

- `tests/test_release_properties.py`: precedence, invariant, fingerprint, migration, invalid-input, SemVer, and default-off properties.
- `tests/test_manager.py`: hostile archives, signature/hash identity, copied-state health, eight-stage recovery, paired rollback, two prior-version upgrade paths, missing migration, migration failure, and disk failure.
- `tests/fixtures/security/capsule-attacks.json`: independent privacy attack corpus.
- `tests/fixtures/migrations/supported-upgrade-paths.yaml`: declared version-path and schema-edge inventory.
- `tests/fixtures/releases/gate-report-v0.13.0.json`: strict release-gate example.
- `.github/workflows/validate.yml`: ordinary cross-platform branch CI.
- `.github/workflows/release.yml`: tag-only signed publication.

## Known operational limits

- No production release is considered published until the signed tag workflow succeeds; source on `main` remains `development` channel.
- Trust bootstrap and signing-key rotation are explicit operator actions.
- Current published state schemas are all version 1, so the migration-edge inventory is empty; two earlier Core-version paths are still exercised end to end through copy, validation, activation, and launch.
- A manual-recovery result deliberately requires an operator because overwriting newer learning would be unsafe.
