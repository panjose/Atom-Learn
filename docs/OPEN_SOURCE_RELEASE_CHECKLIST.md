# AtomLearn Open-Source Release Checklist

This checklist separates controls that are enforced by the repository from
settings that require a GitHub repository administrator. Complete the unchecked
items immediately before changing repository visibility. Re-run the list after
any visibility, ownership, release, or security-setting change.

## How to use this checklist

- `[x]` means the control is present in the current source tree and covered by
  repository validation.
- `[ ]` means a maintainer must verify or configure GitHub state. A source
  commit cannot prove that external state.
- Items explicitly marked optional are defense-in-depth recommendations and do
  not block publication when the repository owner accepts the residual risk.
- Do not make the repository public while any privacy, credential, provenance,
  licensing, or release-signing item remains unresolved.

## Repository baseline

- [x] Apache-2.0 is declared consistently in Core, Manager, package metadata,
  `LICENSE`, `NOTICE`, and `THIRD_PARTY_NOTICES.md`.
- [x] The optional OCR adapter uses pypdfium2/PDFium instead of PyMuPDF; wheel
  contents and license metadata are checked after build.
- [x] `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `SUPPORT.md`,
  `GOVERNANCE.md`, and `CITATION.cff` define the community contract.
- [x] Issue forms, the pull-request template, CODEOWNERS, Dependabot, CodeQL,
  and the Open Source Readiness workflow are version controlled.
- [x] English and Chinese READMEs identify signed `v0.15.0` base-only delivery
  and the absence of an
  established AtomLearn learning-gain result.
- [x] `python release/open_source_gate.py` scans required files, tracked private
  paths, credential patterns, user-specific absolute paths, Git history, and
  optional built wheels without printing a matched secret.
- [x] Publish and independently verify the signed `v0.15.0` manifest, Core,
  Manager, and complete base runtime matrix before treating the release notes as
  stable delivery evidence.

## Privacy and history audit

- [ ] Review every branch, tag, release, issue, pull request, discussion, wiki,
  Actions log, artifact, and cache that will become visible or remain linked.
- [ ] Run `python release/open_source_gate.py --json` from a full clone with all
  refs fetched, then independently review the result and GitHub secret-scanning
  alerts. Pattern matching is a backstop, not proof that no secret exists.
- [ ] Confirm that every previously committed credential has been revoked and
  rotated even if it was later removed. Never rely on deletion alone.
- [ ] Confirm that no learner state, copyrighted textbook, paper corpus, exam
  answer, unpublished result, model credential, cookie, signing private key, or
  release-key backup exists in any public ref or hosted artifact.
- [x] The repository owner accepted public exposure of commit-author email
  `242panjose@gmail.com` on 2026-08-23. Current Git history contains it; do not
  rewrite signed/tagged history casually because rewriting changes commit
  identities and requires a separate migration and release-integrity plan.
- [ ] Review repository collaborators, deploy keys, webhooks, GitHub Apps,
  Actions secrets/variables, environments, Pages, Codespaces, and package
  permissions. Visibility changes do not grant permission to expose a secret.

## GitHub security and governance

- [ ] Enable GitHub private vulnerability reporting and subscribe maintainers to
  security-alert notifications before directing users to `SECURITY.md`.
- [ ] Verify the dependency graph, Dependabot alerts, and Dependabot security
  updates. Triage the initial alert set before launch.
- [ ] Verify public-repository secret scanning, review every alert, and enable
  repository push protection where the account/plan permits it.
- [ ] Confirm the checked-in CodeQL workflow runs successfully after the
  repository becomes public. It intentionally skips private repositories that
  do not have GitHub Code Security access.
- [ ] Optional: create a `main` ruleset that requires pull requests and successful checks
  for `Validate AtomLearn`, `Open Source Readiness`, and `CodeQL`; block branch
  deletion and force pushes; define a narrow emergency bypass role.
- [ ] Optional: protect `v*` tags against update and deletion. Treat every published tag,
  manifest, runtime bundle, trust bundle, and signature as immutable.
- [ ] Limit Actions permissions to read by default, require approval for
  first-time contributors, and review which third-party actions are allowed.
- [ ] Configure the release environment, `ATOMLEARN_RELEASE_PRIVATE_KEY`, and
  `ATOMLEARN_RELEASE_KEY_ID` with least privilege and optional required
  reviewers. The private key must never enter repository files or logs.

## Release and community boundary

- [ ] Verify repository description, website, topics, social preview, default
  branch, Releases, Discussions choice, and issue/discussion moderation owners.
- [ ] Create a clean-room clone, install Core and Manager from the intended
  public instructions, and run the quick verification on Windows and Linux.
- [ ] Build Core and Manager wheels and run
  `python release/open_source_gate.py --skip-history --wheel-dir <directory>`.
- [ ] Confirm that all included fixtures and media are synthetic, authored for
  this repository, public-domain, or distributed under a compatible documented
  license. No comic or promotional image is part of this launch.
- [ ] Publish release notes that distinguish repository implementation, signed
  stable delivery, harness/model behavior evidence, and human learning-effect
  evidence. Never infer the latter from tests, benchmarks, or local telemetry.
- [ ] Define initial maintainer response expectations for security reports,
  bugs, support requests, and contribution review; keep them realistic rather
  than promising an SLA that cannot be sustained.

## Final visibility change

- [ ] Freeze merges and releases for the final audit window.
- [ ] Record the exact audited commit and the result of repository, history,
  wheel, CI, CodeQL, and manual privacy reviews.
- [ ] Have the repository owner perform GitHub's visibility change only after
  acknowledging its consequences and confirming all preceding items.
- [ ] Immediately verify anonymous cloning, README/license rendering, issue
  forms, private vulnerability reporting, Actions, branch/tag rulesets,
  Dependabot, CodeQL, Releases, and package links from a signed-out session.
- [ ] Announce the repository only after post-change verification passes. If a
  secret or private artifact appears, contain access, revoke/rotate first, and
  use the documented incident process before resuming launch.
