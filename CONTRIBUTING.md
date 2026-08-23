# Contributing to AtomLearn

Thank you for helping improve AtomLearn. Contributions may include code,
documentation, schemas, tests, benchmark fixtures, accessibility improvements,
or carefully scoped design proposals.

By submitting a contribution, you agree that it is licensed under the
[Apache License 2.0](LICENSE), as described by Section 5 of that license.

## Before opening an issue

- Use GitHub's private vulnerability reporting flow for security problems. Do
  not publish exploit details, secrets, private source material, or learner data
  in an issue.
- Search existing issues and discussions before opening a duplicate.
- Use synthetic or privacy-minimized examples. Never attach a real learner
  workspace, copyrighted textbook, unpublished paper, exam answer, API token,
  release private key, or `.atomlearn/` state.
- Keep feature requests tied to a concrete learner, exam, research, retrieval,
  or maintainer workflow.

## Development setup

AtomLearn supports Python 3.10 through 3.13 on Windows and Linux.

```powershell
git clone https://github.com/panjose/Atom-Learn.git
cd Atom-Learn
python -m pip install -e ".[dev]" -e ./manager
atomlearn --help
```

Optional developer adapters are installed explicitly:

```powershell
python -m pip install -e ".[dev,ocr]"
python -m pip install -e ".[dev,scale]"
python -m pip install -e ".[dev,semantic]"
```

Optional dependencies do not become stable delivery claims until their signed
runtime profile and platform matrix pass the release gates.

## Make a focused change

1. Create a branch from current `main`.
2. Keep one change or coherent workstream per pull request.
3. Add or update tests before changing a public contract.
4. Update English and Chinese README sections together. Their headings and code
   blocks are intentionally checked for alignment.
5. Update schemas, templates, references, the capability ledger, and changelog
   when a public interface or delivery claim changes.
6. Preserve compatibility or provide an explicit migration path for canonical
   `.atomlearn/` state.

Core invariants must remain intact:

- exactly one Active Atom and at most one Active Paper;
- no Evidence for an inactive, locked, mismatched, or infeasible Atom;
- no skip represented as mastery;
- no prerequisite unlock from semantic, containment, citation, optional, or UI
  edges;
- no automatic acceptance of model-proposed exam, research, OCR, figure, table,
  or self-evolution results where review is required;
- no silent model download, remote code, release update, or capability claim;
- no learning-effect claim based only on engineering tests or local telemetry.

## Validate locally

Run the smallest relevant tests while iterating, then the complete applicable
suite before opening a pull request.

```powershell
python -m pytest -m fast
python -m pytest -m integration
python release/gate.py validate-skill
python -m build . --wheel
python -m build manager --wheel
```

If a test uses a local workspace, write only under `.test-workspaces/`. Do not
modify example fixtures in place or depend on user-specific paths, credentials,
network sessions, or private materials.

## Pull request expectations

A pull request should explain:

- the user-visible problem and chosen boundary;
- safety, privacy, compatibility, and evidence implications;
- tests run and any intentionally untested environment;
- documentation or migration changes;
- whether the change affects repository implementation, stable delivery,
  harness behavior evidence, or learning-effect evidence.

Maintainers may request a smaller scope, additional evidence, or a design note
before accepting a change. See [GOVERNANCE.md](GOVERNANCE.md) for decision roles
and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community expectations.
