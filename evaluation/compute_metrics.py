"""Compute headline metrics across one or more audited LLM runs.

This is the script that produces Table 1 of the paper. All rates are
computed against the full input population N (the number of audited rows
in the file), *not* the number of flagged plans, so they are directly
comparable across models.

Headline metrics:
  - parse_success_rate        : % plans where the parser recovered ≥1 block
  - compliant_rate            : % plans the verifier accepted unchanged
  - prereq_violation_rate     : % plans with ≥1 prereq violation
  - credit_cap_violation_rate : % plans with ≥1 credit-cap violation
  - unknown_course_rate       : % plans referencing nonexistent courses
  - duplicate_rate            : % plans re-listing already-completed courses
  - program_mismatch_rate     : % plans containing courses not in the program
  - mean_violations_per_plan  : average # of violation records per plan
  - mean_edit_distance        : average # of repair ops per plan
  - repair_success_rate       : among plans where repair was attempted,
                                % whose *re-verified* output was clean.
                                This is the verifier-checked figure, not a
                                counted-edits proxy — a bad fix that still
                                violates a constraint does not count as a
                                success.

Slice-able by tags (e.g. only adversarial, only short_term).

Optional 95% bootstrap confidence intervals (--bootstrap):
The verifier is called exactly once per audited row to compute the
expensive per-record outcomes; bootstrap then resamples *those cached
outcomes* with replacement, so the DB is touched only once per snapshot
no matter how many bootstrap iterations are requested.

Usage:
    python evaluation/compute_metrics.py --inputs evaluation/audited/*.jsonl
    python evaluation/compute_metrics.py --inputs evaluation/audited/*.jsonl --bootstrap 1000
"""
from __future__ import annotations
import argparse, json, glob, random
from collections import defaultdict
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit_layer import verifier
from audit_layer.models import Plan


def _has_kind(violations: list[dict], kind: str) -> bool:
    return any(v["kind"] == kind for v in violations)


# Metric keys whose aggregate is a population mean over per-record bool/int
# outcomes. Keyed by the per-record field they read.
_MEAN_OVER_N: dict[str, str] = {
    "parse_success_rate":        "parse_ok",
    "compliant_rate":            "compliant",
    "prereq_violation_rate":     "has_prereq",
    "credit_cap_violation_rate": "has_cap",
    "unknown_course_rate":       "has_unknown",
    "duplicate_rate":            "has_dup",
    "program_mismatch_rate":     "has_prog",
    "mean_violations_per_plan":  "n_violations",
    "mean_edit_distance":        "edit_distance",
}


def per_record_outcomes(
    rows: list[dict], *, no_plan_treatment: str = "fail"
) -> list[dict]:
    """Compute every per-record fact once.

    The expensive bit is `repair_compliant`, which calls the live verifier
    on the repaired plan. Everything else is read from the cached JSONL
    fields, so the verifier is invoked at most once per row no matter how
    many times we aggregate (including bootstrap resampling).

    `no_plan_treatment` controls how rows that the parser failed on (the
    structural ``no_plan_extracted`` guard fired) are counted:
      - ``"fail"`` (default, paper-consistent): the row stays in the
        sample. Because the audit stage emits an empty-shell repaired
        plan even for these rows, they remain in the repair-rate
        denominator and re-verification will (correctly) mark them as
        repair failures. This reproduces the published Table 1 numbers
        bit-for-bit (e.g., Gemma 0.630 = 29/46).
      - ``"exclude"``: the row is dropped from the sample entirely. All
        rates and counts recompute over the smaller population. This
        answers the reviewer ask "*sensitivity analysis for parse
        failures (e.g., counting NO_PLAN as repair failure)*" by showing
        how the headline numbers would shift if NO_PLAN were treated as
        out-of-scope rather than as a repair failure.
    """
    if no_plan_treatment not in {"fail", "exclude"}:
        raise ValueError(
            f"no_plan_treatment must be 'fail' or 'exclude', got "
            f"{no_plan_treatment!r}"
        )
    out: list[dict] = []
    for r in rows:
        viols = r["violations"]
        is_no_plan = _has_kind(viols, "no_plan_extracted")
        if no_plan_treatment == "exclude" and is_no_plan:
            continue
        rec = {
            "parse_ok":         bool(r["parsed_plan"]["blocks"]),
            "compliant":        bool(r.get("compliant")),
            "has_prereq":       _has_kind(viols, "prereq_missing"),
            "has_cap":          _has_kind(viols, "credit_cap_exceeded"),
            "has_unknown":      _has_kind(viols, "unknown_course"),
            "has_dup":          _has_kind(viols, "duplicate_of_completed"),
            "has_prog":         _has_kind(viols, "program_requirement_unmet"),
            "is_no_plan":       is_no_plan,
            "n_violations":     len(viols),
            "edit_distance":    r.get("edit_distance", 0),
            "repair_attempted": r.get("repaired_plan") is not None,
            "repair_compliant": False,   # filled below if applicable
        }
        if rec["repair_attempted"]:
            repaired = Plan.model_validate(r["repaired_plan"])
            rec["repair_compliant"] = not verifier.verify(repaired)
        out.append(rec)
    return out


def aggregate(outs: list[dict]) -> dict:
    """Aggregate per-record outcomes into the headline metric dict."""
    n = len(outs) or 1
    rep_att = [o for o in outs if o["repair_attempted"]]
    agg: dict = {"n": len(outs)}
    for k, field in _MEAN_OVER_N.items():
        agg[k] = sum(o[field] for o in outs) / n
    agg["repair_success_rate"] = (
        sum(o["repair_compliant"] for o in rep_att) / (len(rep_att) or 1)
    )
    return agg


def metrics_for(rows: list[dict], *, no_plan_treatment: str = "fail") -> dict:
    """Headline metrics for one snapshot. Backwards-compatible wrapper.

    Default ``no_plan_treatment="fail"`` reproduces the paper-published
    Table 1 numbers bit-for-bit. Pass ``no_plan_treatment="exclude"`` for
    the sensitivity-analysis variant in which NO_PLAN_EXTRACTED rows are
    dropped from the sample entirely.
    """
    return aggregate(per_record_outcomes(
        rows, no_plan_treatment=no_plan_treatment
    ))


def timing_summary(rows: list[dict]) -> dict | None:
    """Aggregate per-stage audit timings if the JSONL carries them.

    Returns ``None`` when no record has a ``timings_ms`` field — older
    audited snapshots (v1 paper) lack the field, and we don't want
    ``--show-timings`` to spew zeros at them. Otherwise returns a dict
    mapping stage name to {n, mean, median, p95, max} milliseconds.
    """
    stages = ("parser", "verifier", "explainer", "repair", "total")
    samples: dict[str, list[float]] = {s: [] for s in stages}
    seen = False
    for r in rows:
        t = r.get("timings_ms")
        if not t:
            continue
        seen = True
        for s in stages:
            if s in t:
                samples[s].append(float(t[s]))
    if not seen:
        return None

    def _p(xs: list[float], q: float) -> float:
        if not xs:
            return 0.0
        idx = max(0, min(len(xs) - 1, int(q * (len(xs) - 1))))
        return sorted(xs)[idx]

    out: dict[str, dict] = {}
    for s in stages:
        xs = samples[s]
        if not xs:
            continue
        xs_sorted = sorted(xs)
        out[s] = {
            "n":      len(xs),
            "mean":   round(sum(xs) / len(xs), 2),
            "median": round(xs_sorted[len(xs) // 2], 2),
            "p95":    round(_p(xs_sorted, 0.95), 2),
            "max":    round(xs_sorted[-1], 2),
        }
    return out


def pairwise_diff_ci(
    outs_a: list[dict],
    outs_b: list[dict],
    *,
    metric_key: str = "repair_success_rate",
    B: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict:
    """Paired-bootstrap CI on the difference in a metric between two snapshots.

    Why *paired*: the same set of N queries was asked to every generator,
    so the per-record outcomes are paired by query index. Resampling the
    *same* indices for both snapshots on each replicate (rather than two
    independent resamples) preserves that pairing, controls for
    query-difficulty variance, and gives the correct null-hypothesis test
    of "model A is no better than model B on this query set".

    Returns the difference point estimate, the [lo, hi] percentile CI on
    that difference, and an `excludes_zero` flag that signals statistical
    significance at the chosen `alpha`.

    Both snapshots must be the same length and aligned by query index;
    the caller is responsible for that alignment (typically via JSONL
    ordering inherited from queries.yaml).
    """
    if len(outs_a) != len(outs_b):
        raise ValueError(
            f"snapshots have different lengths: {len(outs_a)} vs {len(outs_b)}"
        )
    n = len(outs_a)
    if n == 0:
        return {
            "metric": metric_key, "n": 0, "diff_point": 0.0,
            "lo": 0.0, "hi": 0.0, "excludes_zero": False,
        }
    point = aggregate(outs_a)[metric_key] - aggregate(outs_b)[metric_key]
    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(B):
        idx = [rng.randrange(n) for _ in range(n)]
        ra = [outs_a[i] for i in idx]
        rb = [outs_b[i] for i in idx]
        diffs.append(aggregate(ra)[metric_key] - aggregate(rb)[metric_key])
    diffs.sort()
    lo_idx = int(alpha / 2 * B)
    hi_idx = int((1 - alpha / 2) * B) - 1
    lo, hi = diffs[lo_idx], diffs[hi_idx]
    return {
        "metric":        metric_key,
        "n":             n,
        "diff_point":    point,
        "lo":            lo,
        "hi":            hi,
        "excludes_zero": (lo > 0) or (hi < 0),
        "B":             B,
        "seed":          seed,
    }


def bootstrap_cis(
    outs: list[dict], *, B: int = 1000, seed: int = 0, alpha: float = 0.05
) -> dict[str, tuple[float, float]]:
    """Percentile-bootstrap 95% CIs for every metric in `aggregate`.

    Resamples `outs` with replacement B times, recomputes aggregate on each
    resample, then returns the (lower, upper) percentile bounds at
    alpha/2 and 1-alpha/2. The verifier is NOT re-run; bootstrap operates
    on the already-computed per-record outcomes.
    """
    rng = random.Random(seed)
    n = len(outs)
    if n == 0:
        return {}
    # Run all B replicates first, then take percentiles per metric.
    samples: list[dict] = []
    for _ in range(B):
        resample = [outs[rng.randrange(n)] for _ in range(n)]
        samples.append(aggregate(resample))
    ci: dict[str, tuple[float, float]] = {}
    lo_idx = int(alpha / 2 * B)
    hi_idx = int((1 - alpha / 2) * B) - 1
    for k in samples[0]:
        if k == "n":
            continue
        vals = sorted(s[k] for s in samples)
        ci[k] = (vals[lo_idx], vals[hi_idx])
    return ci


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True,
                    help="One or more audited JSONL files (or glob patterns)")
    ap.add_argument("--by_tag", action="store_true",
                    help="Also report metrics sliced by query tag")
    ap.add_argument("--bootstrap", type=int, default=0, metavar="B",
                    help="If >0, also report percentile-bootstrap 95%% CIs "
                         "from B resamples (default 0 = off; recommended 1000)")
    ap.add_argument("--bootstrap-seed", type=int, default=0,
                    help="Seed for bootstrap resampling (default 0)")
    ap.add_argument("--no-plan-treatment", choices=["fail", "exclude"],
                    default="fail",
                    help="How to count rows where the parser failed "
                         "(no_plan_extracted): 'fail' (default) keeps them "
                         "as repair failures (paper-consistent); 'exclude' "
                         "drops them from the sample entirely for a "
                         "sensitivity analysis.")
    ap.add_argument("--show-timings", action="store_true",
                    help="If the audited JSONL records carry a 'timings_ms' "
                         "field (added by run_audit.py going forward), also "
                         "print per-stage audit-overhead stats. Silently "
                         "skipped for older snapshots that lack the field.")
    ap.add_argument("--pairwise", nargs="?", const="repair_success_rate",
                    default=None, metavar="METRIC",
                    help="Print a pairwise paired-bootstrap matrix of "
                         "differences in the given metric across all input "
                         "snapshots. Default metric is 'repair_success_rate'. "
                         "Uses the same --bootstrap / --bootstrap-seed values.")
    args = ap.parse_args()

    files: list[str] = []
    for p in args.inputs:
        matched = glob.glob(p)
        files.extend(matched if matched else [p])

    # Pairwise mode caches per-snapshot outs so we can compute every pair
    # without redoing per_record_outcomes. Filled inside the per-file loop.
    pairwise_cache: list[tuple[str, list[dict]]] = []

    print(f"\n=== Metrics across {len(files)} run(s) ===\n")
    for f in files:
        rows = [json.loads(l) for l in Path(f).read_text().splitlines() if l.strip()]
        if not rows:
            print(f"{f}: empty")
            continue
        llm = rows[0].get("llm", Path(f).stem)
        print(f"--- {llm} ({f}) ---")
        outs = per_record_outcomes(
            rows, no_plan_treatment=args.no_plan_treatment
        )
        if args.pairwise is not None:
            pairwise_cache.append((llm, outs))
        n_in  = len(rows)
        n_out = len(outs)
        n_no_plan = sum(1 for o in outs if o["is_no_plan"])
        if args.no_plan_treatment == "exclude":
            print(f"  (no-plan-treatment=exclude: dropped "
                  f"{n_in - n_out}/{n_in} rows; aggregating over {n_out})")
        elif n_no_plan:
            print(f"  (no-plan-treatment=fail: {n_no_plan} no_plan rows "
                  f"counted as repair failures)")
        m = aggregate(outs)
        ci = (bootstrap_cis(outs, B=args.bootstrap, seed=args.bootstrap_seed)
              if args.bootstrap > 0 else {})
        for k, v in m.items():
            if isinstance(v, float):
                if k in ci:
                    lo, hi = ci[k]
                    print(f"  {k:30s} {v:.3f}   95%% CI [{lo:.3f}, {hi:.3f}]")
                else:
                    print(f"  {k:30s} {v:.3f}")
            else:
                print(f"  {k:30s} {v}")
        if args.bootstrap > 0:
            print(f"  ({args.bootstrap} bootstrap resamples, seed={args.bootstrap_seed})")

        if args.show_timings:
            ts = timing_summary(rows)
            if ts is None:
                print("  (no timings_ms in this snapshot — re-run run_audit.py "
                      "to populate per-stage timings)")
            else:
                print("  per-stage audit timings (ms):")
                print(f"    {'stage':<10} {'n':>4} {'mean':>8} {'median':>8} {'p95':>8} {'max':>8}")
                for stage, st in ts.items():
                    print(f"    {stage:<10} {st['n']:>4} {st['mean']:>8.2f} "
                          f"{st['median']:>8.2f} {st['p95']:>8.2f} {st['max']:>8.2f}")

        if args.by_tag:
            buckets: dict[str, list[dict]] = defaultdict(list)
            for r in rows:
                for t in r.get("tags", []) or ["untagged"]:
                    buckets[t].append(r)
            for tag, sub in sorted(buckets.items()):
                m = metrics_for(sub, no_plan_treatment=args.no_plan_treatment)
                print(f"  ↳ tag={tag} (n={m['n']})  "
                      f"prereq={m['prereq_violation_rate']:.2f} "
                      f"compliant={m['compliant_rate']:.2f} "
                      f"edits={m['mean_edit_distance']:.2f}")
        print()

    # Pairwise paired-bootstrap matrix across all input snapshots.
    if args.pairwise is not None and len(pairwise_cache) >= 2:
        metric = args.pairwise
        B = args.bootstrap if args.bootstrap > 0 else 1000
        seed = args.bootstrap_seed
        print(f"=== Pairwise paired-bootstrap differences on {metric} "
              f"(B={B}, seed={seed}) ===")
        print(f"    Cell [row, col] = (row metric) − (col metric).")
        print(f"    '*' marks differences whose 95% CI excludes zero.\n")
        names = [n for n, _ in pairwise_cache]
        # Header
        header = " " * 30 + " ".join(f"{n[:18]:>22}" for n in names)
        print(header)
        for i, (name_i, outs_i) in enumerate(pairwise_cache):
            cells: list[str] = []
            for j, (name_j, outs_j) in enumerate(pairwise_cache):
                if i == j:
                    cells.append(f"{'—':>22}")
                    continue
                if len(outs_i) != len(outs_j):
                    cells.append(f"{'len mismatch':>22}")
                    continue
                res = pairwise_diff_ci(
                    outs_i, outs_j, metric_key=metric,
                    B=B, seed=seed,
                )
                star = "*" if res["excludes_zero"] else " "
                cells.append(
                    f"{res['diff_point']:+.3f}{star}[{res['lo']:+.2f},{res['hi']:+.2f}]"
                    .rjust(22)
                )
            print(f"{name_i[:28]:<30}" + " ".join(cells))
        print()


if __name__ == "__main__":
    main()
