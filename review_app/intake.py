"""Checks on the raw uploaded bytes, before any parser sees them. Both files are untrusted."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from review_app import limits

ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

UNSUPPORTED_FORMAT = "This format cannot be read. Save the workbook as .xlsx or .xlsm in Excel and upload it again."


@dataclass
class WorkbookIntake:
    name: str
    extension: str
    errors: list[str] = field(default_factory=list)


def _mib(n: int) -> str:
    return f"{n / limits.MiB:.0f} MB"


def check_workbook(name: str, data: bytes) -> WorkbookIntake:
    ext = PurePath(name).suffix.lower()
    result = WorkbookIntake(name=PurePath(name).name, extension=ext)
    if ext in {".xls", ".xlsb"}:
        result.errors.append(UNSUPPORTED_FORMAT)
        return result
    if ext not in limits.WRITABLE_EXTENSIONS:
        result.errors.append(f"'{result.name}' is not an Excel workbook. Upload an .xlsx or .xlsm file.")
        return result
    if not data:
        result.errors.append("The workbook file is empty.")
        return result
    if len(data) > limits.MAX_WORKBOOK_BYTES:
        result.errors.append(f"The workbook is larger than {_mib(limits.MAX_WORKBOOK_BYTES)}.")
        return result
    if data.startswith(OLE_MAGIC):
        result.errors.append(
            "The workbook is password-protected or in a legacy binary format and cannot be read. "
            "Remove the password or save it as .xlsx or .xlsm."
        )
        return result
    if not data.startswith(ZIP_MAGIC):
        result.errors.append("The workbook could not be opened: the file is not a valid Excel package.")
        return result
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > limits.MAX_ZIP_ENTRIES:
                result.errors.append("The workbook package has too many parts to be processed safely.")
                return result
            if sum(i.file_size for i in infos) > limits.MAX_UNZIPPED_BYTES:
                result.errors.append(
                    "The workbook expands to more than the allowed size and cannot be processed safely."
                )
                return result
            names = {i.filename for i in infos}
            if "xl/workbook.xml" not in names:
                result.errors.append("The workbook could not be opened: it has no xl/workbook.xml part.")
            if "xl/workbook.bin" in names:
                result.errors.append(UNSUPPORTED_FORMAT)
    except zipfile.BadZipFile:
        result.errors.append("The workbook could not be opened: the file is not a valid Excel package.")
    return result


@dataclass
class ReportIntake:
    data: Any = None
    errors: list[str] = field(default_factory=list)


def check_report(name: str, data: bytes) -> ReportIntake:
    result = ReportIntake()
    if PurePath(name).suffix.lower() != ".json":
        result.errors.append(f"'{PurePath(name).name}' is not a JSON file. Upload the .review.json report.")
        return result
    if len(data) > limits.MAX_REPORT_BYTES:
        result.errors.append(f"The report is larger than {_mib(limits.MAX_REPORT_BYTES)}.")
        return result
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        result.errors.append("The report is not UTF-8 encoded text.")
        return result
    if not text.strip():
        result.errors.append("The report file is empty.")
        return result
    try:
        result.data = json.loads(text)
    except json.JSONDecodeError as exc:
        result.errors.append(f"The report is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg}).")
        return result
    if not isinstance(result.data, dict):
        result.errors.append("The report must be a JSON object.")
        result.data = None
    return result
