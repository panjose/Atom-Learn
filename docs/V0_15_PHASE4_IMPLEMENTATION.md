# v0.15 Phase 4 Implementation Record

## Scope

Phase 4 implements Workstream D from `V0_15_PRODUCT_READINESS_REMEDIATION_DESIGN.md`: one stable signed bootstrap, an explicitly separate unmanaged developer path, root-bound Codex bridge resolution, and conservative source-copy migration with recovery.

## Stable onboarding contract

`atomlearn-manager bootstrap plan|apply|status|recover` composes the existing trust, signed Core, immutable runtime-profile, bridge, doctor, and transaction boundaries without weakening them.

`plan` is read-only. It does not create the Manager root, Codex home, or Skill directory. It reports:

- the proposed or existing trust level and every active fingerprint;
- the signed Core version, channel, platform, architecture, Python ABI, and selected profile;
- local artifact/runtime verification and disk-space results;
- whether Core will be installed, updated, reused, refused as a downgrade, or changed to another signed profile;
- the existing Codex Skill ownership classification;
- every external write location and the explicit remediation for each blocker.

`apply` requires `--confirmed` and repeats the full plan with the same inputs before writing. It initializes or pins the trust root, applies the signed Core/profile transaction, installs or migrates the bridge, verifies its exact inventory, and runs capability doctor. Every sub-operation is independently idempotent or journaled, so a repeated apply converges on the same state. Experimental profiles require `--allow-experimental` in both bootstrap and ordinary Core update paths.

The Manager package carries a byte-identical convenience copy of `release/atomlearn-trust-bundle.json`; the release gate fails if the copies diverge. This does not replace independent fingerprint verification.

## Stable and developer paths

The stable path installs a reviewed Manager distribution independently, then uses bootstrap to obtain the signed Core/runtime and fixed bridge. The developer path uses editable repository packages and a direct/copied/linked source Skill in a separate development Codex home. It is explicitly unmanaged. The two paths never share ownership markers or silently replace one another.

## Root-bound exact bridge

Bridge schema v2 records the absolute Manager root selected at installation. The packaged resolver reads only that marker and invokes `atomlearn-manager --manager-root <bound-root> codex resolve --json` with an argument list, not a shell string. A custom-root installation therefore cannot accidentally resolve the platform-default Manager state.

Bridge verification requires exactly `SKILL.md`, `agents/openai.yaml`, `scripts/resolve.py`, and the ownership marker. Changed, missing, linked, or additional files make the owned bridge repairable but invalid. Reinstalling a valid bridge returns an idempotent result and creates no redundant previous copy. Repair still requires confirmation and retains the replaced owned tree.

## Conservative source-copy migration

Manager reads an existing unowned Skill without following links, rejects case collisions and unsafe entries, and hashes its complete regular-file tree. It calls a tree official only when the identity exactly matches either:

- the `atom-learn/` tree of an installed release whose manifest signature and installed content have both been reverified; or
- during bootstrap planning, the `atom-learn/` tree of the explicitly supplied local signed Core artifact.

The classifications are `absent`, `owned_bridge`, `owned_bridge_needs_repair`, `official_source_copy`, `unknown_or_modified_source_copy`, `unsafe_source_copy`, and `unsafe_linked_path`. Only `official_source_copy` is migratable. Unknown, modified, linked, nested-linked, case-colliding, and unsafe trees remain untouched.

Confirmed migration creates a `bmtxn-*` journal, atomically renames the exact official tree to `atom-learn.source-backup-<timestamp>-<transaction>`, installs the root-bound bridge, verifies it, and commits. The original source backup remains after success. Recovery handles process interruption after backup or bridge installation: any partial bridge is retained separately, the exact original fingerprint is restored, and ambiguous preservation paths fail closed for manual recovery. No migration path deletes content.

## Interfaces

```text
atomlearn-manager bootstrap plan <version> --expected-fingerprint <sha256> [asset/profile/state options]
atomlearn-manager bootstrap apply <version> --expected-fingerprint <sha256> [same options] --confirmed
atomlearn-manager bootstrap status [--codex-home <absolute-path>]
atomlearn-manager bootstrap recover
atomlearn-manager codex migrate plan [--codex-home <absolute-path>]
atomlearn-manager codex migrate apply [--codex-home <absolute-path>] --confirmed
atomlearn-manager codex migrate recover
atomlearn-manager trust pin --fingerprint <sha256> --confirmed
```

## Compatibility and recovery

Existing valid Manager-owned bridges are reused. Older owned bridge markers are classified as repairable because they lack an explicit Manager-root binding. Existing exact official source copies require preview and confirmation; they are never automatically claimed. Existing unknown or locally modified Skills remain foreign. Core `txn-*`, profile `ptxn-*`, and bridge `bmtxn-*` recovery remain separate, while `bootstrap recover` runs all three conservative recovery handlers.

## Verification

The Phase 4 tests cover read-only fresh planning, fingerprint display, first apply, repeated apply without duplicate bridge copies, Manager-root/Skill-path isolation, manifest-v2 and bridge-protocol enforcement, root-bound marker identity and resolver arguments, exact bridge inventory, packaged trust identity, exact official source recognition, modified and linked refusal, timestamped retained backups, interruption after bridge installation, exact source restoration, and successful retry.

Final verification on Windows/Python 3.12:

- split full suite: Core/non-Manager **185 passed, 1 skipped** in 8:54; Manager integration **17 passed** in 12:59; Manager contracts **18 passed** in 0:04, for **220 passed, 1 skipped** combined. The skip is the existing optional real-HNSW case because the local `scale` extra is absent;
- both the complete AtomLearn Skill and packaged bridge Skill passed Skill Creator `quick_validate.py`;
- release `validate-skill`, Python compileall, all **53** JSON Schemas, and bilingual documentation tests passed;
- English and Chinese READMEs remain aligned at **353 lines**, **22 heading lines**, and **18 code blocks** each;
- Core and Manager wheel/sdist builds passed, and the Manager wheel/sdist both contain `bootstrap.py`, the bridge resolver, bridge-migration schema, and packaged trust bundle.
