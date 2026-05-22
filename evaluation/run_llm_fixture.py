"""Run an HF model over the v1 46-query benchmark using a hardcoded
student-context fixture instead of a live PostgreSQL DB.

Mirrors the JSONL schema of `run_llm.py` so the output is a drop-in
substitute that downstream `run_audit.py` can consume. The fixture
contains the three seeded students whose contexts appear in the v1
audited snapshots; all 46 v1 queries reference one of these three.

Usage:
    python evaluation/run_llm_fixture.py \
        --queries evaluation/queries.yaml \
        --llm hf:deepseek-r1-0528-qwen3-8b \
        --out evaluation/runs/hf_deepseek_r1_0528_qwen3_8b_full.jsonl \
        --v1-only
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.run_llm import RUNNERS

# Canonical student contexts (program, completed_course_ids), extracted
# from the v1 audited JSONL snapshots so this runner produces records
# directly comparable to the paper's other-model results.
FIXTURE: dict[int, tuple[str, list[str]]] = {
    4942915: ("CS-BS", ["CGS_1920", "COP_2210", "MAD_2104", "COT_3100"]),
    6564893: ("CS-BS-SDD", ["CGS_1920", "COP_2210", "MAD_2104"]),
    7689039: ("MS-CS", [
        "CEN_5011", "COP_5614", "COP_5725", "CEN_5120", "COP_6611",
        "CAP_5602", "CAP_5768", "TCN_5440", "TCN_6275", "TCN_5010",
        "CAP_5640", "CEN_5076", "CIS_6930", "TCN_6270", "CAP_5610",
        "COT_5520", "TCN_5640", "COT_6936", "CEN_5064", "CIS_5372",
        "CDA_6939", "CIS_5346", "CEN_6075", "COT_6931", "CDA_5655",
        "COT_5310", "TCN_5445", "COT_5428", "CNT_5109", "TCN_5080",
        "TCN_5060", "CAP_5771", "CNT_5415", "CAP_5627", "TCN_5030",
        "CNT_6208", "CIS_5373", "COP_6727", "COT_6421", "TCN_6880",
        "CEN_6070", "COP_5621", "CAP_5738", "TCN_6215", "CIS_5370",
        "COP_6556", "TCN_5421", "TCN_6420", "CIS_5208", "CEN_5079",
        "TCN_6430", "TCN_6260", "CNT_6207", "CAP_6778", "TCN_6450",
        "CIS_5374",
    ]),
}

# v1 paper-snapshot query IDs (the 46 originally evaluated; v2 routine
# extension ids ist_06..ist_08, ilt_06..ilt_08, isk_04..isk_06,
# iex_04..iex_06, iem_05..iem_07 are EXCLUDED unless --no-v1-only).
V2_EXT_IDS = {
    "ist_06", "ist_07", "ist_08",
    "ilt_06", "ilt_07", "ilt_08",
    "isk_04", "isk_05", "isk_06",
    "iex_04", "iex_05", "iex_06",
    "iem_05", "iem_06", "iem_07",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", required=True, type=Path)
    ap.add_argument("--llm", required=True, choices=list(RUNNERS))
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=None,
                    help="Optional cap on number of queries to run.")
    ap.add_argument("--v1-only", action="store_true",
                    help="Run only the 46 v1 paper-snapshot queries "
                         "(default: run every query in the YAML).")
    args = ap.parse_args()

    spec = yaml.safe_load(args.queries.read_text())
    all_q = spec["queries"]
    if args.v1_only:
        qs = [q for q in all_q if q["id"] not in V2_EXT_IDS]
    else:
        qs = all_q
    if args.limit:
        qs = qs[: args.limit]

    runner = RUNNERS[args.llm]
    args.out.parent.mkdir(parents=True, exist_ok=True)

    t_start = time.time()
    print(f"# fixture runner: {args.llm}", flush=True)
    print(f"# queries: {len(qs)}", flush=True)
    print(f"# out: {args.out}", flush=True)
    print(f"# started: {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    with args.out.open("w") as f:
        for i, q in enumerate(qs, 1):
            uid = q["user_id"]
            if uid not in FIXTURE:
                print(f"[{i:>2}/{len(qs)}] {q['id']:12s}  "
                      f"SKIP: user_id={uid} not in fixture", flush=True)
                continue
            program, completed = FIXTURE[uid]
            t0 = time.time()
            try:
                raw = runner(q["query"], program, completed)
                err = None
            except Exception as e:
                raw = ""
                err = f"{type(e).__name__}: {e}"
            rec = {
                "id":            q["id"],
                "user_id":       uid,
                "program":       program,
                "completed":     completed,
                "query":         q["query"],
                "tags":          q.get("tags", []),
                "category":      q.get("category"),
                "expected_kind": q.get("expected_kind"),
                "llm":           args.llm,
                "raw":           raw,
                "error":         err,
                "latency_s":     round(time.time() - t0, 2),
            }
            f.write(json.dumps(rec) + "\n")
            f.flush()  # so a tail -f log is always live
            elapsed = round(time.time() - t_start, 1)
            print(f"[{i:>2}/{len(qs)}] {q['id']:12s}  "
                  f"{rec['latency_s']:>6}s   total={elapsed}s"
                  + (f"  ERROR: {err}" if err else ""),
                  flush=True)

    total = round(time.time() - t_start, 1)
    print(f"# finished: {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"# wall time: {total}s ({total/60:.1f} min)", flush=True)


if __name__ == "__main__":
    main()
