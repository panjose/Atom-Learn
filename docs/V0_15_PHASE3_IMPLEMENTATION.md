# v0.15 Phase 3 Implementation Record

## Scope

Phase 3 implements Workstream C from `V0_15_PRODUCT_READINESS_REMEDIATION_DESIGN.md`: signed immutable runtime profiles, side-by-side profile activation and rollback, capability doctor, model/OCR supply-chain preflight, and target-profile release gates.

## Runtime profile identity

Runtime recipe schema v2 binds:

- Core version, OS, architecture, and Python minor ABI;
- one finite profile contract and capability set;
- the complete wheel inventory and canonical dependency-lock hash;
- an optional explicit model lock;
- native-engine requirements;
- a canonical target-platform smoke report and its hash.

The profile hash commits to the profile contract, dependency lock, model lock, and smoke identity. Manifest v2 signs the full recipe and profile identity, bundle hash and size, immutable release URL, Core wheel, and the runtime-profile registry hash.

Profiles install at `runtimes/<core>/<platform>/<hash-prefix>/`. The short directory label keeps deep Windows virtual-environment paths usable. The installed state and every verifier retain and compare the full SHA-256; a prefix collision is rejected rather than reused.

## Finite profiles and truthful delivery

`runtime-profiles.yaml` defines `base`, `scale`, `semantic-cpu`, `ocr`, and experimental `semantic-gpu`. The registry is cross-checked against Manager's bundle recipes and the capability ledger.

The current `v0.14.2` delivery claim remains `base` only. The other CPU profiles are candidates until signed assets pass their complete Windows/Linux Python 3.10–3.13 matrices. Defining or testing a recipe does not promote it. Stable release construction rejects candidate/experimental profiles and rejects a stable profile whose coordinate matrix or target-platform smoke report is incomplete.

## Offline installation and supply-chain boundaries

Manager creates a short same-volume staging venv, installs every wheel from the signed lock with `pip --no-index --no-deps`, inventories it, moves it into the immutable final path, then computes and seals the final content hash. No active runtime is modified.

Semantic profiles require a signed model ID, revision, `trust_remote_code: false`, and every required relative file path, size, and SHA-256. Pickle-capable formats are rejected. Activation requires an explicit absolute local model directory and never downloads model files. OCR separately checks Python adapters and each native executable; adapter import success alone is not usability.

## Transactions and recovery

Core updates retain their existing `txn-*` journal and paired state snapshot. Profile changes use independent `ptxn-*` journals so a profile activation cannot replace the transaction needed for Core rollback.

`profile apply` downloads or verifies the exact signed asset, installs it side by side, runs adapter/native/model preflight and Core smoke, and only then atomically replaces the runtime fields in `active.yaml`. An interruption before pointer change leaves the prior profile active. `profile recover` closes an unfinished transaction or restores its paired prior pointer when necessary. `profile rollback` revalidates and smokes only the paired previous runtime.

## Capability doctor

`atomlearn-manager doctor` reports `available`, `declared`, `installed`, `usable`, and `stable` independently. Typed blockers include absent release declaration, missing/inactive profile, invalid installed content, missing Python adapter, missing native engine, missing model, unsafe model policy, and model hash mismatch. A repository import, host executable, or passing source test cannot by itself set `stable: true`.

## Interfaces

```text
atomlearn-manager profile status
atomlearn-manager profile plan <profile> [--runtime-bundle <signed.zip>]
atomlearn-manager profile apply <profile> [--runtime-bundle <signed.zip>] --confirmed
atomlearn-manager profile recover
atomlearn-manager profile rollback --confirmed
atomlearn-manager doctor [--capability <name>] [--model-dir <absolute-path>]
```

Core `update plan/apply` also accepts `--profile`; semantic activation accepts `--model-dir`. Experimental activation requires `--allow-experimental`.

## Compatibility

Historical release manifest v2 and runtime recipe v1 remain readable, verifiable, launchable, and rollback-compatible through their legacy runtime path and base-profile interpretation. New release builds require recipe v2. Existing active documents without profile fields remain valid; profile fields are added only when a recipe-v2 runtime is activated.

## Verification

The Phase 3 suite covers strict schemas, registry/ledger/Manager agreement, model-lock format rejection, signed smoke and dependency-lock identity, existing signed Core upgrade and paired rollback, Windows-safe final hashing, interruption before profile activation, profile recovery, capability doctor, activation, tamper detection, and paired profile rollback.

Final verification on Windows/Python 3.12:

- the final suite was split to retain independent long-running results: non-Manager tests **201 passed, 1 skipped** in 9:50, and complete Manager integration **16 passed** in 13:32, for **217 passed, 1 skipped** combined; the skip is the existing optional real-HNSW case because the local `scale` extra is absent.
- Manager schema/CLI contracts: **16 passed**.
- release `validate-skill`, Skill Creator `quick_validate.py`, and Python compileall: passed.
- all 52 Core/Manager JSON Schemas plus the runtime-profile registry instance: passed.
- English/Chinese README alignment: 338 lines each, 20 matching heading levels, and 17 matching code blocks.
- Core and Manager wheel/sdist builds: passed; the runtime registry and new Manager schemas are packaged.
