# Evolution Capsule

An Evolution Capsule is a user-initiated, privacy-minimized product-improvement candidate. It is not telemetry, it does not upload anything, and it never changes Core behavior or a course.

## Build and export

Build only from an existing, validated course evolution proposal with at least three referenced observations:

```text
atomlearn evolve capsule build <workspace> --proposal <proposal-id>
atomlearn evolve capsule preview <local-capsule-path>
atomlearn evolve capsule export <local-capsule-path> --output <explicit-path> --confirmed
```

`build` derives enum categories and bucketed counts from local state, assigns a random one-time Capsule ID, runs privacy lint, and writes JSON plus a Markdown rendering under the platform user-data directory. It does not write into the course and does not make a network request.

`preview` displays the complete Capsule and records a local receipt for its hash. `export` requires matching lint and preview receipts, explicit confirmation, a non-existing output path, and an unused Capsule ID. Build a new Capsule if another export is needed. The canonical content hash remains identical across build, preview, and export.

## Privacy contract

The exported JSON contains only:

- Core and schema versions;
- enum failure, feature, candidate, and coarse-window categories;
- occurrence, attempt-delta, and delayed-review buckets;
- constant-false content and identifier flags plus `lint_status: passed`;
- optionally, a SHA-256 reproduction-fixture hash.

Unknown fields fail schema validation. Privacy lint rejects raw or free-text escape hatches, source content, absolute paths, URLs, DOI values, precise timestamps, account identifiers, UUID-shaped stable identifiers, and the two-observation bucket. Profile, workspace, course, source, and Atom identifiers are never copied. Lint and preview receipts stay local and are not part of the export.

There is deliberately no `submit` command, network client, telemetry flag, or background sender in the Capsule module. A future remote submission capability would require a separate command, separate consent, and a new security review.

## Maintainer workflow

Maintainers may validate and deterministically deduplicate exported Capsules without a stable user ID:

```text
atomlearn evolve capsule maintainer-ingest <capsule-path> --store <absolute-store>
atomlearn evolve capsule fixture-convert <capsule-path> --output <fixture.yaml> --confirmed
```

The semantic fingerprint excludes the one-time Capsule ID and privacy constants. Triage records contain only enums, hashes, a duplicate count, routing, and priority. Fixture conversion creates a `needs_reproduction` seed with `requires_reproduction_test: true` and `automatic_patch_allowed: false`.

A Capsule is discovery evidence, not a product conclusion. Reproduce the failure independently, add a failing test, make a normal reviewed repository change, run the full regression suite and Skill validator, then ship through the versioned release process. Never generate or merge code automatically from one Capsule.
