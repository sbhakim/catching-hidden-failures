"""Similarity-vs-safety analysis for audited advising plans.

This experiment addresses the reviewer concern that "semantic similarity is
not enough" by comparing LLM outputs with a small set of verifier-clean
advisor-style reference plans. The similarity measure is intentionally cheap
and reproducible: TF-IDF cosine similarity over the raw generated text and the
canonical reference plan text. The safety measure is the audit layer's typed
violation count from the frozen audited JSONL snapshots.

Outputs:
  evaluation/metrics/similarity_vs_safety.csv
  evaluation/metrics/similarity_vs_safety_summary.txt
  New_Manuscript/Figure/similarity_vs_safety_scatter.{pdf,png}

Run with DB validation (recommended in the aurora env):
  python evaluation/similarity_vs_safety.py

Run without DB validation when using an env that has plotting deps but not
psycopg2, after references were already validated:
  python evaluation/similarity_vs_safety.py --skip-reference-validation
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import yaml
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from audit_layer.models import Plan, SemesterBlock  # noqa: E402


AUDITED_FILES = [
    ("Qwen2.5-7B", "qwen2_5_7b_full.jsonl"),
    ("Mistral-7B", "hf_mistral_7b_v03_full.jsonl"),
    ("Gemma-2-9B", "hf_gemma_2_9b_it_full.jsonl"),
    ("Gemma-3-12B", "hf_gemma_3_12b_it_full.jsonl"),
    ("DeepSeek-R1", "hf_deepseek_r1_0528_qwen3_8b_full.jsonl"),
    ("GPT-4o-mini", "gpt4o_mini_full.jsonl"),
    ("deepseek-chat", "deepseek_chat_full.jsonl"),
    ("Gemini 2.5 FL", "openrouter_gemini_2_5_flash_lite_full.jsonl"),
    ("Llama-3.3-70B", "openrouter_llama_3_3_70b_instruct_full.jsonl"),
    ("Claude Haiku 4.5", "claude_haiku_4_5_full.jsonl"),
]


def canonical_plan_text(blocks: list[dict]) -> str:
    """Stable text representation for a plan-like list of semester blocks."""
    lines: list[str] = []
    for block in blocks:
        semester = str(block.get("semester", "")).strip()
        courses = [str(c).upper() for c in block.get("courses", [])]
        if semester or courses:
            lines.append(f"{semester}: {' '.join(courses)}")
    return "\n".join(lines)


def reference_to_plan(ref: dict) -> Plan:
    return Plan(
        student_id=int(ref["student_id"]),
        program=str(ref["program"]).lower().replace("-", "_"),
        completed=[str(c).upper() for c in ref.get("completed", [])],
        blocks=[
            SemesterBlock(
                semester=str(b["semester"]),
                courses=[str(c).upper() for c in b.get("courses", [])],
            )
            for b in ref["reference_plan"]
        ],
        source="advisor_reference",
    )


def load_references(path: Path) -> dict[str, dict]:
    spec = yaml.safe_load(path.read_text())
    refs = {r["id"]: r for r in spec["references"]}
    if len(refs) != len(spec["references"]):
        raise ValueError("duplicate reference ids in reference_plans.yaml")
    return refs


def validate_references(refs: dict[str, dict]) -> None:
    from audit_layer import verifier

    bad: list[str] = []
    for qid, ref in refs.items():
        plan = reference_to_plan(ref)
        violations = verifier.verify(plan)
        if violations:
            details = "; ".join(
                f"{v.kind}:{v.course or '-'}:{v.detail}" for v in violations
            )
            bad.append(f"{qid}: {details}")
    if bad:
        raise SystemExit(
            "Reference plans must be verifier-clean before analysis:\n"
            + "\n".join(bad)
        )


def load_audited_rows(audited_dir: Path, wanted_ids: set[str]) -> list[dict]:
    rows: list[dict] = []
    for model_label, filename in AUDITED_FILES:
        path = audited_dir / filename
        if not path.exists():
            raise FileNotFoundError(path)
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["id"] not in wanted_ids:
                continue
            row = dict(row)
            row["model_label"] = model_label
            rows.append(row)
    return rows


def compute_query_local_similarities(rows: list[dict], refs: dict[str, dict]) -> None:
    by_qid: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_qid[row["id"]].append(row)

    vectorizer = TfidfVectorizer(
        lowercase=True,
        token_pattern=r"(?u)\b[\w_][\w_]+\b",
        ngram_range=(1, 2),
    )
    for qid, q_rows in by_qid.items():
        ref_text = canonical_plan_text(refs[qid]["reference_plan"])
        docs = [ref_text] + [r.get("raw") or "" for r in q_rows]
        matrix = vectorizer.fit_transform(docs)
        sims = cosine_similarity(matrix[0:1], matrix[1:]).ravel()
        for row, sim in zip(q_rows, sims):
            row["similarity"] = float(sim)
            row["reference_text"] = ref_text


def load_query_meta(path: Path) -> dict[str, dict]:
    spec = yaml.safe_load(path.read_text())
    return {q["id"]: q for q in spec["queries"]}


def enrich_rows(rows: list[dict], query_meta: dict[str, dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        violations = row.get("violations") or []
        kinds = Counter(v["kind"] for v in violations)
        parsed_blocks = row.get("parsed_plan", {}).get("blocks", [])
        repaired_blocks = (
            (row.get("repaired_plan") or {}).get("blocks", [])
            if row.get("repaired_plan")
            else []
        )
        meta = query_meta.get(row["id"], {})
        out.append({
            "query_id": row["id"],
            "category": row.get("category") or meta.get("category", ""),
            "expected_kind": row.get("expected_kind") or meta.get("expected_kind", ""),
            "model": row["model_label"],
            "llm": row.get("llm") or "",
            "similarity": row["similarity"],
            "raw_violation_count": len(violations),
            "parse_ok": bool(parsed_blocks),
            "no_plan": bool(kinds.get("no_plan_extracted")),
            "violation_kinds": ";".join(f"{k}:{v}" for k, v in sorted(kinds.items())),
            "parsed_plan_text": canonical_plan_text(parsed_blocks),
            "repaired_plan_text": canonical_plan_text(repaired_blocks),
            "raw": row.get("raw") or "",
        })
    return out


def pearson(xs: np.ndarray, ys: np.ndarray) -> float:
    if len(xs) < 2 or np.std(xs) == 0 or np.std(ys) == 0:
        return 0.0
    return float(np.corrcoef(xs, ys)[0, 1])


def rankdata(xs: np.ndarray) -> np.ndarray:
    order = np.argsort(xs)
    ranks = np.empty(len(xs), dtype=float)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        rank = (i + j) / 2.0 + 1.0
        ranks[order[i:j + 1]] = rank
        i = j + 1
    return ranks


def summarize(rows: list[dict]) -> dict:
    sims = np.array([r["similarity"] for r in rows], dtype=float)
    viol = np.array([r["raw_violation_count"] for r in rows], dtype=float)
    q75 = float(np.quantile(sims, 0.75))
    top = [r for r in rows if r["similarity"] >= q75]
    unsafe_top = [r for r in top if r["raw_violation_count"] > 0]
    safe_top = [r for r in top if r["raw_violation_count"] == 0]
    unsafe = [r for r in rows if r["raw_violation_count"] > 0]
    parse_fail = [r for r in rows if r["no_plan"]]
    by_model = {}
    for model in sorted({r["model"] for r in rows}):
        mrows = [r for r in rows if r["model"] == model]
        by_model[model] = {
            "n": len(mrows),
            "mean_similarity": sum(r["similarity"] for r in mrows) / len(mrows),
            "unsafe": sum(r["raw_violation_count"] > 0 for r in mrows),
            "mean_violations": sum(r["raw_violation_count"] for r in mrows) / len(mrows),
        }
    examples = sorted(
        unsafe_top,
        key=lambda r: (-r["similarity"], -r["raw_violation_count"], r["model"]),
    )[:8]
    return {
        "n": len(rows),
        "n_queries": len({r["query_id"] for r in rows}),
        "n_models": len({r["model"] for r in rows}),
        "unsafe": len(unsafe),
        "parse_fail": len(parse_fail),
        "q75_similarity": q75,
        "top_quartile_n": len(top),
        "top_quartile_unsafe": len(unsafe_top),
        "top_quartile_safe": len(safe_top),
        "pearson": pearson(sims, viol),
        "spearman": pearson(rankdata(sims), rankdata(viol)),
        "by_model": by_model,
        "examples": examples,
    }


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "query_id", "category", "expected_kind", "model", "llm",
        "similarity", "raw_violation_count", "parse_ok", "no_plan",
        "violation_kinds", "parsed_plan_text", "repaired_plan_text", "raw",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            record = dict(row)
            record["similarity"] = f"{record['similarity']:.6f}"
            writer.writerow(record)


def write_summary(summary: dict, path: Path) -> None:
    lines = [
        "Similarity-vs-safety summary",
        "============================",
        "",
        f"Rows: {summary['n']} ({summary['n_queries']} queries x {summary['n_models']} models)",
        f"Unsafe rows with >=1 raw audit violation: {summary['unsafe']}/{summary['n']}",
        f"Rows with NO_PLAN_EXTRACTED: {summary['parse_fail']}/{summary['n']}",
        f"Top-quartile similarity threshold: {summary['q75_similarity']:.3f}",
        (
            "Top-quartile similarity rows still unsafe: "
            f"{summary['top_quartile_unsafe']}/{summary['top_quartile_n']}"
        ),
        f"Pearson r(similarity, violations): {summary['pearson']:.3f}",
        f"Spearman rho(similarity, violations): {summary['spearman']:.3f}",
        "",
        "By model:",
    ]
    for model, vals in summary["by_model"].items():
        lines.append(
            f"  {model:<18} n={vals['n']:>2} "
            f"mean_sim={vals['mean_similarity']:.3f} "
            f"unsafe={vals['unsafe']:>2}/{vals['n']} "
            f"mean_viol={vals['mean_violations']:.2f}"
        )
    lines.extend(["", "High-similarity unsafe examples:"])
    for row in summary["examples"]:
        lines.append(
            f"  {row['query_id']:<10} {row['model']:<18} "
            f"sim={row['similarity']:.3f} "
            f"viol={row['raw_violation_count']:<2} "
            f"kinds={row['violation_kinds']}"
        )
    path.write_text("\n".join(lines) + "\n")


def write_plot(rows: list[dict], summary: dict, pdf_path: Path, png_path: Path) -> None:
    try:
        import matplotlib.pyplot as plt
        from matplotlib import rcParams
    except ImportError:
        print("matplotlib not installed; skipping scatter plot")
        return

    rcParams.update({
        "font.family": "serif",
        "font.serif": [
            "Linux Libertine O", "Linux Libertine", "Libertine",
            "Nimbus Roman", "Times New Roman", "Times", "DejaVu Serif",
        ],
        "mathtext.fontset": "stix",
        "font.size": 8.5,
        "axes.titlesize": 9.0,
        "axes.labelsize": 8.8,
        "xtick.labelsize": 8.2,
        "ytick.labelsize": 8.2,
        "legend.fontsize": 6.6,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "figure.dpi": 300,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.02,
    })

    models = [label for label, _ in AUDITED_FILES]
    palette = [
        "#1f77b4", "#d62728", "#2ca02c", "#17becf", "#9467bd",
        "#8c564b", "#e377c2", "#bcbd22", "#7f7f7f", "#ff7f0e",
    ]
    colors = dict(zip(models, palette))

    fig, ax = plt.subplots(figsize=(4.45, 2.75))
    for model in models:
        mrows = [r for r in rows if r["model"] == model]
        if not mrows:
            continue
        xs = [r["similarity"] for r in mrows]
        ys = [r["raw_violation_count"] for r in mrows]
        ax.scatter(
            xs, ys, s=18, color=colors[model], label=model,
            alpha=0.78, edgecolor="white", linewidth=0.3,
        )

    q75 = summary["q75_similarity"]
    ax.axvline(q75, color="#444444", linestyle="--", linewidth=0.8)
    ax.axhline(0.5, color="#444444", linestyle=":", linewidth=0.8)
    ax.text(
        q75 + 0.006, ax.get_ylim()[1] * 0.92,
        "top similarity quartile", fontsize=6.8, color="#333333",
    )

    for row in summary["examples"][:4]:
        ax.annotate(
            f"{row['query_id']}\\n{row['model'].split()[0]}",
            xy=(row["similarity"], row["raw_violation_count"]),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=5.8,
            color="#222222",
            arrowprops={"arrowstyle": "-", "linewidth": 0.35, "color": "#555555"},
        )

    ax.set_xlabel("TF-IDF similarity to verifier-clean reference plan")
    ax.set_ylabel("Raw audit violations")
    ax.set_xlim(left=0.0, right=min(1.02, max(r["similarity"] for r in rows) + 0.08))
    ax.set_ylim(bottom=-0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="#dddddd", linewidth=0.45)
    ax.legend(
        ncol=2, frameon=False, loc="upper left",
        bbox_to_anchor=(1.01, 1.02), borderaxespad=0.0,
        handletextpad=0.25, columnspacing=0.7,
    )
    fig.tight_layout()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(pdf_path)
    fig.savefig(png_path)
    print(f"wrote {pdf_path}")
    print(f"wrote {png_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--references",
        type=Path,
        default=ROOT / "evaluation" / "reference_plans.yaml",
    )
    ap.add_argument(
        "--audited-dir",
        type=Path,
        default=ROOT / "evaluation" / "audited",
    )
    ap.add_argument(
        "--queries",
        type=Path,
        default=ROOT / "evaluation" / "queries.yaml",
    )
    ap.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "evaluation" / "metrics" / "similarity_vs_safety.csv",
    )
    ap.add_argument(
        "--summary",
        type=Path,
        default=ROOT / "evaluation" / "metrics" / "similarity_vs_safety_summary.txt",
    )
    ap.add_argument(
        "--figure-pdf",
        type=Path,
        default=REPO_ROOT / "New_Manuscript" / "Figure" / "similarity_vs_safety_scatter.pdf",
    )
    ap.add_argument(
        "--figure-png",
        type=Path,
        default=REPO_ROOT / "New_Manuscript" / "Figure" / "similarity_vs_safety_scatter.png",
    )
    ap.add_argument(
        "--skip-reference-validation",
        action="store_true",
        help="Skip live verifier validation of references; useful in plotting envs without psycopg2.",
    )
    args = ap.parse_args()

    refs = load_references(args.references)
    if not args.skip_reference_validation:
        validate_references(refs)
        print(f"validated {len(refs)} reference plans")

    query_meta = load_query_meta(args.queries)
    rows = load_audited_rows(args.audited_dir, set(refs))
    expected_n = len(refs) * len(AUDITED_FILES)
    if len(rows) != expected_n:
        raise SystemExit(f"expected {expected_n} rows, found {len(rows)}")
    compute_query_local_similarities(rows, refs)
    enriched = enrich_rows(rows, query_meta)
    summary = summarize(enriched)
    write_csv(enriched, args.csv)
    write_summary(summary, args.summary)
    write_plot(enriched, summary, args.figure_pdf, args.figure_png)
    print(f"wrote {args.csv}")
    print(f"wrote {args.summary}")
    print(
        "top-quartile unsafe: "
        f"{summary['top_quartile_unsafe']}/{summary['top_quartile_n']}; "
        f"pearson={summary['pearson']:.3f}; spearman={summary['spearman']:.3f}"
    )


if __name__ == "__main__":
    main()
