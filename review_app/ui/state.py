"""Adapter between st.session_state and the SessionState, plus small rendering helpers.

Only one SessionState object lives in st.session_state (key 'rs'). Workbook objects are never stored there:
workbook content is kept as bytes and read through the cached, read-only reader."""

from __future__ import annotations

import re

import streamlit as st

from review_app.review_session import Outcome, SessionState

STATE_KEY = "rs"
FLASH_KEY = "flash"
STALE_KEY = "stale_for"


def get_state() -> SessionState:
    if STATE_KEY not in st.session_state:
        st.session_state[STATE_KEY] = SessionState()
    return st.session_state[STATE_KEY]


def set_state(state: SessionState) -> None:
    st.session_state[STATE_KEY] = state


def reset(clear_uploads: bool = True) -> None:
    """Discard the review and its widget values. Uploaders are cleared by giving them new keys."""
    upload_round = st.session_state.get("upload_round", 0)
    # The upload page draws the mode choice before the uploads are read, so a soft reset must keep it.
    keep = () if clear_uploads else ("mode", "mode_conflict")
    for key in list(st.session_state.keys()):
        if clear_uploads or not (str(key).startswith("up_") or key in keep):
            del st.session_state[key]
    st.session_state["upload_round"] = upload_round + (1 if clear_uploads else 0)
    st.session_state[STATE_KEY] = SessionState()


def upload_key(name: str) -> str:
    return f"up_{name}_{st.session_state.get('upload_round', 0)}"


def action_id(state: SessionState, kind: str, fid: str = "") -> str:
    """Bound at render time: a second click on the same rendered button reuses the id and is ignored."""
    return f"{kind}:{fid}:{state.version}"


def flash(level: str, text: str) -> None:
    st.session_state[FLASH_KEY] = (level, text)


def flash_outcome(outcome: Outcome, success: str | None = None) -> None:
    if outcome.status in {"applied", "saved", "undone"}:
        flash("success", success or outcome.message or "Saved.")
    elif outcome.status in {"blocked", "invalid"}:
        flash("error", outcome.message)


def show_flash() -> None:
    item = st.session_state.pop(FLASH_KEY, None)
    if not item:
        return
    level, text = item
    {"success": st.success, "error": st.error, "warning": st.warning}.get(level, st.info)(text)


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$:])")


def esc(text: object) -> str:
    """Escape untrusted text (report or workbook content) for st.markdown. HTML is never enabled."""
    return _MD_SPECIAL.sub(r"\\\1", str(text if text is not None else ""))
