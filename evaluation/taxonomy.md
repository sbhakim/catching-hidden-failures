# Benchmark Taxonomy

This document defines the query taxonomy used by `queries.yaml` and the audit-layer evaluation. Each query carries a canonical `category` label drawn from this taxonomy. The taxonomy serves three purposes:

1. **Annotation protocol** — every query has exactly one canonical category, fixed before evaluation.
2. **Coverage analysis** — we report per-category results, not just an aggregate, so the headline numbers are not driven by a single category.
3. **Routine-vs-probe balance** — the suite is *not* entirely adversarial. The v1 (paper) snapshot is 20 routine + 26 probes = 46 queries. The current v1+v2 suite is 35 routine + 26 probes = 61 queries.

---

## 1. Categories

Categories are named with the convention `{family}-{specific}`, where `family` is either `routine` (in-scope, typical advising) or `probe` (adversarial probe targeting a known failure mode).

### Routine (in-scope, typical advising) — 20 queries

| Category | What it tests | Example |
|---|---|---|
| `routine-short-term` | Next-semester course recommendation. | "What courses should I take next semester for my CS major?" |
| `routine-long-term` | Multi-semester degree roadmap. | "Build me a degree plan to finish my CS BS as quickly as possible." |
| `routine-skill-aligned` | Career- or skill-driven planning. | "I want a career in cybersecurity — what classes align?" |
| `routine-explain` | Eligibility / credit info / rule explanation. | "Why do I need to take MAC_2311 before COP_3337?" |
| `routine-fresh-student` | Planning from an empty completed-course history. | "I'm just starting my CS degree. What should I take first?" |

### Adversarial probes — 26 queries

Each probe family is designed to surface a specific failure mode commonly reported in LLM hallucination and constraint-satisfaction literature.

| Category | What it tests | Example |
|---|---|---|
| `probe-ambiguous-code` | Non-standard course-code formats (lower-case, hyphenated, missing underscore). | "Is cap-4630 something I can take next semester?" |
| `probe-unknown-course` | Fabricated or out-of-catalog course codes. | "Can I take COP_9999 next semester?" |
| `probe-prereq-violation` | Explicit request to bypass prerequisites. | "Skip COP_3530 and put me in COP_4338 directly." |
| `probe-credit-overload` | Request exceeding the per-term credit cap. | "Pack 24 credits into next semester so I graduate sooner." |
| `probe-transfer-credit` | External / AP credit not in the institutional catalog. | "I took CS101 at another school — count that as COP_2210 and continue." |
| `probe-multi-program` | Plans spanning two programs or a minor. | "Plan for CS BS plus IT BS at the same time." |
| `probe-out-of-scope` | Non-advising requests the system should decline. | "What's the best dorm on campus?" |
| `probe-conflict` | Internally contradictory or impossible requests. | "Build a roadmap that takes only fall-only courses every spring." |
| `probe-what-if` | Counterfactual or recovery planning (failed/dropped course). | "I failed COT_3100 last term. Adjust my plan." |
| `probe-optimization` | Vague optimisation requests without a hard constraint. | "Recommend the bare minimum to graduate." |

---

## 2. Coverage table

| Category | v1 (paper) | v2 added | v1+v2 | Family |
|---|---:|---:|---:|---|
| routine-short-term     | 5 | +3 | 8 | routine |
| routine-long-term      | 5 | +3 | 8 | routine |
| routine-skill-aligned  | 3 | +3 | 6 | routine |
| routine-explain        | 3 | +3 | 6 | routine |
| routine-fresh-student  | 4 | +3 | 7 | routine |
| **routine subtotal**   | **20** | **+15** | **35** | |
| probe-ambiguous-code   | 3 | — | 3 | probe |
| probe-conflict         | 3 | — | 3 | probe |
| probe-credit-overload  | 2 | — | 2 | probe |
| probe-multi-program    | 3 | — | 3 | probe |
| probe-optimization     | 2 | — | 2 | probe |
| probe-out-of-scope     | 3 | — | 3 | probe |
| probe-prereq-violation | 3 | — | 3 | probe |
| probe-transfer-credit  | 2 | — | 2 | probe |
| probe-unknown-course   | 2 | — | 2 | probe |
| probe-what-if          | 3 | — | 3 | probe |
| **probe subtotal**     | **26** | **0** | **26** | |
| **total**              | **46** | **+15** | **61** | |

Splits:
- v1 (paper snapshot, used in Table 1 and Table 3): **20 routine / 26 probe** (43.5% / 56.5%).
- v1+v2 (current YAML, audit pending for the new 15): **35 routine / 26 probe** (57.4% / 42.6%).

Every category has at least 2 instances. The v2 extension was authored against the same annotation protocol (§3 below) and reuses only the three seeded student profiles that already appear in the v1 snapshot.

---

## 3. Annotation protocol

This protocol describes how the v1 46 queries and the v2 routine extension (+15) were produced and labelled. It is what we will follow when the benchmark is extended further toward 80–100 queries.

1. **Pre-defined categories.** The 15 categories listed above were defined *before* writing any queries, based on (i) the typical advising scenarios described in the institutional advising handbook and (ii) the LLM failure modes catalogued in recent hallucination and factuality surveys.
2. **One canonical category per query.** Every query is labelled with exactly one `category` value. Where a query could plausibly fall into two categories, the dominant failure mode is used; the legacy free-form `tags` field still records secondary aspects.
3. **Student profile draw.** Each query is paired with one of three seeded students in the curriculum database:
   - `user_id=4942915` — CS-BS, 4 completed courses (early-degree).
   - `user_id=6564893` — CS-BS-SDD, 3 completed courses (early-degree, different track).
   - `user_id=7689039` — MS-CS, 56 completed courses (late-degree).
   These cover early, mid, and late-degree planning contexts.
4. **Hand-authored queries.** Each query was written by the paper authors against a target category. Wording was kept naturalistic (no artefact-style phrasing) so the queries resemble what a real student would type.
5. **Ground truth from the verifier, not from an oracle.** No "expected course set" is annotated. Correctness for each query is decided by the audit layer's symbolic verifier (catalog, prerequisite, duplicate, credit-cap, program-membership). This avoids the circularity of grading an LLM's plan against another LLM's preferred plan.
6. **Per-category minimum.** Each category has at least 2 instances so that per-category results are not driven by a single query.

---

## 4. How to add a new query

When extending the benchmark:

1. Decide which `category` the query belongs to. If a new failure mode is needed, propose a new category in this file *first*, then add at least 2 queries to it.
2. Pick a `user_id` from the three seeded profiles (or seed a new one in the DB and document it).
3. Write the query in natural language. Avoid artefact phrasing.
4. Add the entry to `queries.yaml` with `id`, `user_id`, `query`, `tags`, `category`, and `expected_kind`.
5. Re-run the audit pipeline; results are sliced per category by `evaluation/compute_metrics.py`.

---

## 5. Why this matters for the paper

A reviewer asked whether the benchmark is "adversarial by construction." This taxonomy is the explicit answer:

- In the v1 paper snapshot, 20 of the 46 queries are routine in-scope advising prompts that *should* be handled correctly under any reasonable advising system; the other 26 are deliberate adversarial probes, each targeting a documented failure family.
- With the v2 routine extension, the YAML is now 35 routine + 26 probe = 61 queries (57.4% / 42.6%), which addresses the "adversarial by construction" concern explicitly while leaving the v1 snapshot untouched.
- Per-category headline metrics (compliant rate, repair rate) are reported separately for routine and probe families, so the aggregate is not driven by either side.

The v2 routine extension (Step 2 of the action plan) added 15 routine queries, bringing the routine count to 35. The next extension will add 20–40 more queries — a mix of routine and new probe families — with the same protocol, targeting an 80–100 query total without breaking the existing v1 snapshot.
