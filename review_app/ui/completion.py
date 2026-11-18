"""Completion: summary, open findings, output generation behind the integrity gate, and downloads."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from review_app import review_session as rs
from review_app.exporter import output_names
from review_app.integrity import download_allowed
from review_app.ui.finding_card import DECISION_LABELS
from review_app.ui.state import esc, get_state, show_flash

MIME = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".json": "application/json",
    ".zip": "application/zip",
}


def _open(fid: str) -> None:
    state = get_state()
    rs.jump(state, fid)
    state.page = "review"


def _back() -> None:
    get_state().page = "review"


def _build() -> None:
    with st.spinner("Building and checking the outputs…"):
        rs.build_outputs(get_state())


def render() -> None:
    state = get_state()
    show_flash()
    st.button("◀ Back to review", key="to_review", on_click=_back)
    st.title("Finish review")
    p = rs.progress(state)
    c1, c2, c3 = st.columns(3)
    c1.metric("Reviewed", f"{p['reviewed']} of {p['total']}")
    c2.metric("Still open", p["remaining"])
    c3.metric("Fixes applied", p["applied"])

    counts: dict[str, int] = {}
    for fid in state.order:
        label = DECISION_LABELS[rs.status_of(state, fid)]
        counts[label] = counts.get(label, 0) + 1
    st.caption(" · ".join(f"{k}: {v}" for k, v in counts.items()))

    open_ids = [fid for fid in state.order if not rs.is_reviewed(state, fid)]
    if open_ids:
        st.warning(f"{len(open_ids)} finding(s) are still open. You can still export; the log records them as open.")
        with st.expander("Open findings"):
            for fid in open_ids:
                f = state.findings[fid]
                a, b = st.columns([5, 1])
                a.markdown(f"**{esc(fid)}** · {esc(f.rule.rule_id)} · {esc(f.primary_label)}")
                b.button("Open", key=f"open:{fid}", on_click=_open, args=(fid,))

    if state.visibility_requests:
        st.info(f"{len(state.visibility_requests)} hidden location(s) will be unhidden in the output workbook.")
    else:
        st.caption("The output workbook keeps the original visibility of every sheet, row and column.")

    st.divider()
    if state.output_dirty or not state.outputs:
        st.button("Generate outputs", type="primary", on_click=_build, key="generate")
        if state.outputs:
            st.caption("The review changed since the outputs were generated. Generate them again to download.")
        return

    names = output_names(state.original_name)
    if state.integrity:
        st.markdown("#### Workbook checks")
        st.dataframe(
            pd.DataFrame([{"Check": r.check, "Result": r.status.upper(), "Detail": r.detail} for r in state.integrity]),
            hide_index=True,
        )
        if not download_allowed(state.integrity):
            st.error(
                "The modified workbook failed a check and cannot be downloaded. The review log is still available."
            )
    elif state.applied or state.visibility_requests:
        st.warning("Changes cannot be written to this workbook; only the review log is available.")
    else:
        st.caption("No changes were applied, so there is no modified workbook. The review log is available.")
    for err in state.errors:
        st.error(err)

    st.markdown("#### Downloads")
    for key in ("workbook", "log", "package"):
        name = names[key]
        if name in state.outputs:
            ext = "." + name.rsplit(".", 1)[-1].lower()
            st.download_button(
                f"Download {name}",
                data=state.outputs[name],
                file_name=name,
                mime=MIME.get(ext, "application/octet-stream"),
                key=f"dl:{key}",
            )
    st.caption("The uploaded workbook and the original report are unchanged.")
