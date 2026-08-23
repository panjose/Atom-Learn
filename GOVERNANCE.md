# Governance

AtomLearn currently uses a maintainer-led governance model.

## Roles

- **Users** may open issues, discussions, and private vulnerability reports.
- **Contributors** submit documentation, tests, designs, code, or review under
  the Apache-2.0 contribution terms.
- **Reviewers** provide technical feedback but do not gain release or security
  authority solely by reviewing a change.
- **Maintainers** decide roadmap priorities, merge pull requests, moderate the
  community, manage trust roots, and publish signed releases.

The current lead maintainer is `@panjose`.

## Decisions

Routine fixes may be accepted through normal review. Changes to canonical state,
mastery Evidence, privacy boundaries, self-evolution authority, release trust,
capability claims, migration compatibility, research review gates, or learning-
effect language should include a design record or a clearly documented rationale.

Maintainers prefer reversible, schema-validated, tested changes. Consensus is
sought where practical, but the lead maintainer makes the final decision when a
decision is required. Rejected proposals should receive a concise technical,
product, safety, maintenance, or scope reason.

## Releases and trust

Only maintainers with explicit release authority may access signing credentials,
change the public trust bundle, configure protected release environments, or
publish stable assets. A merged feature is repository implementation, not stable
delivery, until the signed release matrix passes.

Security reports and embargoed fixes are handled privately under
[SECURITY.md](SECURITY.md). Community behavior is governed by
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

This model may evolve as the contributor base grows. A governance change must be
proposed publicly, preserve security response continuity, and identify the new
decision and release authorities before it takes effect.
