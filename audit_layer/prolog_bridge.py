"""Thin SWI-Prolog bridge.

Invokes `swipl` once per query as a subprocess (~150–300 ms per call). The
trade is statelessness: every check sees a fresh KB, so asserted facts from
one query cannot leak into the next. That property is what makes the
leave-one-out ablation honest and lets parallel callers run safely.

End-to-end audit latency (~1–3 s per plan) is dominated by subprocess
startup; an embedded session via pyswip is a v2 optimization.
"""
from __future__ import annotations
import os, re, subprocess
from pathlib import Path

PROLOG_KB_DIR = Path(__file__).resolve().parent.parent / "prolog_kb"
RULES_LOADER = PROLOG_KB_DIR / "rules_loader.pl"

_COURSE_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PROG_RX = re.compile(r"[A-Za-z0-9_]+")


class PrologError(RuntimeError):
    """Raised when SWI-Prolog reports a load or query error."""


def norm_course(s: str) -> str:
    """'MAC-2311' / 'mac_2311' / 'MAC 2311' -> 'mac2311'"""
    s = re.sub(r"[^A-Za-z0-9]", "", s.lower())
    return ("c_" + s) if not s or not s[0].isalpha() else s


def norm_prog(s: str) -> str:
    """'CS-BS' -> 'cs_bs'"""
    if not s:
        return ""
    matches = _PROG_RX.findall(s.lower().replace(" ", "_").replace("-", "_"))
    return matches[0] if matches else ""


def denorm_course(atom: str) -> str:
    """'cop3530' -> 'COP_3530'; non-course atoms are uppercased as-is."""
    m = re.fullmatch(r"([a-zA-Z]{2,4})(\d{3,4})", atom)
    if m:
        return f"{m.group(1).upper()}_{m.group(2)}"
    return atom.upper()


def atoms_from_list_literal(txt: str) -> list[str]:
    return [m.lower() for m in _COURSE_RX.findall(txt)]


def run_prolog(goal: str, *, capture_trace: bool = False,
               strict: bool = True) -> tuple[str, str]:
    """Run a Prolog goal once. Returns (stdout, stderr).

    capture_trace=True wraps the goal in a meta-call that emits the resolution
    trace via leash(-all) + trace/0. Used by the explainer.
    """
    if capture_trace:
        wrapped = f"(leash(-all), trace, ({goal}), notrace)"
    else:
        wrapped = goal
    proc = subprocess.run(
        ["swipl", "-q", "-s", str(RULES_LOADER), "-g", wrapped, "-t", "halt"],
        cwd=str(PROLOG_KB_DIR),
        capture_output=True,
        text=True,
        timeout=30,
    )
    stdout, stderr = proc.stdout.strip(), proc.stderr.strip()
    if strict and (proc.returncode != 0 or "ERROR:" in stderr):
        raise PrologError(stderr or stdout or f"swipl exited {proc.returncode}")
    return stdout, stderr


def consult_program(prog_atom: str) -> str:
    """Build the consult prefix for a given program. Returns the snippet."""
    if not prog_atom:
        return ""
    folder = "graduate" if prog_atom.startswith(("ms_", "phd_")) else "undergraduate"
    path = f"flowchart_rules/{folder}/{prog_atom}_rules.pl"
    return f"consult('{path}'), "


def prereqs_of(course: str, program: str | None = None) -> list[str]:
    """Return immediate hard prereqs and unsatisfied alternative options."""
    atom = norm_course(course)
    consult = consult_program(norm_prog(program)) if program else ""
    out, _ = run_prolog(
        f"({consult}"
        f" findall(P,user:prerequisite({atom},P),Hard),"
        f" findall(A,(user:one_of_prereqs_fact({atom},Opts),member(A,Opts)),Alts),"
        f" append(Hard,Alts,L),sort(L,S),format('~q',[S]))"
    )
    return [denorm_course(a) for a in atoms_from_list_literal(out)]


def is_eligible(taken: list[str], target: str, program: str | None = None) -> bool:
    """Check whether `target` is takeable given completed courses. Optionally
    consult a program file first so rule scope matches.

    The goal uses `forall + memberchk` rather than full backtracking: we want
    a yes/no decision, not a witness, so this is both faster and easier to
    parse. Three conjuncts are required to hold simultaneously: every hard
    prereq satisfied, at least one alternative for each `one_of_prereqs_fact`
    satisfied, and every corequisite satisfied.
    """
    consult = consult_program(norm_prog(program)) if program else ""
    taken_lit = "[" + ",".join(norm_course(c) for c in taken) + "]"
    tgt = norm_course(target)
    goal = (
        f"({consult}"
        f" (forall(user:prerequisite({tgt},P),memberchk(P,{taken_lit})),"
        f"  forall(user:one_of_prereqs_fact({tgt},Opts),"
        f"         (member(Alt,Opts),memberchk(Alt,{taken_lit}))),"
        f"  forall(user:corequisite({tgt},Co),memberchk(Co,{taken_lit}))"
        f"  -> write(yes); write(no)))"
    )
    try:
        out, _ = run_prolog(goal)
    except PrologError:
        return False
    return out.strip() == "yes"


def in_program(course: str, program: str | None = None) -> bool:
    """Whether a course appears in the consulted program flowchart rules."""
    if not program:
        return True
    consult = consult_program(norm_prog(program))
    tgt = norm_course(course)
    try:
        out, _ = run_prolog(
            f"({consult} (validator_rules:in_program({tgt}) -> write(yes); write(no)))"
        )
    except PrologError:
        return False
    return out.strip() == "yes"
