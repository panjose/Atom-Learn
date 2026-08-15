# Signed Release Manager

The AtomLearn Release Manager is a separate, stable distribution. It installs signed Core releases side by side, validates state on copies, and changes one small active pointer only after the new Core passes health checks. Course sessions must never perform these operations.

## Security boundary

- `atomlearn` owns course behavior and course state. It has no update or rollback command.
- `atomlearn-manager` owns trust configuration, release directories, transaction journals, and the active pointer.
- Only Ed25519-signed manifests from an explicitly trusted repository and key are accepted.
- Stable artifacts must use an exact immutable GitHub tagged-release URL. Branch archives, HTTP, decorated URLs, prerelease versions on the stable channel, and cross-repository assets are rejected.
- The artifact filename, byte size, SHA-256, normalized content-tree hash, embedded Core manifest, package version, schema declarations, commit, tag, and CI gate report must agree.
- New artifact code is not executed until signature, archive structure, hashes, and embedded identities have been verified.

The manager root must be isolated from both the AtomLearn user-data root and every course workspace. Trust initialization never overwrites an existing trust root.

## Install the independent distribution

From a trusted repository checkout:

```powershell
python -m pip install -e ./manager
atomlearn-manager --help
atomlearn-release --help
atomlearn-core --help
```

Production bootstrap should install a reviewed manager build independently from the Core artifact it manages. Keep the release private key outside the repository and manager host. Distribute only the raw base64 Ed25519 public key through a trusted channel.

Initialize once:

```powershell
atomlearn-manager init --key-id release-2026 --public-key <BASE64_ED25519_PUBLIC_KEY> --repository panjose/Atom-Learn
atomlearn-manager version
```

Use `--manager-root <absolute-path>` before the subcommand when an explicit isolated root is needed. Otherwise the manager uses the platform-specific `AtomLearnManager` user-data directory.

## Update workflow

Use an immutable signed release-manifest URL or a previously downloaded local manifest:

```powershell
atomlearn-manager update check --manifest <MANIFEST_PATH_OR_HTTPS_URL> --channel stable
atomlearn-manager update plan 0.14.0 --manifest <MANIFEST_PATH_OR_HTTPS_URL> --artifact <LOCAL_ZIP> --data-dir <ABSOLUTE_USER_DATA> --workspace <ABSOLUTE_COURSE>
atomlearn-manager update apply 0.14.0 --manifest <MANIFEST_PATH_OR_HTTPS_URL> --artifact <LOCAL_ZIP> --data-dir <ABSOLUTE_USER_DATA> --workspace <ABSOLUTE_COURSE> --confirmed
atomlearn-manager update status
```

Run the selected Core through the stable dispatcher:

```powershell
atomlearn-core version
atomlearn-core status <ABSOLUTE_COURSE> --json
atomlearn-core -- --help
```

Before every dispatch, `atomlearn-core` validates the active-pointer schema, signed release manifest, trusted repository/key, manifest hash, installed content-tree hash, and Core entry point. It then launches that exact read-only Core with bytecode writes disabled. `--manager-root` is a launcher option; use `--` before Core options when necessary.

Repeat `--workspace` for multiple courses. Omitting `--artifact` downloads the exact signed artifact URL. `plan` is read-only: it verifies a supplied artifact, calculates disk requirements, and reports compatible, migratable, review-required, or forbidden state documents. Review the plan before using `--confirmed`.

Apply performs these journaled stages:

1. Download to same-root staging with a bounded signed size.
2. Verify the hostile-archive boundary and all signed identities.
3. Copy user profiles, strategy state, and complete `.atomlearn` workspace state.
4. Run only registered manager migration functions on those copies.
5. Install the verified Core in a new, read-only version directory.
6. Run version/help/migration validation and real workspace `validate`/`status` checks against copied state.
7. Apply migrated state with compare-before-write guards.
8. Atomically switch `active.yaml` and commit the transaction journal.

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

`update check --offline` performs no network request and reports the current Core as still usable. A manifest fetch failure is also reported as offline without changing active state. Apply fails closed on a truncated download, insufficient disk, unsafe ZIP member, bad signature/hash, missing migration, failed Core health check, state race, or pointer race.

Network redirects must remain HTTPS. Archive extraction rejects traversal, absolute or Windows-unsafe names, reparse/symlink entries, duplicate and case-colliding names, file/directory prefix collisions, encryption, excessive size, and suspicious compression ratios. Extraction is manual; `extractall` is never used.

## Building a release

`atomlearn-release` is a maintainer command. It requires an exact tag/commit gate report, a stable or prerelease channel, and an Ed25519 private key. It creates a deterministic ZIP and a signed manifest without overwriting existing outputs.

Stable publication is allowed only after the cross-platform release gates described in [Self-Evolution v2 Implementation Plan](../../docs/SELF_EVOLUTION_V2_IMPLEMENTATION_PLAN.md) pass. Building an artifact does not publish it.

## Invariants

- The course runtime cannot write manager state or Core installation files.
- The manager never migrates live state before copied-state validation succeeds.
- Artifact code never runs before cryptographic and structural verification.
- Activation is one atomic pointer replacement, not an in-place Core overwrite.
- The stable launcher resolves and verifies the active pointer on every invocation.
- At least the paired previous Core and state snapshot remain recoverable.
- Recovery refuses to overwrite learning created after the failed transaction.
