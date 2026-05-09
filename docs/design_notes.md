# Design notes

Internal scratchpad. Not user-facing.

## Thesis in one sentence

Treat any LLM-generated advising plan as an untrusted artifact: parse it,
verify it against symbolic curriculum rules, explain failures from the
proof, and repair under verifier-checked feedback.

## What the paper claims

Claims:

1. A model-agnostic post-generation audit interface with bounded
   semantics (prereqs, credit caps, duplicates, course existence,
   program membership) plus a structural empty-plan guard.
2. Quantitative evidence that modern LLMs, left unsupervised, produce
   advising plans with measurable rates of prereq and program-membership
   violations that this verifier catches.
3. A repair operator that returns compliant plans with bounded edit
   distance, only counting a fix as successful after re-verification.
4. A leave-one-out ablation that isolates which constraint family
   carries detection and repair difficulty.

Does **not** claim:

- A new LLM, a new prompt template, or fine-tuning.
- Auto-extraction of rules from catalogs (future work).
- Multi-institution portability — single-institution study.

## Why post-generation matters

- **Generator-agnostic.** Pre-generation grounding ties safety to a
  single trusted pipeline. The audit layer's input is a plan, so any
  generator (commercial API, local HF model, tool-calling agent) can be
  swapped without re-tuning prompts or re-validating outputs.
- **Auditability.** Violations are first-class objects with a Prolog
  proof, not LLM narration. A downstream advisor or registrar service
  can act on them programmatically.
- **Counterfactual analysis.** `verify(skip_checks=(...))` lets the
  ablation runner quantify each constraint family's contribution
  without forking the verifier.

## Scope decisions

- **One institution.** Multi-institution would re-introduce the
  catalog-extraction problem this paper deliberately doesn't solve.
- **Greedy repair.** ILP is more principled but harder to ship; greedy
  gives a defensible baseline and an upper bound on edit distance.
- **Single-shot, not multi-turn.** Dialog state is a separate surface.
- **No fine-tuning.** Adding it would muddle the model-agnostic story.

## Open issues

1. **Adversarial query coverage.** 46 queries are enough for the headline
   table but small for tag-sliced claims. v1 should expand to 80–100.
2. **`needed_chain/3` self-references** observed in some program files;
   the explainer falls back to the immediate-prereq tree, but the
   underlying rule files should be cleaned up.
3. **Repair is remove-heavy.** v0 prefers removal; replacing with an ILP
   that prefers `move` and considers feasible substitutions from
   `program_course` is the next obvious lever.
4. **Proof trees are coarse.** v0 uses `needed_chain/3` output as a
   stand-in for a structured proof. v1 should use `clause/2`
   introspection rather than parsing SWI-Prolog's leashed trace.

## Roadmap (rough)

| Step | Goal |
|---|---|
| 1 | Smoke test green; ≥1 LLM runner working |
| 2 | All queries audited for ≥2 models; first metric pass |
| 3 | Fix `needed_chain` issue; upgrade explainer |
| 4 | Replace greedy repair with ILP (OR-Tools) |
| 5 | Add baseline: tool-calling LLM agent without audit |
| 6 | Expand benchmark to 80–100 queries |
