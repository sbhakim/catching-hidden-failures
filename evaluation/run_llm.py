"""Collect raw plans from each LLM under test.

Pluggable runners keyed by name. Each runner takes (query, program,
completed) and returns a string suitable for the audit-layer's
plan_parser. API keys are read from env (ANTHROPIC_API_KEY,
OPENAI_API_KEY); BASELINE_LLM_URL points at any HTTP-served baseline
generator (e.g. a curriculum-grounded advising service to compare
against).

Usage:
    python evaluation/run_llm.py --queries evaluation/queries.yaml \
                                 --llm ollama:qwen2.5:7b \
                                 --out evaluation/runs/qwen2_5_7b_full.jsonl
"""
from __future__ import annotations
import argparse, json, os, sys, time
from functools import lru_cache
from pathlib import Path
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit_layer import db


# ───────────────────────── runners ──────────────────────────────────
def _build_prompt(query: str, program: str, completed: list[str]) -> str:
    completed_str = ", ".join(sorted(completed)) or "(none)"
    return (
        f"You are an academic advisor. The student is in program {program} and "
        f"has completed: {completed_str}.\n\n"
        f"Student question: {query}\n\n"
        f"Reply with a plan as a markdown bullet list of the form:\n"
        f"  - **Fall 2026**: COP_3530, MAD_2104\n"
        f"  - **Spring 2027**: COP_3337, ...\n"
        f"Use only one bullet per semester. Do not add commentary outside the list."
    )


def run_anthropic(query: str, program: str, completed: list[str], model: str) -> str:
    import anthropic  # lazy import
    client = anthropic.Anthropic()
    prompt = _build_prompt(query, program, completed)
    msg = client.messages.create(
        model=model,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def run_openrouter(query: str, program: str, completed: list[str], model: str) -> str:
    """OpenRouter's API is OpenAI-compatible. Routes a single request to any
    of the hosted models OpenRouter aggregates (Google Gemini, Anthropic
    Claude, Meta Llama, etc.). Model IDs use the ``provider/model-name``
    format (e.g. ``google/gemini-2.5-flash-lite``).
    """
    import os
    from openai import OpenAI
    client = OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
    )
    prompt = _build_prompt(query, program, completed)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    return resp.choices[0].message.content or ""


def run_deepseek_api(query: str, program: str, completed: list[str], model: str) -> str:
    """DeepSeek's hosted API at api.deepseek.com is OpenAI-compatible.

    We reuse the OpenAI SDK with a custom ``base_url`` and the
    ``DEEPSEEK_API_KEY`` env var. ``deepseek-chat`` (V4 Flash non-thinking)
    is the cheap default; ``deepseek-reasoner`` activates the thinking
    mode and produces ``<think>...</think>`` blocks that our parser
    already strips before plan extraction.
    """
    import os
    from openai import OpenAI
    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
    )
    prompt = _build_prompt(query, program, completed)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    return resp.choices[0].message.content or ""


def run_openai(query: str, program: str, completed: list[str], model: str) -> str:
    from openai import OpenAI
    client = OpenAI()
    prompt = _build_prompt(query, program, completed)
    # GPT-5 family (e.g. gpt-5, gpt-5-nano) rejects `max_tokens` and requires
    # `max_completion_tokens`. The newer parameter is also accepted by
    # earlier chat-completion models, so we use it uniformly. GPT-5
    # reasoning models also spend a large fraction of the budget on
    # internal reasoning tokens that never reach `choices[0].message.content`
    # unless we either raise the cap substantially or instruct the model to
    # minimise reasoning. We do both: 2000-token budget + minimal-effort
    # reasoning, applied only to gpt-5* models so other model families
    # keep their existing decoding contract.
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": 2000,
    }
    if model.startswith("gpt-5"):
        kwargs["reasoning_effort"] = "minimal"
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def run_baseline_service(query: str, program: str, completed: list[str], model: str = None) -> str:
    """POST a query to an external baseline advising service.

    Used to evaluate the audit layer against a curriculum-grounded LLM
    pipeline that already enforces its own rules — set BASELINE_LLM_URL
    to its /recommend_raw endpoint.
    """
    import httpx
    url = os.getenv("BASELINE_LLM_URL", "http://127.0.0.1:8011/recommend_raw")
    r = httpx.post(url, json={"q": query}, timeout=120.0)
    r.raise_for_status()
    return r.json().get("advice", "")


def run_ollama(query: str, program: str, completed: list[str], model: str) -> str:
    """Hit a local Ollama server (default http://127.0.0.1:11434).
    Install: https://ollama.com/download   then `ollama pull <model>`.
    Free, offline, reproducible. Recommended for paper experiments.

    Decoding overrides via env (used by the temperature-sweep sensitivity
    analysis; absent → published defaults so existing snapshots reproduce):
        OLLAMA_TEMPERATURE  float, default 0.2
        OLLAMA_SEED         int,   default unset (Ollama draws its own)
    """
    import httpx
    url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
    prompt = _build_prompt(query, program, completed)
    options: dict[str, object] = {
        "num_predict": 600,
        "temperature": float(os.getenv("OLLAMA_TEMPERATURE", "0.2")),
    }
    seed_env = os.getenv("OLLAMA_SEED")
    if seed_env is not None and seed_env != "":
        options["seed"] = int(seed_env)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": options,
    }
    r = httpx.post(url, json=payload, timeout=300.0)
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "")


@lru_cache(maxsize=4)
def _load_hf_model(model_id: str):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    tok = AutoTokenizer.from_pretrained(
        model_id,
        trust_remote_code=True,
        local_files_only=True,
    )
    quant = None
    if torch.cuda.is_available():
        quant = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
        )
    m = AutoModelForCausalLM.from_pretrained(
        model_id,
        device_map="auto",
        quantization_config=quant,
        dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        trust_remote_code=True,
        local_files_only=True,
    )
    m.eval()
    return tok, m


def run_hf_local(query: str, program: str, completed: list[str], model: str,
                 max_new_tokens: int = 600) -> str:
    """Direct transformers load for cached HF models.

    ``max_new_tokens`` defaults to 600 (used for v1 paper snapshots so they
    reproduce bit-for-bit). Reasoning models such as DeepSeek-R1 need a
    larger budget because <think>...</think> blocks consume tokens before
    the actual plan is emitted; pass e.g. 1500 for those.
    """
    import torch

    tok, m = _load_hf_model(model)
    prompt = _build_prompt(query, program, completed)
    messages = [{"role": "user", "content": prompt}]
    if getattr(tok, "chat_template", None):
        prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tok(prompt, return_tensors="pt").to(m.device)
    with torch.no_grad():
        out = m.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                          pad_token_id=tok.eos_token_id)
    return tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)


# Pluggable runner registry. Add custom models by extending this dict.
RUNNERS = {
    # ── commercial (cost-incurring, optional) ─────────────────────────
    "claude-haiku-4-5":  lambda q, p, c: run_anthropic(q, p, c, "claude-haiku-4-5"),
    "claude-opus-4-7":   lambda q, p, c: run_anthropic(q, p, c, "claude-opus-4-7"),
    "claude-sonnet-4-6": lambda q, p, c: run_anthropic(q, p, c, "claude-sonnet-4-6"),
    "gpt-4o":            lambda q, p, c: run_openai(q, p, c, "gpt-4o"),
    "gpt-4o-mini":       lambda q, p, c: run_openai(q, p, c, "gpt-4o-mini"),
    "gpt-5":             lambda q, p, c: run_openai(q, p, c, "gpt-5"),
    "gpt-5-nano":        lambda q, p, c: run_openai(q, p, c, "gpt-5-nano"),

    # ── hosted DeepSeek API (OpenAI-compatible) ────────────────────────
    "deepseek-chat":     lambda q, p, c: run_deepseek_api(q, p, c, "deepseek-chat"),
    "deepseek-reasoner": lambda q, p, c: run_deepseek_api(q, p, c, "deepseek-reasoner"),

    # ── hosted via OpenRouter (OpenAI-compatible) ──────────────────────
    "openrouter:gemini-2.5-flash-lite":
        lambda q, p, c: run_openrouter(q, p, c, "google/gemini-2.5-flash-lite"),
    "openrouter:llama-3.3-70b-instruct":
        lambda q, p, c: run_openrouter(q, p, c, "meta-llama/llama-3.3-70b-instruct"),

    # ── offline via Ollama (recommended for paper experiments) ────────
    "ollama:qwen2.5:7b":          lambda q, p, c: run_ollama(q, p, c, "qwen2.5:7b-instruct"),
    "ollama:llama3.2:3b":         lambda q, p, c: run_ollama(q, p, c, "llama3.2:3b-instruct"),
    "ollama:mistral:7b":          lambda q, p, c: run_ollama(q, p, c, "mistral:7b-instruct"),
    "ollama:deepseek-r1:7b":      lambda q, p, c: run_ollama(q, p, c, "deepseek-r1:7b"),
    "ollama:gemma2:9b":           lambda q, p, c: run_ollama(q, p, c, "gemma2:9b-instruct"),

    # ── offline via direct HF transformers load ───────────────────────
    "hf:deepseek-r1-qwen-7b":     lambda q, p, c: run_hf_local(q, p, c, "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"),
    # DeepSeek-R1-0528 (reasoning model) — needs a larger generation budget
    # because <think>...</think> blocks consume tokens before the plan.
    "hf:deepseek-r1-0528-qwen3-8b": lambda q, p, c: run_hf_local(
        q, p, c, "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B", max_new_tokens=1500),
    "hf:llama3.2-1b":             lambda q, p, c: run_hf_local(q, p, c, "meta-llama/Llama-3.2-1B"),
    "hf:llama3.2-3b":             lambda q, p, c: run_hf_local(q, p, c, "meta-llama/Llama-3.2-3B"),
    "hf:mistral-7b-instruct-v0.3": lambda q, p, c: run_hf_local(q, p, c, "mistralai/Mistral-7B-Instruct-v0.3"),
    "hf:gemma-2-9b-it":           lambda q, p, c: run_hf_local(q, p, c, "google/gemma-2-9b-it"),
    "hf:gemma-3-12b-it":          lambda q, p, c: run_hf_local(q, p, c, "google/gemma-3-12b-it"),
    "hf:qwen3-8b":                lambda q, p, c: run_hf_local(q, p, c, "Qwen/Qwen3-8B"),
    "hf:qwen2.5-7b-instruct":     lambda q, p, c: run_hf_local(q, p, c, "Qwen/Qwen2.5-7B-Instruct"),

    # ── external baseline generator (set BASELINE_LLM_URL) ────────────
    "baseline-service": run_baseline_service,
}


# ───────────────────────── driver ───────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", required=True, type=Path)
    ap.add_argument("--llm", required=True, choices=list(RUNNERS))
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    spec = yaml.safe_load(args.queries.read_text())
    qs = spec["queries"][: args.limit] if args.limit else spec["queries"]
    runner = RUNNERS[args.llm]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for q in qs:
            program, completed = db.student_context(q["user_id"])
            t0 = time.time()
            try:
                raw = runner(q["query"], program or "", completed)
                err = None
            except Exception as e:
                raw = ""
                err = f"{type(e).__name__}: {e}"
            rec = {
                "id": q["id"],
                "user_id": q["user_id"],
                "program": program,
                "completed": completed,
                "query": q["query"],
                "tags": q.get("tags", []),
                "category": q.get("category"),
                "expected_kind": q.get("expected_kind"),
                "llm": args.llm,
                "raw": raw,
                "error": err,
                "latency_s": round(time.time() - t0, 2),
            }
            f.write(json.dumps(rec) + "\n")
            print(f"[{q['id']}] {args.llm}  ({rec['latency_s']}s)" + (f"  ERROR: {err}" if err else ""))


if __name__ == "__main__":
    main()
