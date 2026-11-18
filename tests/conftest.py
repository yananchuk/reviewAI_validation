"""Shared fixtures. Only synthetic data is committed. Real client-like files are opt-in through environment
variables (REVIEWAI_EXAMPLE_JSON, REVIEWAI_EXAMPLE_XLSX) and are never copied into the repository."""

from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent.parent
SHARED_EXAMPLE = REPO / "shared" / "examples" / "example-review-report.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def example_report() -> dict:
    return json.loads(SHARED_EXAMPLE.read_text(encoding="utf-8"))


def finding(report: dict, fid: str) -> dict:
    for rule in report["rules"]:
        for f in rule.get("findings", []):
            if f["id"] == fid:
                return f
    raise KeyError(fid)


@pytest.fixture
def real_report() -> dict:
    path = os.environ.get("REVIEWAI_EXAMPLE_JSON")
    if not path:
        pytest.skip("set REVIEWAI_EXAMPLE_JSON to run tests on a real report")
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def real_workbook() -> tuple[str, bytes]:
    path = os.environ.get("REVIEWAI_EXAMPLE_XLSX")
    if not path:
        pytest.skip("set REVIEWAI_EXAMPLE_XLSX to run tests on a real workbook")
    return Path(path).name, Path(path).read_bytes()


# ---------------------------------------------------------------- synthetic workbook

SYN_NAME = "Synthetic_Leadsheet.xlsx"


def build_workbook(*, xlsm: bool = False, extras: dict[str, bytes] | None = None, mutate=None) -> bytes:
    """A small workbook covering the features the app must preserve.

    Lead: values, formulas, fonts, merged band, a hidden row and column, custom column width.
    'Kunde's Daten': non-English name with apostrophe, typed value replacing a formula.
    'Prüfung – Übersicht': header cell in Calibri 16 bold.
    'Hidden Calc': hidden sheet. 'Locked': protected sheet. Defined name, conditional formatting, data validation.
    """
    import zipfile

    from openpyxl import Workbook
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    lead = wb.active
    lead.title = "Lead"
    lead["A1"] = "ABC Ltd"
    lead["A1"].font = Font(name="Carlito", size=11)
    lead["A2"] = "1. Materiality"
    lead.merge_cells("A2:D2")
    for r, v in ((67, 100), (68, 200), (69, 300)):
        lead[f"B{r}"] = v
    lead["B70"] = "=SUM(B67:B68)"
    lead["B63"] = "The invoices was checked."
    lead["D67"] = "Please explain the €6,000 difference"
    lead["D67"].font = Font(name="Carlito", size=11)
    lead["A32"], lead["B32"] = "Sales Total", "x"
    lead["A32"].font = lead["B32"].font = Font(name="Carlito", size=11)
    lead["A33"], lead["B33"] = "Other", "y"
    lead["H12"] = "Amount (€)"
    lead["H13"] = 120000
    lead["H14"] = 8500
    lead.column_dimensions["H"].width = 2.0
    lead["C10"] = 5
    lead["C10"].fill = PatternFill("solid", fgColor="FFFF00")
    lead["C10"].alignment = Alignment(horizontal="center")
    lead.row_dimensions[5].hidden = True
    lead.column_dimensions["F"].hidden = True
    lead["F1"] = "hidden column value"
    lead["A5"] = "hidden row value"
    lead.conditional_formatting.add(
        "B67:B69", CellIsRule(operator="greaterThan", formula=["250"], fill=PatternFill("solid", fgColor="FF0000"))
    )
    dv = DataValidation(type="list", formula1='"Yes,No"')
    lead.add_data_validation(dv)
    dv.add("E5")

    kd = wb.create_sheet("Kunde's Daten")
    for r in (74, 75, 76):
        kd[f"A{r}"], kd[f"B{r}"] = 2, 60
    kd["C74"], kd["C75"], kd["C76"] = "=A74*B74", 120, "=A76*B76"

    pr = wb.create_sheet("Prüfung – Übersicht")
    pr["B2"] = "Ergebnisse"
    pr["B2"].font = Font(name="Calibri", size=16, bold=True)

    hc = wb.create_sheet("Hidden Calc")
    hc["D14"] = "x-ref?"
    hc.sheet_state = "hidden"

    lk = wb.create_sheet("Locked")
    lk["A1"] = "locked value"
    lk.protection.sheet = True

    wb.defined_names["Materiality"] = DefinedName("Materiality", attr_text="Lead!$B$67")

    if mutate:
        mutate(wb)
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    if not (xlsm or extras):
        return data
    # Add parts openpyxl would not write itself (VBA, custom XML, label metadata) to prove they survive.
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in src.infolist():
            content = src.read(info.filename)
            if xlsm and info.filename == "[Content_Types].xml":
                content = content.replace(
                    b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                    b"application/vnd.ms-excel.sheet.macroEnabled.main+xml",
                ).replace(
                    b"</Types>",
                    b'<Default Extension="bin" ContentType="application/vnd.ms-office.vbaProject"/></Types>',
                )
            zout.writestr(info, content)
        if xlsm:
            zout.writestr("xl/vbaProject.bin", b"\xd0\xcf\x11\xe0 dummy vba project " + bytes(range(256)))
        for name, content in (extras or {}).items():
            zout.writestr(name, content)
    return out.getvalue()


@pytest.fixture
def syn_bytes() -> bytes:
    return build_workbook(extras={"docMetadata/LabelInfo.xml": b'<?xml version="1.0"?><labelList/>'})


@pytest.fixture
def syn_report(example_report) -> dict:
    """The shared example, pointed at the synthetic workbook."""
    report = copy.deepcopy(example_report)
    report["workbook"] = SYN_NAME
    return report
