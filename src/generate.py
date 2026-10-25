"""AI layer: hybrid-grounded report generation.

Structured feed (numbers) always comes from kpis. RAG supplies non-numeric context only.
Supports a real Ollama call and deterministic mock modes for offline testing.
"""
from __future__ import annotations
import json
import os
import time
import requests

from .kpi_engine import kpi_block_text

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")


def _read(name: str) -> str:
    with open(os.path.join(_PROMPT_DIR, name), encoding="utf-8") as f:
        return f.read()


def build_prompt(scenario_title, period, kpis, context_block="", questions=None) -> tuple[str, str]:
    system = _read("system_prompt.txt")
    qs = questions or []
    questions_block = "\n".join(f"{i}. {q}" for i, q in enumerate(qs, 1)) or "(δεν ορίστηκαν ερωτήματα)"
    user = _read("user_prompt_template.txt").format(
        scenario_title=scenario_title, period=period,
        kpi_block=kpi_block_text(kpis),
        context_block=context_block or "(δεν παρασχέθηκε πρόσθετο πλαίσιο)",
        questions_block=questions_block)
    return system, user


def call_ollama(system, user, cfg, seed=None) -> str:
    llm = cfg["llm"]
    options = {
        "temperature": llm.get("temperature", 0.2),
        "top_p": llm.get("top_p", 0.9),
        # per-run seed (varies across runs, reproducible); fallback only if none supplied
        "seed": seed if seed is not None else llm.get("seed", 0),
        "num_ctx": llm.get("num_ctx", 8192),
    }
    # optional generation cap: bounds worst-case latency (a common cause of read timeouts on CPU).
    # Set generously above the ~150-250 word target so normal outputs are never truncated.
    if llm.get("num_predict"):
        options["num_predict"] = llm["num_predict"]
    payload = {"model": llm["model"], "prompt": user, "system": system,
               "stream": False, "options": options}
    timeout = llm.get("timeout", 900)          # per-call read timeout (s), configurable
    retries = llm.get("retries", 2)            # transient timeouts/connection drops
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(f"{llm['base_url']}/api/generate", json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json().get("response", "").strip()
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            last = e
            if attempt < retries:
                print(f"[ollama] {type(e).__name__} (attempt {attempt+1}/{retries+1}); retrying...")
                time.sleep(3)
    raise last


def _fmt(v):
    val, unit = v["value"], v["unit"]
    if unit == "EUR":
        return f"{val:,.2f} EUR"
    if unit == "%":
        return f"{val:.2f}%"
    return f"{val:g} {unit}"


def mock_generate(scenario_title, period, kpis, noisy=False) -> str:
    """Deterministic, faithful templated narrative (for offline flow testing).

    If noisy=True, one KPI value is deliberately perturbed to demonstrate that the
    validation layer detects hallucinations. Never used for reported results.
    """
    items = list(kpis.items())
    lines = [f"Αναφορά επίδοσης - {scenario_title} ({period}).", "", "Σύνοψη:"]
    for i, (k, v) in enumerate(items):
        name = k.replace("::", " - ")
        shown = _fmt(v)
        if noisy and i == 0:
            bad = dict(v); bad["value"] = round(v["value"] * 1.25 + 3, 2)
            shown = _fmt(bad)  # injected error
        lines.append(f"Ο δείκτης «{name}» διαμορφώθηκε σε {shown}.")
    lines += ["", "Επιφύλαξη: η αναφορά βασίζεται αποκλειστικά στα διαθέσιμα δεδομένα της περιόδου."]
    return "\n".join(lines)


def generate(scenario, period, kpis, cfg, context_block="", mode="ollama", seed=None) -> dict:
    system, user = build_prompt(scenario["title"], period, kpis, context_block,
                                questions=scenario.get("question"))
    if mode == "mock":
        text = mock_generate(scenario["title"], period, kpis, noisy=False)
    elif mode == "mock-noisy":
        text = mock_generate(scenario["title"], period, kpis, noisy=True)
    else:
        text = call_ollama(system, user, cfg, seed=seed)
    return {"system": system, "user": user, "text": text, "mode": mode, "seed": seed}
