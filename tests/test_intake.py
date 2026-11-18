import io
import zipfile

from review_app import limits
from review_app.intake import UNSUPPORTED_FORMAT, check_report, check_workbook


def test_xlsx_accepted(syn_bytes):
    assert check_workbook("WP.xlsx", syn_bytes).errors == []


def test_legacy_formats_rejected(syn_bytes):
    assert check_workbook("WP.xls", syn_bytes).errors == [UNSUPPORTED_FORMAT]
    assert check_workbook("WP.xlsb", syn_bytes).errors == [UNSUPPORTED_FORMAT]


def test_other_extensions_and_content():
    assert "not an Excel workbook" in check_workbook("WP.csv", b"a,b").errors[0]
    assert "empty" in check_workbook("WP.xlsx", b"").errors[0]
    assert "not a valid Excel package" in check_workbook("WP.xlsx", b"hello").errors[0]
    assert "password-protected" in check_workbook("WP.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 10).errors[0]


def test_renamed_zip_without_workbook_part():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "x")
    assert "no xl/workbook.xml" in check_workbook("WP.xlsx", buf.getvalue()).errors[0]


def test_zip_bomb_guard(monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", b"0" * 2_000_000)
    monkeypatch.setattr(limits, "MAX_UNZIPPED_BYTES", 1_000_000)
    assert "expands" in check_workbook("WP.xlsx", buf.getvalue()).errors[0]


def test_size_limit(monkeypatch, syn_bytes):
    monkeypatch.setattr(limits, "MAX_WORKBOOK_BYTES", 10)
    assert "larger than" in check_workbook("WP.xlsx", syn_bytes).errors[0]


def test_report_checks():
    assert check_report("r.json", b'{"a": 1}').data == {"a": 1}
    assert check_report("r.json", b"  ").errors == ["The report file is empty."]
    assert "line 1" in check_report("r.json", b"{oops").errors[0]
    assert check_report("r.json", b"[1]").errors == ["The report must be a JSON object."]
    assert "not a JSON file" in check_report("r.txt", b"{}").errors[0]
    assert "UTF-8" in check_report("r.json", b"\xff\xfe\x00").errors[0]
    assert check_report("r.json", "﻿{}".encode()).data == {}
