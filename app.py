"""Streamlit UI for the Restaurant Intelligence Agent.

Three inputs, one button, one structured report. The agent's step-by-step
status is surfaced live so the agentic loop is visible during a demo.

Run locally:
    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from agent import config
from agent.orchestrator import run_agent

st.set_page_config(page_title="Restaurant Intelligence Agent", page_icon="🍝", layout="centered")

_PRIORITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}


def _missing_keys() -> list[str]:
    names = [
        "ANTHROPIC_API_KEY",
        "GOOGLE_PLACES_API_KEY",
        "YELP_API_KEY",
        "TICKETMASTER_API_KEY",
    ]
    return [n for n in names if not getattr(config, n)]


def _render_report(report: dict) -> None:
    if report.get("error"):
        st.error(report["error"])
        return

    r = report["restaurant"]
    cuisine = ", ".join(r.get("cuisine") or []) or "—"
    st.subheader(f"📍 {r.get('name')}")
    st.caption(
        f"{r.get('location') or ''}  ·  ⭐ {r.get('rating') or '—'}  "
        f"·  {cuisine}  ·  price level {r.get('price_level') if r.get('price_level') is not None else '—'}"
    )

    st.markdown("### 🩺 Diagnosis")
    st.write(report.get("diagnosis") or "_No diagnosis returned._")

    recs = report.get("recommendations") or []
    if recs:
        st.markdown("### ✅ Recommendations")
        for i, rec in enumerate(recs, 1):
            emoji = _PRIORITY_EMOJI.get((rec.get("priority") or "").lower(), "•")
            with st.container(border=True):
                st.markdown(f"**{emoji} {i}. {rec.get('action', '')}**")
                if rec.get("rationale"):
                    st.write(rec["rationale"])
                st.caption(f"Priority: {rec.get('priority', 'n/a')}")

    gaps = report.get("market_gaps") or []
    if gaps:
        st.markdown("### 🕳️ Market Gaps")
        for g in gaps:
            st.markdown(f"- {g}")

    dont = report.get("what_not_to_do") or []
    if dont:
        st.markdown("### ⛔ What Not To Do")
        for d in dont:
            st.markdown(f"- {d}")

    comps = report.get("competitors") or []
    if comps:
        st.markdown("### 🍴 Top Competitors")
        for c in comps:
            with st.container(border=True):
                st.markdown(f"**{c.get('name')}** · ⭐ {c.get('rating') or '—'}")
                highlights = c.get("menu_highlights") or []
                if highlights:
                    st.caption("Menu highlights: " + ", ".join(highlights))
                for theme in c.get("review_themes") or []:
                    if theme:
                        st.markdown(f"> {theme}")

    if report.get("events_context"):
        st.markdown("### 🎟️ Events Context")
        st.write(report["events_context"])

    sources = report.get("data_sources") or []
    if sources:
        st.divider()
        st.caption("Data sources: " + " · ".join(sources))


def main() -> None:
    st.title("🍝 Restaurant Intelligence Agent")
    st.write(
        "Type your restaurant name and city. The agent autonomously gathers "
        "competitor data, reads menus via vision AI, and returns specific, "
        "data-grounded recommendations."
    )

    missing = _missing_keys()
    if missing:
        st.warning(
            "Missing API key(s): "
            + ", ".join(missing)
            + ". Set them in `.env` (local) or the Streamlit secrets manager (deployed)."
        )

    with st.form("analyze"):
        name = st.text_input("Restaurant name", placeholder="Piccolo Forno")
        city = st.text_input("City", placeholder="Pittsburgh")
        problem = st.text_area(
            "Describe your problem (optional)",
            placeholder="e.g. slow Tuesday dinners",
        )
        submitted = st.form_submit_button("Analyze →", use_container_width=True)

    if not submitted:
        return
    if not name or not city:
        st.error("Please enter both a restaurant name and a city.")
        return
    if missing:
        st.stop()

    status = st.status("Running the agent...", expanded=True)

    def progress(msg: str) -> None:
        status.write(msg)

    try:
        report = run_agent(
            name.strip(),
            city.strip(),
            problem.strip() or "general competitive analysis",
            progress=progress,
        )
    except Exception as exc:  # surface failures cleanly instead of a stack trace
        status.update(label="Failed", state="error")
        st.error(f"Something went wrong: {exc}")
        return

    state = "error" if report.get("error") else "complete"
    status.update(label="Analysis complete", state=state)
    _render_report(report)


if __name__ == "__main__":
    main()
