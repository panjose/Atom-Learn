# Mastery and review rubric

## Contents

- Evidence dimensions
- Check design
- Test-out checks
- Expanded-child and integration checks
- Scoring
- Remediation
- Spaced review

## Evidence dimensions

- `explain`: state the mechanism accurately in the learner's own words.
- `apply`: use it in a representative problem or procedure.
- `discriminate`: identify boundaries, counterexamples, or common misconceptions.
- `transfer`: use it in a modestly changed context.
- `connect`: relate the Atom to prerequisite, successor, or sibling concepts without substituting those concepts for the current objective.
- `compute`: produce a correct value under a declared numeric/unit contract.
- `derive`: produce and justify the required intermediate steps.
- `critique` / `evaluate`: assess a supplied claim, method, or artifact against declared criteria.

`teach_back` is a task form that may measure `explain` and `connect`; it is not automatically a separate score dimension.

Select dimensions that match the Atom objective. Do not force computation onto a conceptual distinction or accept verbal fluency for a procedural skill.

## Check design

Use short, diagnostic checks. Ask one or two prompts at a time. Avoid copying the teaching example exactly. Include the central misconception when it is important to the objective. Do not ask “Do you understand?” as evidence.

## Test-out checks

When a learner says the Atom is easy or already mastered, skip the lecture before skipping the Evidence. Use `skip --mode diagnostic` to retrieve all required dimensions and thresholds, then create the smallest check that still covers them. A passed test-out check is normal mastered Evidence. A provisional skip is not Evidence and must not receive scores, confidence, or review scheduling.

## Expanded-child and integration checks

Check each expanded child against only its own objective. Do not require knowledge from a later child and do not accept one broad response as Evidence for several children.

After all children are mastered, check the parent in `integrating` phase with a new prompt that requires their relationships or joint application. Child Evidence does not automatically master the parent. Expanded children may use diagnostic test-out Evidence, but cannot use provisional skip.

## Scoring

Use [MEASUREMENT.md](MEASUREMENT.md) for Evidence v3 task-form compatibility, immutable scorer provenance, feasibility preflight, item banks, held-out retention/transfer checks, calibration, eligibility, and legacy migration. The numeric rubric below defines educational meaning; it does not by itself qualify the task or scorer.

Score each required dimension from 0.0 to 1.0:

- `0.0–0.39`: absent or substantially incorrect;
- `0.4–0.59`: partial with a blocking misconception;
- `0.6–0.79`: functional but incomplete or fragile;
- `0.8–0.89`: correct and independently usable;
- `0.9–1.0`: precise, robust, and transferable for the requested level.

The CLI aggregates compatible, qualified Evidence across items. It marks `mastered` only when every required dimension has an eligible score, the aggregate average meets `pass_threshold`, every dimension meets `minimum_dimension_score`, and the Atom's evidence-diversity/delayed/transfer policy passes. A single item may cover only a subset. The Atom remains Active while any required path is missing.

Save the prompt, response summary, scores, feedback, rationale, measurement kind, item and episode IDs, task form, response mode, item family, novelty scope, supported dimensions, registered grader/rubric/calibration provenance, independence claim, and a local answer hash. Core derives `required_dimension_scores` from the Atom/item/task/scorer intersection; never ask the caller to override them. Do not save a fabricated verbatim learner answer.

Run `atomlearn measure feasibility <workspace>` before activation when mastery dimensions or scorer availability change. For higher-risk claims, set at least two item families/forms and require delayed or held-out transfer Evidence as appropriate. If no valid path exists, narrow the claim or label the Atom reading/exploration instead of weakening the gate.

Free model scores and other unregistered, uncalibrated, or non-independent graders may guide feedback but cannot independently master an Atom. Prefer a Core deterministic item when the answer contract is exact, otherwise use a registered calibrated, dual-independent, or human scorer. Raw responses stay local and are not copied into canonical Evidence.

Record Evidence only for `current.active_atom_id`, and require that Atom's canonical status to be `active`. Do not pre-write Evidence for available, locked, deferred, skipped, mastered, or archived Atoms. `validate` rejects pending Evidence whose Atom is no longer Active.

## Remediation

Target the lowest-scoring dimension. Rebuild causal links for weak explanation, fade scaffolding for weak application, use counterexamples for weak discrimination, vary one context feature for weak transfer, and request a revised missing link for weak teach-back. Do not repeat the entire original lecture by default.

## Spaced review

Use the course's configured intervals, defaulting to 1, 3, 7, and 30 days. This fixed policy remains the default. On a successful review, schedule the next interval. On failure, keep historical Evidence, lower derived confidence, and create targeted remediation before rescheduling.

Prefer due reviews before new material unless the learner has explicitly chosen a different session goal.

For per-Atom memory, shadow suggestions, gated active scheduling, normalized active-retrieval observations, exam target windows, and the unified capacity-aware queue, read [ADAPTIVE_REVIEW.md](ADAPTIVE_REVIEW.md). Passive reading, recognition, self-report, satisfaction, chat duration, and response speed alone never update memory. A policy change does not rewrite pending or historical review dates.
