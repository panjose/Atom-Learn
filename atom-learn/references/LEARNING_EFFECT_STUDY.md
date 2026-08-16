# Learning-effect studies

Use this namespace only for a separately consented real-learning study. Strategy experiments, global personalization, course participation, engineering tests, and scorer calibration are not consent to research. The namespace is local-only and default-off.

## Required design

An enrollment preregisters a control and candidate condition, assignment method, missing-data policy, analysis version, domain and prior-knowledge strata, and all five learning measures: immediate mastery, 7-day retention, 30-day retention, near transfer, and far transfer. Completion, withdrawal, time, and prompt burden may be added as process measures. No engineering or calibration result may replace these measures.

Consent must be explicit, versioned, withdrawable, and limited to named data categories. Core fixes these privacy properties: raw answers and content text are forbidden, references are opaque, data remains local, and automatic export is disabled. Unknown fields fail closed.

```text
atomlearn study enroll study-transfer-pilot --input enrollment.yaml
atomlearn study record study-transfer-pilot --input observation.yaml --expected-study-revision 1
atomlearn study status study-transfer-pilot
atomlearn study withdraw study-transfer-pilot --confirmed --expected-study-revision 2
atomlearn study validate study-transfer-pilot
```

## Minimized observations

Each observation contains only opaque participant and episode references, assignment, a declared measurement kind, a bounded score or enumerated missing reason, completion, a coarse duration bucket, prompt count, domain and prior-knowledge buckets, and optional 1–5 satisfaction/burden ratings. It cannot contain prompts, source text, raw answers, quotations, names, email addresses, or free-text demographic fields. The measurement and non-default process fields must be covered by the consented data categories.

Participant/episode/measurement tuples are append-once. Revision checks reject stale writers. The ledger records only opaque IDs and measurement kinds, not learner content.

## Withdrawal and claims

`study withdraw --confirmed` preserves the audit history but marks every retained observation ineligible for analysis and blocks new observations. This is recoverable audit retention, not permission to use withdrawn data. AtomLearn never automatically exports or aggregates study records.

The `status` result always reports `learning_effect_claim_supported: false`: a local data contract alone is not an analyzed causal result. Any later study report must separately document allocation, sample size, missingness, intervals, preregistered deviations, adverse experience, domain/prior-knowledge strata, and independent review before it can support a bounded learning-effect statement.
