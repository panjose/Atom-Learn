# v0.15 Phase 8 Research Provider Contract Implementation

## Scope

Phase 8 closes the research-provider and cross-paper synthesis workstream from the v0.15 remediation design. It keeps provider acquisition bounded and auditable while preserving the AtomLearn rule that incomplete retrieval is not evidence of absence.

## Delivered

### 1. Shared provider contract

`atom-learn/scripts/research.py` now normalizes Crossref, OpenAlex, PubMed, Semantic Scholar, and arXiv records into one contract containing:

- provider and canonical identifiers, DOI, title, authors, year, venue, abstract, URL, and license metadata;
- references and forward citations when the provider exposes them;
- integrity status and source locator;
- per-field completeness flags;
- pagination state, rate-limit policy, and a three-attempt retry policy.

Provider responses are stored as exact, bounded cache receipts in `state.yaml`. Cache keys include provider, operation, and normalized request. A repeated request reuses the response; `--refresh-cache` replaces the receipt for that key. Typed provider failures record a stable code, message, retryability, operation, and request. A failed or unsupported provider operation returns incomplete coverage and never a `not found` claim.

### 2. Provider adapters

- Crossref uses bibliographic search and DOI reference lists, including abstract and license metadata when available.
- OpenAlex reconstructs inverted-index abstracts, preserves referenced-work IDs, and records retraction status.
- PubMed uses E-utilities ESearch plus ESummary and preserves PMID/DOI identifiers.
- Semantic Scholar uses Graph API search and retains bounded backward and forward citation relations.
- arXiv parses Atom XML, categories, abstracts, DOI, and license metadata.

Direct metadata acquisition uses the same normalized discovery and cache path for every provider, resolving by DOI when available and otherwise by title. Citation snowballing executes direct provider graphs when supported; unsupported directions produce `citation_graph_unavailable` rather than silently returning an empty graph. Harness Web Search remains an explicit submission boundary.

### 3. Provenance and reconciliation

Each paper retains provider observations instead of overwriting canonical metadata. Conflicting title, DOI, year, or venue values become `provider_disagreements` with `needs_review`. Citation provenance retains direction, provider, provider record ID, retrieval time, reference identifier, and resolved internal target. DOI/title deduplication continues to rewrite internal citation edges to the canonical paper.

### 4. Claim-level evidence matrix

Critical claims now accept `intervention_exposure` and `effect_direction`. `research synthesize` emits an `evidence_matrix` per theme. Each row contains the claim and paper IDs, structured facets, effect direction, support/neutral/opposition stance, and the source locator. Themes also expose supporting claim IDs, opposing claim IDs, and conditional boundaries. `LITERATURE_MATRIX.md` renders these rows, boundaries, and provider disagreements. Synthesis remains a proposal until `research review-synthesis` confirms, relabels, or rejects it.

## Schemas and templates

- `atom-learn/assets/schemas/research-provider-cache.schema.json` defines the cache receipt contract.
- `research-discovery-submission.schema.json` accepts provider identifiers, abstracts, licenses, citations, and completeness metadata.
- Research paper, note, discovery-submission, and state templates expose the new fields.

## Verification

Fast research verification is offline and deterministic:

```text
python -m py_compile atom-learn/scripts/research.py
python -m pytest tests/test_research.py -q
```

The research suite covers the existing reading loop plus five mocked direct providers, cache reuse, typed retryable failure, provider disagreement retention, citation provenance, and claim-matrix effect direction/support/opposition/boundary output. No test requires live provider network access.

## Boundaries and limitations

- Provider result caps remain bounded search evidence, not exhaustive systematic-review coverage.
- PubMed and arXiv do not provide a complete citation graph through the implemented public endpoints; unsupported graph directions are typed gaps.
- Provider observations and synthesis themes require review; normalized metadata does not authenticate an external provider identity.
- Abstracts and metadata are not substitutes for critical reading of the indexed full source. Full paper text remains outside canonical research state.
