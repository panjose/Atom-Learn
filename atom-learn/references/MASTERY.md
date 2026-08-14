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
- `teach_back`: organize an explanation for another beginner and expose missing links.

Select dimensions that match the Atom objective. Do not force computation onto a conceptual distinction or accept verbal fluency for a procedural skill.

## Check design

Use short, diagnostic checks. Ask one or two prompts at a time. Avoid copying the teaching example exactly. Include the central misconception when it is important to the objective. Do not ask “Do you understand?” as evidence.

## Test-out checks

When a learner says the Atom is easy or already mastered, skip the lecture before skipping the Evidence. Use `skip --mode diagnostic` to retrieve all required dimensions and thresholds, then create the smallest check that still covers them. A passed test-out check is normal mastered Evidence. A provisional skip is not Evidence and must not receive scores, confidence, or review scheduling.

## Expanded-child and integration checks

Check each expanded child against only its own objective. Do not require knowledge from a later child and do not accept one broad response as Evidence for several children.

After all children are mastered, check the parent in `integrating` phase with a new prompt that requires their relationships or joint application. Child Evidence does not automatically master the parent. Expanded children may use diagnostic test-out Evidence, but cannot use provisional skip.

## Scoring

Score each required dimension from 0.0 to 1.0:

- `0.0–0.39`: absent or substantially incorrect;
- `0.4–0.59`: partial with a blocking misconception;
- `0.6–0.79`: functional but incomplete or fragile;
- `0.8–0.89`: correct and independently usable;
- `0.9–1.0`: precise, robust, and transferable for the requested level.

The CLI marks `mastered` only when the average meets `pass_threshold` and every required dimension meets `minimum_dimension_score`. It marks `partial` when the average is at least 0.5, otherwise `not_mastered`.

Save the prompt, response summary, scores, feedback, and rationale. Do not save a fabricated verbatim learner answer.

## Remediation

Target the lowest-scoring dimension. Rebuild causal links for weak explanation, fade scaffolding for weak application, use counterexamples for weak discrimination, vary one context feature for weak transfer, and request a revised missing link for weak teach-back. Do not repeat the entire original lecture by default.

## Spaced review

Use the course's configured intervals, defaulting to 1, 3, 7, and 30 days. On a successful review, schedule the next interval. On failure, keep historical Evidence, lower derived confidence, and create targeted remediation before rescheduling.

Prefer due reviews before new material unless the learner has explicitly chosen a different session goal.
