"""Tool-calling LLM baseline for the catching-hidden-failures benchmark.

Janki's May 19 email called this experiment "central": *"the tool-calling
LLM question is too central to defer as future work. It is arguably the
most important experiment for the paper's thesis."*

This script runs the same v1 advising queries through a function-calling
LLM that has *direct, structured access* to the curriculum database via
three tools (each backed by the same DB / Prolog helpers the audit-layer
verifier uses, so the LLM and the verifier are looking at identical
ground truth):

    - get_course_info(course_id)         catalog + credits + prereqs
    - check_prereq(course_id, completed) yes/no eligibility given history
    - list_program_courses(program)      all courses attached to a program

The LLM is encouraged (but not forced) to call these before committing
to a plan. We record:

    - tool_calls   : list of {name, args, result_summary} per query
    - n_tool_calls : iteration count (bounded by --max-iters; default 10)
    - raw          : the final assistant message (Markdown plan)

The output JSONL is schema-identical to `run_llm.py`'s output so the
existing `run_audit.py` audits it without changes. This keeps the
experiment scoped: only the *generator* changes; the audit layer, the
benchmark, and the metrics pipeline stay frozen.

CHECK-AND-BALANCE DESIGN
~~~~~~~~~~~~~~~~~~~~~~~~

Reading the codebase under the same assumptions a reviewer would, this
runner deliberately:

    1. Does *not* touch `run_llm.py`. Existing 10-model runs reproduce
       bit-for-bit.
    2. Defaults to ``--smoke`` mode (one query) so a fresh checkout
       proves the tools work before any cost is incurred.
    3. Bounds the tool-call loop with ``--max-iters`` so a runaway model
       cannot rack up an unbounded API bill.
    4. Calls the same Prolog `is_eligible` predicate the verifier uses
       for prereq checks, so any false negatives the LLM sees are also
       false negatives the verifier would see.
    5. Records every tool call (name + args + truncated result) in the
       JSONL so the experiment is auditable end-to-end.
    6. Skips a query gracefully if any tool errors; the row carries
       ``error`` so re-runs can resume.

Usage:
    # 1-query smoke test (default; ~3-5 s, ~$0.001):
    python evaluation/run_tool_calling.py \
        --queries evaluation/queries.yaml \
        --llm gpt-4o-mini \
        --out evaluation/runs/tool_calling/smoke.jsonl

    # 5-query validation slice:
    python evaluation/run_tool_calling.py \
        --queries evaluation/queries.yaml \
        --llm gpt-4o-mini \
        --out evaluation/runs/tool_calling/limit5.jsonl \
        --limit 5

    # full v1 run (only after the slice looks reasonable):
    python evaluation/run_tool_calling.py \
        --queries evaluation/queries.yaml \
        --llm gpt-4o-mini \
        --out evaluation/runs/tool_calling/gpt4o_mini_full.jsonl \
        --v1-only

Then audit through the standard pipeline:
    python evaluation/run_audit.py \
        --in  evaluation/runs/tool_calling/gpt4o_mini_full.jsonl \
        --out evaluation/audited/tool_calling/gpt4o_mini_full.jsonl
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Same fixture used by run_llm_fixture.py so the LLM sees the identical
# student context our other runs use. Imported, not duplicated.
from evaluation.run_llm_fixture import FIXTURE, V2_EXT_IDS

# DB + Prolog helpers — exact same code paths as the verifier.
from audit_layer import db as audit_db
from audit_layer import prolog_bridge


# ───────────────────────── Tool implementations ─────────────────────────

# NOTE: ``program`` is a required argument to ``tool_get_course_info`` and
# ``tool_check_prereq`` because prereq facts live in the program-specific
# Prolog rule file. Calling ``prolog_bridge.prereqs_of`` or ``is_eligible``
# without a program atom silently returns an empty/permissive result, which
# would mislead the LLM into thinking every course has no prerequisites.

def tool_get_course_info(course_id: str, program: str) -> dict:
    """Look up a course. Returns existence + credits + prereqs (hard +
    one-of-alternatives, exactly as the verifier sees them).

    ``program`` is required because prereq facts are loaded from the
    program-specific Prolog rule file (CS-BS, CS-BS-SDD, MS-CS, ...).
    """
    cid = course_id.strip().upper()
    if not audit_db.course_exists(cid):
        return {"course_id": cid, "exists_in_catalog": False}
    credits = audit_db.credits_of(cid)
    prereqs = prolog_bridge.prereqs_of(cid, program=program)
    return {
        "course_id": cid,
        "exists_in_catalog": True,
        "credits": credits,
        "prereqs": prereqs,
        "program_context": program,
    }


def tool_check_prereq(course_id: str, completed_courses: list[str], program: str) -> dict:
    """Use the *same* ``is_eligible`` predicate the verifier uses (with
    the same program rule file loaded), so the LLM sees identical
    ground truth. Returns yes/no, plus the list of immediate missing
    prereqs for diagnostic value."""
    cid = course_id.strip().upper()
    if not audit_db.course_exists(cid):
        return {"course_id": cid, "satisfied": False,
                "reason": "course is not in the catalog"}
    completed_norm = [c.strip().upper() for c in (completed_courses or [])]
    satisfied = prolog_bridge.is_eligible(completed_norm, cid, program=program)
    prereqs = prolog_bridge.prereqs_of(cid, program=program)
    missing = [p for p in prereqs if p not in completed_norm]
    return {
        "course_id": cid,
        "satisfied": satisfied,
        "completed_count": len(completed_norm),
        "missing_prereqs": missing,
        "program_context": program,
    }


def tool_list_program_courses(program: str) -> dict:
    """Return all courses attached to the program (truncated to 200 IDs
    to fit comfortably inside model context windows)."""
    pid = program.strip()
    courses = audit_db.program_courses(pid)
    return {
        "program": pid,
        "n_courses": len(courses),
        "courses": courses[:200],
        "truncated": len(courses) > 200,
    }


TOOL_FUNCTIONS = {
    "get_course_info":     lambda args: tool_get_course_info(args["course_id"], args["program"]),
    "check_prereq":        lambda args: tool_check_prereq(args["course_id"], args.get("completed_courses", []), args["program"]),
    "list_program_courses":lambda args: tool_list_program_courses(args["program"]),
}


# ───────────────────────── OpenAI tools schema ──────────────────────────

OPENAI_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_course_info",
            "description": (
                "Look up a course by its catalog ID (e.g. 'COP_3530') in "
                "the context of the student's degree program. Returns "
                "whether the course exists, its credit value, and its "
                "prerequisites (from the program's rule file). Use this "
                "to verify that a course you are about to recommend is "
                "in the catalog."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {
                        "type": "string",
                        "description": "Catalog course ID, e.g. 'COP_3530'.",
                    },
                    "program": {
                        "type": "string",
                        "description": "Student's degree program, e.g. 'CS-BS'. REQUIRED — prerequisites are defined per program.",
                    },
                },
                "required": ["course_id", "program"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_prereq",
            "description": (
                "Check whether the prerequisites of a target course are "
                "satisfied given a list of already-completed courses, "
                "evaluated under the student's program rule file. "
                "Returns satisfied=true/false and a list of immediate "
                "missing prerequisites. Use this before scheduling a "
                "course in a given semester."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {"type": "string"},
                    "completed_courses": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "All course IDs the student has already completed (case-insensitive).",
                    },
                    "program": {
                        "type": "string",
                        "description": "Student's degree program, e.g. 'CS-BS'. REQUIRED — prereq satisfaction is checked against the program's rule file.",
                    },
                },
                "required": ["course_id", "completed_courses", "program"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_program_courses",
            "description": (
                "Return all courses attached to a degree program "
                "(e.g. 'CS-BS', 'CS-BS-SDD', 'MS-CS'). Use this to "
                "constrain recommendations to courses that count toward "
                "the student's program."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "program": {"type": "string"},
                },
                "required": ["program"],
            },
        },
    },
]


# ──────────────────────── Prompt construction ────────────────────────────

SYSTEM_PROMPT = (
    "You are an academic advising assistant. You have access to three "
    "tools that query a curriculum database directly. Use them to verify "
    "that every course you recommend (a) exists in the catalog, (b) has "
    "its prerequisites satisfied by the student's completed courses at "
    "the start of the semester you place it in, and (c) is listed for "
    "the student's program. Do not recommend courses you have not "
    "verified.\n\n"
    "IMPORTANT: prerequisite facts are stored per program. You MUST pass "
    "the student's `program` argument to both `get_course_info` and "
    "`check_prereq` every time you call them, or the tool will return "
    "empty/incorrect prerequisite data and your plan will violate hidden "
    "prereq chains. The program is given to you in the user message.\n\n"
    "When you are ready, return your plan as a Markdown bullet list with "
    "one bullet per semester, in the format:\n"
    "- **Fall 2026**: COURSE_1, COURSE_2\n"
    "- **Spring 2027**: COURSE_3\n"
    "Use the exact catalog course-ID format (e.g. COP_3530, MAD_2104)."
)


def _build_user_prompt(query: str, program: str, completed: list[str]) -> str:
    completed_str = ", ".join(completed) if completed else "(none)"
    return (
        f"Student program: {program}\n"
        f"Completed courses: {completed_str}\n\n"
        f"Advising query: {query}"
    )


# ───────────────────────── OpenAI driver loop ───────────────────────────

def run_openai_with_tools(
    query: str,
    program: str,
    completed: list[str],
    *,
    model: str = "gpt-4o-mini",
    max_iters: int = 10,
    verbose: bool = False,
) -> dict:
    """Run one query through the tool-calling loop.

    Returns a dict with keys:
        raw          : final assistant message (the plan)
        tool_calls   : list of {name, args, result_summary}
        n_tool_calls : len(tool_calls)
        error        : None or a string describing why the run aborted
    """
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": _build_user_prompt(query, program, completed)},
    ]

    tool_log: list[dict] = []
    for it in range(max_iters):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=OPENAI_TOOLS_SCHEMA,
                tool_choice="auto",
                temperature=0.2,
            )
        except Exception as e:
            return {"raw": "", "tool_calls": tool_log, "n_tool_calls": len(tool_log),
                    "error": f"openai_api: {e}"}

        msg = resp.choices[0].message
        # If the model returned text and no tool calls, we're done.
        if not msg.tool_calls:
            return {"raw": msg.content or "",
                    "tool_calls": tool_log,
                    "n_tool_calls": len(tool_log),
                    "error": None}

        # Append the assistant message (so subsequent tool replies are linked).
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })

        # Execute each tool call and append its result.
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            fn = TOOL_FUNCTIONS.get(name)
            if fn is None:
                result = {"error": f"unknown tool {name}"}
            else:
                try:
                    result = fn(args)
                except Exception as e:
                    result = {"error": f"tool_{name}_failed: {e}"}
            # Truncate large result objects in the log to keep JSONL small.
            log_result = result
            if isinstance(result, dict) and "courses" in result and isinstance(result["courses"], list):
                log_result = {**result, "courses": result["courses"][:10],
                              "_courses_truncated_in_log_to_10": True}
            tool_log.append({"name": name, "args": args, "result": log_result})
            if verbose:
                print(f"    [tool] {name}({args}) -> {str(result)[:120]}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

    # If we exit the loop without a final assistant message, mark it.
    return {"raw": "", "tool_calls": tool_log, "n_tool_calls": len(tool_log),
            "error": f"max_iters_reached:{max_iters}"}


# ───────────────────────────── Driver ───────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=Path, required=True,
                    help="Same queries.yaml the other runners consume.")
    ap.add_argument("--llm", default="gpt-4o-mini",
                    help="Hosted model identifier with OpenAI-compatible function calling.")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--smoke", action="store_true",
                    help="Run a single hand-picked query and print results. Default if --limit/--v1-only absent.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Run only the first N queries (after --v1-only filter, if given).")
    ap.add_argument("--v1-only", action="store_true",
                    help="Restrict to the v1 paper-snapshot 46-query IDs.")
    ap.add_argument("--max-iters", type=int, default=10,
                    help="Hard cap on tool-call iterations per query.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not (args.smoke or args.limit or args.v1_only):
        # Implicit safety: a fresh invocation without an explicit scope
        # defaults to smoke mode rather than the full 46-query run.
        args.smoke = True
        print("[info] no --smoke/--limit/--v1-only given; defaulting to --smoke.")

    raw = yaml.safe_load(args.queries.read_text())
    queries = raw["queries"] if isinstance(raw, dict) else raw

    if args.v1_only:
        queries = [q for q in queries if q["id"] not in V2_EXT_IDS]
    if args.smoke:
        queries = queries[:1]
    elif args.limit:
        queries = queries[: args.limit]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    n = len(queries)
    print(f"[info] running {n} query/queries through {args.llm} with tool calling")
    t0 = time.perf_counter()
    n_ok = n_err = 0
    n_tool_calls_total = 0

    with args.out.open("w") as fout:
        for i, q in enumerate(queries, 1):
            uid = q["user_id"]
            if uid not in FIXTURE:
                print(f"[skip] {q['id']}: user_id {uid} not in fixture")
                continue
            program, completed = FIXTURE[uid]

            t_q = time.perf_counter()
            out = run_openai_with_tools(
                query=q["query"],
                program=program,
                completed=list(completed),
                model=args.llm,
                max_iters=args.max_iters,
                verbose=args.verbose,
            )
            latency = round(time.perf_counter() - t_q, 2)

            record = {
                "id": q["id"],
                "user_id": uid,
                "program": program,
                "completed": list(completed),
                "query": q["query"],
                "tags": q.get("tags", []),
                "category": q.get("category"),
                "expected_kind": q.get("expected_kind"),
                "llm": f"tool_calling:{args.llm}",
                "raw": out["raw"],
                "tool_calls": out["tool_calls"],
                "n_tool_calls": out["n_tool_calls"],
                "error": out["error"],
                "latency_s": latency,
            }
            fout.write(json.dumps(record) + "\n")
            fout.flush()
            n_tool_calls_total += out["n_tool_calls"]
            if out["error"]:
                n_err += 1
                print(f"[{i:2d}/{n}] {q['id']:14s} ERROR={out['error'][:50]}  latency={latency}s")
            else:
                n_ok += 1
                print(f"[{i:2d}/{n}] {q['id']:14s} tool_calls={out['n_tool_calls']:2d}  latency={latency}s")

    elapsed = round(time.perf_counter() - t0, 1)
    print(f"\nDone in {elapsed}s. ok={n_ok}  err={n_err}  total_tool_calls={n_tool_calls_total}")
    print(f"Output: {args.out}")
    if args.smoke and n_ok:
        print(f"\n[smoke output sample] first record's raw plan:\n")
        with args.out.open() as fr:
            first = json.loads(fr.readline())
            print(first["raw"][:600])
            print("\n[tool calls made]")
            for tc in first["tool_calls"][:6]:
                print(f"  - {tc['name']}({tc['args']})")


if __name__ == "__main__":
    main()
