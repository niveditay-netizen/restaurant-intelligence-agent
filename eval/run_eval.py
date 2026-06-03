"""Eval runner — runs all 20 cases and prints a category report.

Usage:
    python eval/run_eval.py            # run every case
    python eval/run_eval.py --limit 3  # smoke-test the first 3 cases

Each case calls the real agent (live API calls), so a full run costs roughly
$2-3 in Google/Anthropic credits. Use --limit while iterating.

Scoring:
  retrieval     target found AND >= min_competitors_found competitors
  quality       exactly 3 recs, each with a rationale, no generic phrases,
                each rec contains a number (specificity) AND references a named
                competitor or review theme (grounding)
  hallucination every competitor name in the output is a subset of the names
                Google Places returned (zero fabricated names)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Allow running as `python eval/run_eval.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.orchestrator import run_agent  # noqa: E402

CASES_PATH = Path(__file__).resolve().parent / "cases.json"

# Phrases that signal vague, ungrounded advice → auto-fail for quality cases.
GENERIC_PHRASES = [
    "improve service",
    "enhance customer experience",
    "focus on quality",
    "improve quality",
    "provide better service",
    "increase marketing",
    "improve the menu",
    "better customer service",
    "improve ambiance",
    "work on branding",
]

_NUMBER_RE = re.compile(r"\d")


def _has_number(text: str) -> bool:
    """Specificity signal: a price, count, distance, or percentage."""
    return bool(_NUMBER_RE.search(text or ""))


def _references_competitor(text: str, competitor_names: list[str]) -> bool:
    """Grounding signal: the text names a real competitor."""
    lowered = (text or "").lower()
    return any(name and name.lower() in lowered for name in competitor_names)


def _score_retrieval(case: dict, report: dict) -> tuple[bool, str]:
    if report.get("error"):
        return False, report["error"]
    found = bool(report.get("restaurant", {}).get("name"))
    n_comp = len(report.get("competitors") or [])
    min_comp = case["expected"].get("min_competitors_found", 3)
    ok = found and n_comp >= min_comp
    detail = f"{report.get('restaurant', {}).get('name', '???')} found, {n_comp} competitors"
    return ok, detail


def _score_quality(case: dict, report: dict) -> tuple[bool, str]:
    if report.get("error"):
        return False, report["error"]
    recs = report.get("recommendations") or []
    names = [c.get("name") for c in report.get("competitors") or []]

    if len(recs) != 3:
        return False, f"expected 3 recommendations, got {len(recs)}"

    problems: list[str] = []
    for i, rec in enumerate(recs, 1):
        blob = f"{rec.get('action', '')} {rec.get('rationale', '')}"
        if not rec.get("rationale"):
            problems.append(f"rec {i} missing rationale")
        low = blob.lower()
        hit = next((p for p in GENERIC_PHRASES if p in low), None)
        if hit:
            problems.append(f"rec {i} generic ('{hit}')")
        if not _has_number(blob):
            problems.append(f"rec {i} not specific (no number)")
        if not _references_competitor(blob, names):
            problems.append(f"rec {i} not grounded (no named competitor)")

    if problems:
        return False, "; ".join(problems)
    return True, "3 recommendations, all data-grounded"


def _score_hallucination(case: dict, report: dict) -> tuple[bool, str]:
    if report.get("error"):
        return False, report["error"]
    known = {n for n in (report.get("_google_competitor_names") or []) if n}
    output_names = {c.get("name") for c in (report.get("competitors") or []) if c.get("name")}
    fabricated = output_names - known
    if fabricated:
        return False, f"fabricated names: {sorted(fabricated)}"
    return True, f"0 fabricated names ({len(output_names)} competitors checked)"


SCORERS = {
    "retrieval": _score_retrieval,
    "quality": _score_quality,
    "hallucination": _score_hallucination,
}

CATEGORY_TITLES = {
    "retrieval": "Data Retrieval",
    "quality": "Recommendation Quality",
    "hallucination": "Hallucination Check",
}


def _bar(passed: int, total: int, width: int = 8) -> str:
    filled = round((passed / total) * width) if total else 0
    return "█" * filled + "░" * (width - filled)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent eval suite.")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    args = parser.parse_args()

    cases = json.loads(CASES_PATH.read_text())
    if args.limit:
        cases = cases[: args.limit]

    print("=" * 50)
    print("RESTAURANT INTEL AGENT — EVAL SUITE")
    print("=" * 50)

    results: dict[str, list[tuple[str, bool, str]]] = {"retrieval": [], "quality": [], "hallucination": []}

    for category in ("retrieval", "quality", "hallucination"):
        cat_cases = [c for c in cases if c["category"] == category]
        if not cat_cases:
            continue
        print(f"\nCategory: {CATEGORY_TITLES[category]} ({len(cat_cases)} cases)")
        for case in cat_cases:
            inp = case["input"]
            try:
                report = run_agent(
                    inp["restaurant_name"], inp["city"], inp.get("problem", "general analysis")
                )
                ok, detail = SCORERS[category](case, report)
            except Exception as exc:  # a crash is a failed case, not a crashed run
                ok, detail = False, f"exception: {exc}"
            mark = "✓" if ok else "✗"
            print(f"  [{mark}] {case['id']} — {detail}")
            results[category].append((case["id"], ok, detail))

    print("\n" + "=" * 50)
    print("AGGREGATE")
    print("=" * 50)
    total_pass = total = 0
    for category in ("retrieval", "quality", "hallucination"):
        rows = results[category]
        if not rows:
            continue
        passed = sum(1 for _, ok, _ in rows if ok)
        total_pass += passed
        total += len(rows)
        label = f"{CATEGORY_TITLES[category]}:".ljust(24)
        suffix = "  (perfect)" if passed == len(rows) else ""
        print(f"{label}{passed}/{len(rows)}  {_bar(passed, len(rows))}{suffix}")
    pct = round((total_pass / total) * 100) if total else 0
    print(f"{'Overall:'.ljust(24)}{total_pass}/{total}  ({pct}%)")
    print("=" * 50)

    sys.exit(0 if total_pass == total else 1)


if __name__ == "__main__":
    main()
