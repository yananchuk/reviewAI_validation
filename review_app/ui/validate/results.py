"""Validator page 4: score, per-rule table and the Markdown report download."""

from __future__ import annotations

import streamlit as st

from review_app import skill_validation as sv
from review_app.exporter import safe_stem
from review_app.ui.state import get_state
from review_app.ui.validate.common import run_details, step_bar


def render() -> None:
    state = get_state()
    v = state.validation
    step_bar("v_results")
    st.subheader("Results")
    run_details()
    if v.answer_key:
        st.caption(f"Answer key: {v.answer_key.tags} tags found, {v.answer_key.matched} matched")

    o = sv.outcomes(state.order, v)
    m = sv.metrics(o)
    cols = st.columns(3)
    cols[0].metric("F2", sv.fmt(m.f2))
    cols[1].metric("Recall", sv.fmt(m.recall))
    cols[2].metric("Precision", sv.fmt(m.precision))

    st.markdown("**Outcomes**")
    st.dataframe(
        [
            {"": "Skill reported", "Problem present": f"TP {o.tp}", "No problem": f"FP {o.fp}"},
            {"": "Skill did not report", "Problem present": f"FN {o.fn}", "No problem": "TN not counted"},
        ],
        hide_index=True,
        width="stretch",
    )
    st.caption(f"Duplicates: {o.duplicates}. Unlabelled: {o.unlabelled}.")

    st.markdown("**Per rule**")
    rows = sv.per_rule(state.findings, state.order, v)
    st.dataframe(
        [
            {
                "Rule": r["rule_id"],
                "TP": r["tp"],
                "FP": r["fp"],
                "FN": r["fn"],
                "Recall": sv.fmt(r["recall"]),
                "Precision": sv.fmt(r["precision"]),
                "F2": sv.fmt(r["f2"]),
            }
            for r in rows
        ],
        hide_index=True,
        width="stretch",
    )

    if v.answer_key is None:
        st.info(sv.REAL_WORKPAPER_NOTE)

    v.validator = st.text_input("Validator name", value=v.validator, key="v_validator")
    st.download_button(
        "Download report (Markdown)",
        data=sv.report_markdown(state),
        file_name=f"{safe_stem(state.original_name)}_skill_validation.md",
        mime="text/markdown",
        key="v_download",
        type="primary",
    )
    st.caption("This page lives only in this browser tab: download the report before closing it.")
