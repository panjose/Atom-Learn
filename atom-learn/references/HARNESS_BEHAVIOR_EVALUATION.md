# Harness and Model Behavior Evaluation

## Evidence boundary

AtomLearn separates three evidence layers:

1. Engineering tests show that schemas, state transitions, recovery, retrieval, and report calculations work.
2. Harness/model behavior reports show how one exact model and harness configuration followed the teaching protocol on the versioned case set.
3. Consented human learning studies test learning, retention, and transfer against a declared control.

No result may be promoted to a stronger layer. An engineering smoke never verifies model compatibility. A passing model compatibility report never establishes learning benefit.

## Protocol v1

`assets/harness-behavior-protocol.yaml` contains 18 English and Chinese cases. Each category appears in both languages:

- single-Atom focus and future-content containment;
- detailed child-Atom expansion and parent reintegration;
- prerequisite, successor, branch, boundary, and out-of-scope routing;
- skip, test-out, backtrack, and resume consistency;
- held-back exam answer protection;
- current research claim locators;
- stale revision failure without mutation;
- sudden-close, duplicate-call, retry, and resume idempotency;
- grading abstention when scorer or evidence support is insufficient.

The protocol records fixed thresholds for protocol adherence, future-knowledge leakage, state mutation, citation support, resume success, abstention quality, and reviewer agreement. Atoms added per turn are reported separately instead of being hidden inside an aggregate pass.

Inspect and validate the immutable bundled protocol:

```text
atomlearn behavior protocol
atomlearn behavior validate-protocol
```

## Run contract

A run names its model provider/name/version, harness name/version, prompt protocol version, temperature, seed, timestamps, and run kind. Each case stores only a trace hash and structured ratings; raw prompts, outputs, or learner content are outside the report schema.

`engineering_smoke` requires one deterministic annotation per case and can produce only `engineering_smoke_only`. `model_compatibility` requires two distinct human evaluators for every case. When their required rubric fields disagree, an explicit adjudication is mandatory. Exact reviewer agreement is reported; the protocol does not disguise unresolved disagreement as a pass.

```text
atomlearn behavior validate-run --input <behavior-run.yaml>
atomlearn behavior evaluate --input <behavior-run.yaml> --output <behavior-report.yaml>
atomlearn behavior validate-report --input <behavior-report.yaml>
```

## Report interpretation

Reports have four gates:

- `incomplete`: missing cases, missing required metrics, invalid evaluator composition, or unresolved disagreement;
- `engineering_smoke_only`: a complete deterministic calculation test, never a model claim;
- `fail`: a complete dual-reviewed model run that misses at least one threshold;
- `pass`: a complete dual-reviewed model run that clears every threshold.

Even `pass` authorizes compatibility language only for the exact recorded model, harness, prompt protocol, language set, temperature, and seed. It does not automatically change the capability ledger's release-wide `harness_behavior` status. Maintainers must review and publish a versioned report before changing that claim.

Every report hard-codes `evidence_layer: harness_model_behavior` and `learning_effect_claims_allowed: false`, plus explicit lists of what it can and cannot establish. Human learning claims continue to require the separate consented study protocol in [LEARNING_EFFECT_STUDY.md](LEARNING_EFFECT_STUDY.md).
