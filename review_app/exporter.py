"""Output file names, the review log and the ZIP package. The original report is never overwritten."""

from __future__ import annotations

import io
import json
import re
import unicodedata
import zipfile
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

from review_app import __version__
from review_app.models import REVIEWED_DECISIONS, DecisionValue

if TYPE_CHECKING:
    from review_app.review_session import SessionState

LOG_VERSION = "1.0"


def safe_stem(name: str) -> str:
    """A file-name stem safe for downloads and ZIP entries: no folders, no control or reserved characters."""
    stem = PurePath(PurePath(name.replace("\\", "/")).name).stem
    stem = unicodedata.normalize("NFC", stem)
    stem = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", stem).strip(" .")
    return (stem or "workbook")[:100]


def output_names(input_name: str) -> dict[str, str]:
    stem = safe_stem(input_name)
    ext = PurePath(input_name).suffix.lower()
    return {
        "workbook": f"{stem}_reviewed{ext}",
        "log": f"{stem}_review_log.json",
        "package": f"{stem}_review_package.zip",
    }


def review_log(state: SessionState) -> dict[str, Any]:
    meta, profile = state.report_meta, state.profile
    findings = []
    for fid in state.order:
        f = state.findings[fid]
        d = state.decisions.get(fid)
        findings.append(
            {
                "id": f.id,
                "fingerprint": f.fingerprint,
                "rule_id": f.rule.rule_id,
                "resolution": f.resolution.value,
                "decision": (d.value if d else DecisionValue.OPEN).value,
                "comment": d.comment if d else "",
                "decided_at": d.decided_at if d else None,
                "stale_override": d.stale_override if d else False,
                "automatic": f.automatic,
                "manual_reason": None if f.automatic else f.operation.reason,
                "proposed_change": f.change.model_dump() if f.change else None,
                "possible_duplicate_of": f.duplicates + f.missing_duplicates,
                "check_problems": f.check_problems,
            }
        )
    decisions = [state.decisions.get(fid) for fid in state.order]
    open_ids = [
        fid for fid, d in zip(state.order, decisions, strict=True) if d is None or d.value not in REVIEWED_DECISIONS
    ]
    counts: dict[str, int] = {}
    for d in decisions:
        key = (d.value if d else DecisionValue.OPEN).value
        counts[key] = counts.get(key, 0) + 1
    workbook_out = state.output_meta.get("workbook")
    return {
        "log_version": LOG_VERSION,
        "generated_by": {"app": "review-web-app", "version": __version__},
        "source_report": {
            "file_name": state.report_name,
            "sha256": state.report_sha256,
            "run_id": meta.run_id if meta else None,
            "schema_version": meta.schema_version if meta else None,
            "skill_version": meta.skill_version if meta else None,
            "reviewed_at": meta.reviewed_at if meta else None,
            "workbook": meta.workbook if meta else None,
        },
        "workbook": {
            "uploaded_name": state.original_name,
            "name_in_report": meta.workbook if meta else None,
            "name_mismatch": state.name_mismatch,
            "name_mismatch_confirmed": state.name_mismatch_confirmed,
            "sha256": state.original_sha256,
            "writable": profile.writable if profile else False,
            "not_writable_reasons": profile.not_writable_reasons if profile else [],
            "output": workbook_out,
        },
        "session": {
            "started_at": state.started_at,
            "completed_at": state.completed_at,
            "open_findings_remain": bool(open_ids),
            "open_finding_ids": open_ids,
            "decision_counts": counts,
        },
        "findings": findings,
        "applied_changes": [c.model_dump(mode="json") for c in state.applied],
        "rejected_changes": [fid for fid, d in state.decisions.items() if d.value == DecisionValue.REJECTED],
        "manual_resolutions": [fid for fid, d in state.decisions.items() if d.value == DecisionValue.MANUALLY_RESOLVED],
        "stale_overrides": [c.finding_id for c in state.applied if c.stale_override],
        "visibility": {
            "original": [
                {"sheet": s.name, "state": s.state, "hidden_rows": s.hidden_rows, "hidden_columns": s.hidden_columns}
                for s in (profile.sheets if profile else [])
            ],
            "reveal_requests": [r.model_dump() for r in state.visibility_requests],
            "output_preserves_original": not state.visibility_requests,
        },
        "validation_warnings": [i.message for i in state.validation_warnings],
        "application_errors": state.errors,
        "integrity": [r.model_dump() for r in state.integrity],
    }


def log_bytes(state: SessionState) -> bytes:
    return json.dumps(review_log(state), ensure_ascii=False, indent=2, default=str).encode("utf-8")


def package(files: dict[str, bytes]) -> bytes:
    """ZIP of the generated outputs. Entry names are flat, sanitized file names (no paths, no traversal)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            ext = re.sub(r"[^A-Za-z0-9.]", "", PurePath(name.replace("\\", "/")).suffix)
            zf.writestr(f"{safe_stem(name)}{ext}", data)
    return buf.getvalue()
