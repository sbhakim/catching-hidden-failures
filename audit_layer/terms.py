"""Explicit term-order contract; no inferred years or silent reordering."""
import re


def ordered_terms(plan):
    keys = []
    for block in plan.blocks:
        match = re.fullmatch(r"(Spring|Summer|Fall|Autumn) (\d{4})", block.semester)
        if not match:
            return False
        keys.append((int(match[2]), {"Spring": 0, "Summer": 1, "Fall": 2, "Autumn": 2}[match[1]]))
    return bool(keys) and all(a < b for a, b in zip(keys, keys[1:]))
