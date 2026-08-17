# Signed Release Manager

The AtomLearn Release Manager is a separate, stable distribution. It installs signed Core releases side by side, validates state on copies, and changes one small active pointer only after the new Core passes health checks. Course sessions must never perform these operations.

## Security boundary

- `atomlearn` owns course behavior and course state. It has no update or rollback command.
- `atomlearn-manager` owns trust configuration, release directories, transaction journals, and the active pointer.
- Only Ed25519-signed manifests from an explicitly trusted repository and key are accepted.
- Stable artifacts must use an exact immutable GitHub tagged-release URL. Branch archives, HTTP, decorated URLs, prerelease versions on the stable channel, and cross-repository assets are rejected.
- Manifest v2 binds the artifact filename, byte size, SHA-256, normalized content-tree hash, embedded Core manifest, package version, schema declarations, commit, tag, CI gate report, Skill entry-point hash/protocol, capability ledger, smoke fixtures, trust-bundle version, and target-specific runtime recipe.
- New artifact code is not executed until signature, archive structure, hashes, and embedded identities have been verified.
- A runtime bundle contains the complete wheelhouse and canonical recipe for its declared runtime profile, never a copied virtual environment. Manager installs it with `pip --no-index` into a release-specific environment. The `v0.14.2` stable line declares only `base`; source extras such as `ocr`, `scale`, and `semantic` are not silently implied by that bundle.

The manager root must be isolated from both the AtomLearn user-data root and every course workspace. Trust initialization never overwrites an existing trust root.

## Install the independent distribution

From a trusted repository checkout:

```powershell
python -m pip install -e ./manager
atomlearn-manager --help
atomlearn-release --help
atomlearn-core --help
```

Production bootstrap should install a reviewed manager build independently from the Core artifact it manages. Keep the release private key outside the repository and manager host. The repository publishes a convenience trust bundle, but strong trust requires comparing its fingerprint through an independent channel.

Initialize once:

```powershell
atomlearn-manager init --trust-bundle release/atomlearn-trust-bundle.json --expected-fingerprint sha256:19e079c2aece68bae50eac9af779e3e0bb74e04edebaf43a2ad3d08e71dbb222
atomlearn-manager trust inspect
atomlearn-manager codex install
atomlearn-manager codex status
atomlearn-manager version
```

Use `--manager-root <absolute-path>` before the subcommand when an explicit isolated root is needed. Otherwise the manager uses the platform-specific `AtomLearnManager` user-data directory.

Direct `--key-id`/`--public-key` pinning remains available. Bundle initialization without `--expected-fingerprint` is explicitly recorded as `unverified`, not silently described as pinned trust. If an operator deliberately accepts the displayed first-seen fingerprint, `trust accept-tofu --fingerprint <SHA256> --confirmed` records the weaker `verified_tofu` level. Trust state therefore distinguishes `pinned`, `verified_tofu`, and `unverified`; signed release manifests cannot introduce new keys. A normal rotation must increment `bundle_version`, name the previous bundle, and carry a valid signature from a currently non-revoked key:

```powershell
atomlearn-manager trust rotate --bundle <NEXT_SIGNED_TRUST_BUNDLE> --confirmed
```

Revocation or account-compromise recovery is a separate operator procedure. Obtain a higher-version replacement bundle and its new active-key fingerprint through an independent channel, then run `trust break-glass --bundle <LOCAL_BUNDLE> --expected-fingerprint <NEW_SHA256> --confirmed`. This path refuses the current active fingerprint and records the replacement as pinned. Downloading a replacement key and fingerprint only from the same compromised release channel is not a trust recovery.

## Stable Codex bridge

The installed `atom-learn` Codex Skill is a small Manager-owned bridge, not a second mutable copy of the teaching protocol. It asks Manager to resolve the active signed Core, then returns the exact `SKILL.md` path, Core version, protocol version, hash, and manifest identity. Install never overwrites a foreign Skill. Repair replaces only an owned bridge and retains the previous copy:

```powershell
atomlearn-manager codex resolve --json
atomlearn-manager codex repair --confirmed
```

## Update workflow

Use an immutable signed release-manifest URL or a previously downloaded local manifest:

```powershell
atomlearn-manager update check --manifest <MANIFEST_PATH_OR_HTTPS_URL> --channel stable
atomlearn-manager update plan 0.14.2 --manifest <MANIFEST_PATH_OR_HTTPS_URL> --artifact <LOCAL_ZIP> --runtime-bundle <LOCAL_RUNTIME_ZIP> --data-dir <ABSOLUTE_USER_DATA> --workspace <ABSOLUTE_COURSE>
atomlearn-manager update apply 0.14.2 --manifest <MANIFEST_PATH_OR_HTTPS_URL> --artifact <LOCAL_ZIP> --runtime-bundle <LOCAL_RUNTIME_ZIP> --data-dir <ABSOLUTE_USER_DATA> --workspace <ABSOLUTE_COURSE> --confirmed
atomlearn-manager update status
```

Run the selected Core through the stable dispatcher:

```powershell
atomlearn-core version
atomlearn-core status <ABSOLUTE_COURSE> --json
atomlearn-core -- --help
```

Before every dispatch, `atomlearn-core` validates the active-pointer schema, signed release manifest, trusted repository/key, manifest hash, installed content-tree hash, runtime identity, and Core entry point. Manifest v2 releases are always launched as `active-runtime-python -m atomlearn`, never with Manager's own Python. `--manager-root` is a launcher option; use `--` before Core options when necessary.

Repeat `--workspace` for multiple courses. Omitting `--artifact` or `--runtime-bundle` downloads the exact URL bound into the signed manifest. `plan` is read-only: it verifies supplied assets, selects exactly one OS/architecture/Python runtime, calculates disk requirements, and reports compatible, migratable, review-required, or forbidden state documents. Review the plan before using `--confirmed`.

Apply performs these journaled stages:

1. Download to same-root staging with a bounded signed size.
2. Verify the hostile-archive boundary and all signed identities.
3. Copy user profiles, strategy state, and complete `.atomlearn` workspace state.
4. Run only registered manager migration functions on those copies.
5. Install the verified Core in a new, read-only version directory.
6. Verify the signed runtime bundle, materialize its wheelhouse, and install the Core and locked dependencies offline into a new runtime.
7. Run version/help/migration validation and real workspace `validate`/`status` checks against copied state.
8. Run capability-declared TXT/HTML/PDF/DOCX extraction, RAG, exam, and research smoke paths using repository-owned fixtures.
9. Apply migrated state with compare-before-write guards.
10. Atomically switch `active.yaml`, resolve the bridge against the signed Skill, and commit the transaction journal.

The old Core stays installed. An existing version directory is reused only when its signed manifest and content-tree hash match exactly.

## Recovery and rollback

Inspect first:

```powershell
atomlearn-manager update status
atomlearn-manager update recover
atomlearn-manager rollback 0.13.0 --confirmed
```

`recover` handles the latest unfinished transaction. It restores state only if no later writer changed the migrated file, and restores the previous active pointer only if no later transaction moved it. If either guard fails, status becomes `needs_manual_recovery` instead of overwriting newer learning.

Rollback is deliberately paired: only the previous version named by the active pointer can be selected, and it uses that update transaction's matching pre-migration state. Arbitrary downgrade is forbidden because an older Core may not be able to read newer schemas.

If a process stops after activation, do not start another update. Run `update status`, then `update recover`. The current or previous version directories are retained; the manager has no purge command.

## Offline and failure behavior

`update check --offline` performs no network request and reports the current Core as still usable. A real manifest request failure exits with a JSON error envelope containing stable `code`, `retryable`, and bounded `details` fields; HTTP 401/403/404 and offline transport failures never reach an internal assertion and never change active state. Apply also fails closed on a truncated download, insufficient disk, unsafe ZIP member, bad signature/hash, missing migration, failed Core health check, state race, or pointer race.

Network redirects must remain HTTPS. Archive extraction rejects traversal, absolute or Windows-unsafe names, reparse/symlink entries, duplicate and case-colliding names, file/directory prefix collisions, encryption, excessive size, and suspicious compression ratios. Extraction is manual; `extractall` is never used.

## Public and private GitHub Releases

Public assets are fetched without a credential. On GitHub 401/403/404, Manager may retry an immutable `github.com/<owner>/<repo>/releases/download/<tag>/<asset>` through the GitHub Releases API. Credentials are read only from `ATOMLEARN_GITHUB_TOKEN`, `GH_TOKEN`, or `gh auth token`; they are never accepted in a manifest URL or CLI option and are never persisted. Authorization is limited to `github.com` and `api.github.com` and is stripped on cross-host redirects. Errors report only a provider label, host, and HTTP class.

## Building a release

`atomlearn-release` is a maintainer command. It requires the exact canonical JSON bytes emitted by `release/gate.py` for the tag/commit, a stable or prerelease channel, an Ed25519 private key, and the local public trust bundle. The builder refuses a key ID, public key, fingerprint, status, bundle version, or repository mismatch, so a release cannot be signed with a key that users have no declared path to trust. The same gate-report bytes are uploaded and embedded in the ZIP. It creates a deterministic ZIP and a signed manifest without overwriting existing outputs.

The builder also requires the already-built universal `atomlearn-manager` wheel and one deterministic runtime bundle for every stable matrix coordinate. Each CI coordinate builds the Core wheel, downloads the complete dependency set for the declared `base` profile, and emits a canonical runtime ZIP. Optional source extras are not stable delivery merely because their code has a separate CI job. The publish job refuses a partial Windows/Linux Python 3.10-3.13 amd64 matrix. Manager identity, runtime recipes, asset hashes, and capability-smoke identities are included in the same Ed25519-signed manifest.

Stable publication is allowed only after the cross-platform release gates described in [Self-Evolution v2 Implementation Plan](../../docs/SELF_EVOLUTION_V2_IMPLEMENTATION_PLAN.md) pass. Building an artifact does not publish it.

## Invariants

- The course runtime cannot write manager state or Core installation files.
- The manager never migrates live state before copied-state validation succeeds.
- Artifact code never runs before cryptographic and structural verification.
- Activation is one atomic pointer replacement, not an in-place Core overwrite.
- The stable launcher resolves and verifies the active pointer on every invocation.
- A manifest v2 launcher uses only the selected release runtime; it never inherits Manager's dependency environment.
- The fixed Codex bridge resolves only the exact signed active `SKILL.md` and stores no learner data or credential.
- At least the paired previous Core and state snapshot remain recoverable.
- Recovery refuses to overwrite learning created after the failed transaction.
