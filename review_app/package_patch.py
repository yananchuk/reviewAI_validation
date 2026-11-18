"""Write approved changes into a copy of the workbook package without re-saving it through a spreadsheet library.

openpyxl drops parts it does not know (sensitivity-label metadata, threaded comments, slicers, custom XML, ...).
Following the precedent of the skill's own renderer (wp_inject.py on the template-output branch), this module copies
every package part byte for byte and changes only what an approved change needs:

* the target worksheet part: the exact <c>, <col> or <row> element text is replaced; the rest of the part is untouched
  (no XML re-serialization, so namespace prefixes, mc:Ignorable and extension lists survive as they are);
* xl/styles.xml: a font or cell format is appended (never changed in place, so other cells are not affected);
* xl/workbook.xml: <calcPr fullCalcOnLoad="1"> after formula edits, and sheet state for explicit reveals;
* xl/calcChain.xml: removed after cell edits (Excel rebuilds it), with its relationship and content-type entry.

Anything this module cannot do exactly raises PatchError; the caller then keeps the finding for manual handling.
"""

from __future__ import annotations

import html
import io
import math
import posixpath
import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET  # noqa: S405 - only used to read parts of an already size-checked package

from review_app.addresses import col_index, parse_range
from review_app.models import FontSpec, SetColumnWidth, SetFont, SetFormula, SetValue, VisibilityRequest

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
WORKSHEET_TYPE = NS_DOC_REL + "/worksheet"
CALC_CHAIN = "xl/calcChain.xml"

_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_ATTR = re.compile(r'([\w:.-]+)\s*=\s*"([^"]*)"')


class PatchError(Exception):
    """The change cannot be written exactly; keep the finding for manual handling."""


# ---------------------------------------------------------------- package reading


@dataclass(frozen=True)
class SheetEntry:
    name: str
    state: str
    part: str | None
    rel_type: str


def _xml(data: bytes) -> ET.Element:
    return ET.fromstring(data)  # noqa: S314 - package size is checked at intake; no DTDs in OOXML parts


def _resolve(base_dir: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(base_dir, target))


def workbook_sheets(zf: zipfile.ZipFile) -> list[SheetEntry]:
    """Sheets in workbook order with their state and part path, read from xl/workbook.xml and its relationships."""
    rels: dict[str, tuple[str, str]] = {}
    if "xl/_rels/workbook.xml.rels" in zf.namelist():
        for rel in _xml(zf.read("xl/_rels/workbook.xml.rels")).findall(f"{{{NS_PKG_REL}}}Relationship"):
            rels[rel.get("Id", "")] = (_resolve("xl", rel.get("Target", "")), rel.get("Type", ""))
    out = []
    root = _xml(zf.read("xl/workbook.xml"))
    for sheet in root.iter(f"{{{NS_MAIN}}}sheet"):
        part, rtype = rels.get(sheet.get(f"{{{NS_DOC_REL}}}id", ""), (None, ""))
        out.append(SheetEntry(sheet.get("name", ""), sheet.get("state", "visible"), part, rtype))
    return out


def defined_names(zf: zipfile.ZipFile) -> list[str]:
    root = _xml(zf.read("xl/workbook.xml"))
    names = []
    for dn in root.iter(f"{{{NS_MAIN}}}definedName"):
        local = dn.get("localSheetId")
        names.append(f"{dn.get('name')}{'@' + local if local is not None else ''}={dn.text or ''}")
    return sorted(names)


def structure_protected(zf: zipfile.ZipFile) -> bool:
    prot = _xml(zf.read("xl/workbook.xml")).find(f"{{{NS_MAIN}}}workbookProtection")
    return prot is not None and prot.get("lockStructure") in {"1", "true"}


# ---------------------------------------------------------------- text helpers


def _prefix(text: str, root: str) -> str:
    m = re.search(rf"<(?:([\w.-]+):)?{root}[\s>]", text)
    if not m:
        raise PatchError(f"The part has no <{root}> element.")
    return f"{m.group(1)}:" if m.group(1) else ""


def _attrs(tag: str) -> dict[str, str]:
    return {k: html.unescape(v) for k, v in _ATTR.findall(tag)}


def _set_attr(tag: str, name: str, value: str) -> str:
    """Set an attribute on an opening tag text ('<c r="A1" s="3">' or '<col .../>')."""
    pattern = re.compile(rf'(\s{re.escape(name)}\s*=\s*")[^"]*(")')
    if pattern.search(tag):
        return pattern.sub(lambda m: m.group(1) + value + m.group(2), tag, count=1)
    end = -2 if tag.endswith("/>") else -1
    return f'{tag[:end].rstrip()} {name}="{value}"{tag[end:]}'


def _remove_attr(tag: str, name: str) -> str:
    return re.sub(rf'\s{re.escape(name)}\s*=\s*"[^"]*"', "", tag, count=1)


def _escape(text: str) -> str:
    if _ILLEGAL_XML.search(text):
        raise PatchError("The new text contains characters that cannot be stored in an Excel cell.")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _number(value: float | int) -> str:
    if isinstance(value, bool):
        raise PatchError("Unexpected boolean.")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PatchError("The new number is not finite.")
        return repr(value)
    return str(value)


def _fmt_num(value: float) -> str:
    """15.0 -> '15', 15.375 -> '15.375' (no precision loss)."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _element(text: str, p: str, name: str, start: int = 0, end: int | None = None) -> list[re.Match]:
    """Non-nested elements <p:name ...>...</p:name> or <p:name .../> between start and end."""
    pattern = re.compile(rf"<{re.escape(p)}{name}(?=[\s/>])[^>]*?(?:/>|>.*?</{re.escape(p)}{name}>)", re.DOTALL)
    return list(pattern.finditer(text, start, len(text) if end is None else end))


def _opening(element: str) -> str:
    return element[: element.index(">") + 1]


# ---------------------------------------------------------------- worksheet edits


class _Sheet:
    def __init__(self, text: str):
        self.text = text
        self.p = _prefix(text, "worksheet")

    def _data_span(self) -> tuple[int, int]:
        p = re.escape(self.p)
        m = re.search(rf"<{p}sheetData(?=[\s/>])[^>]*?(/?)>", self.text)
        if not m:
            raise PatchError("The worksheet has no cell data.")
        if m.group(1):
            return m.end(), m.end()
        close = self.text.find(f"</{self.p}sheetData>", m.end())
        return m.end(), close

    def find_cell(self, ref: str) -> re.Match | None:
        start, end = self._data_span()
        p = re.escape(self.p)
        pattern = re.compile(rf'<{p}c(?=\s)[^>]*?\sr="{ref}"[^>]*?(?:/>|>.*?</{p}c>)', re.DOTALL)
        return pattern.search(self.text, start, end)

    def cell(self, ref: str) -> str:
        m = self.find_cell(ref)
        if m is None:
            raise PatchError(f"Cell {ref} has no content in the workbook, so it cannot be changed automatically.")
        return m.group(0)

    def replace_cell(self, ref: str, new: str) -> None:
        m = self.find_cell(ref)
        if m is None:
            raise PatchError(f"Cell {ref} has no content in the workbook, so it cannot be changed automatically.")
        self.text = self.text[: m.start()] + new + self.text[m.end() :]

    def _content_tag(self, ref: str, drop: Iterable[str] = ("t", "cm", "vm")) -> str:
        tag = _opening(self.cell(ref)).rstrip("/>").rstrip()
        for name in drop:
            tag = _remove_attr(tag, name)
        return tag

    def _check_formula_kind(self, ref: str) -> None:
        f = re.search(rf"<{re.escape(self.p)}f(?=[\s/>])[^>]*", self.cell(ref))
        if f:
            a = _attrs(f.group(0))
            if a.get("t") in {"array", "dataTable"} or (a.get("t") == "shared" and "ref" in a):
                raise PatchError(f"Cell {ref} holds an array or shared formula used by other cells.")

    def set_value(self, ref: str, value: str | int | float | bool) -> None:
        self._check_formula_kind(ref)
        p, tag = self.p, self._content_tag(ref)
        if isinstance(value, bool):
            new = f'{tag} t="b"><{p}v>{1 if value else 0}</{p}v></{p}c>'
        elif isinstance(value, (int, float)):
            new = f"{tag}><{p}v>{_number(value)}</{p}v></{p}c>"
        else:
            new = f'{tag} t="inlineStr"><{p}is><{p}t xml:space="preserve">{_escape(value)}</{p}t></{p}is></{p}c>'
        self.replace_cell(ref, new)

    def set_formula(self, ref: str, formula: str) -> None:
        self._check_formula_kind(ref)
        body = formula[1:] if formula.startswith("=") else formula
        p, tag = self.p, self._content_tag(ref)
        # No cached <v>: Excel calculates the result when the file is opened (fullCalcOnLoad).
        self.replace_cell(ref, f"{tag}><{p}f>{_escape(body)}</{p}f></{p}c>")

    def cell_style(self, ref: str) -> int:
        return int(_attrs(_opening(self.cell(ref))).get("s", "0"))

    def set_cell_style(self, ref: str, style: int) -> None:
        element = self.cell(ref)
        tag = _opening(element)
        self.replace_cell(ref, _set_attr(tag, "s", str(style)) + element[len(tag) :])

    def cell_has_rich_text(self, ref: str, shared_strings: list[str]) -> bool:
        element = self.cell(ref)
        a = _attrs(_opening(element))
        rich = re.compile(r"<(?:[\w.-]+:)?r(?=[\s>])")
        if a.get("t") == "inlineStr":
            return bool(rich.search(element))
        if a.get("t") == "s":
            m = re.search(r"<(?:[\w.-]+:)?v>(\d+)</", element)
            if m and int(m.group(1)) < len(shared_strings):
                return bool(rich.search(shared_strings[int(m.group(1))]))
        return False

    # columns ------------------------------------------------------------

    def _cols(self, col: int) -> list[re.Match]:
        return [
            m for m in _element(self.text, self.p, "col") if _col_span(m.group(0))[0] <= col <= _col_span(m.group(0))[1]
        ]

    def column_width(self, letter: str) -> tuple[float | None, bool]:
        """(width or None, shared with other columns)."""
        idx = col_index(letter)
        found = self._cols(idx)
        if not found:
            return None, False
        lo, hi = _col_span(found[0].group(0))
        width = _attrs(found[0].group(0)).get("width")
        return (float(width) if width else None), (lo, hi) != (idx, idx) or len(found) > 1

    def set_column_width(self, letter: str, width: float) -> None:
        idx = col_index(letter)
        found = self._cols(idx)
        value = _fmt_num(width)
        if len(found) > 1 or (found and _col_span(found[0].group(0)) != (idx, idx)):
            raise PatchError(f"Column {letter} shares its settings with other columns.")
        if found:
            m = found[0]
            new = _set_attr(_set_attr(m.group(0), "width", value), "customWidth", "1")
            self.text = self.text[: m.start()] + new + self.text[m.end() :]
            return
        p = self.p
        new_col = f'<{p}col min="{idx}" max="{idx}" width="{value}" customWidth="1"/>'
        cols = re.search(rf"<{re.escape(p)}cols(?=[\s>])[^>]*>", self.text)
        if cols:
            after = [m for m in _element(self.text, p, "col") if _col_span(m.group(0))[0] > idx]
            pos = after[0].start() if after else self.text.index(f"</{p}cols>")
            self.text = self.text[:pos] + new_col + self.text[pos:]
        else:
            pos = re.search(rf"<{re.escape(p)}sheetData(?=[\s/>])", self.text).start()
            self.text = self.text[:pos] + f"<{p}cols>{new_col}</{p}cols>" + self.text[pos:]

    # visibility ---------------------------------------------------------

    def unhide_row(self, row: int) -> None:
        m = re.search(rf'<{re.escape(self.p)}row(?=\s)[^>]*?\sr="{row}"[^>]*?/?>', self.text)
        if m and _attrs(m.group(0)).get("hidden") in {"1", "true"}:
            self.text = self.text[: m.start()] + _remove_attr(m.group(0), "hidden") + self.text[m.end() :]

    def unhide_column(self, letter: str) -> None:
        idx = col_index(letter)
        for m in self._cols(idx):
            if _attrs(m.group(0)).get("hidden") in {"1", "true"}:
                if _col_span(m.group(0)) != (idx, idx):
                    raise PatchError(f"Column {letter} is hidden together with other columns.")
                self.text = self.text[: m.start()] + _remove_attr(m.group(0), "hidden") + self.text[m.end() :]
                return


def _col_span(col_tag: str) -> tuple[int, int]:
    a = _attrs(col_tag)
    return int(a.get("min", "0")), int(a.get("max", "0"))


# ---------------------------------------------------------------- styles


class _Styles:
    def __init__(self, text: str):
        self.text = text
        self.p = _prefix(text, "styleSheet")
        self._xf_cache: dict[tuple[int, FontSpec, FontSpec], int] = {}

    def _block(self, name: str) -> re.Match:
        m = re.search(
            rf"<{re.escape(self.p)}{name}(?=[\s>])[^>]*>(.*?)</{re.escape(self.p)}{name}>", self.text, re.DOTALL
        )
        if not m:
            raise PatchError(f"The workbook styles have no <{name}> list.")
        return m

    def _items(self, block: str, item: str) -> list[str]:
        m = self._block(block)
        return [x.group(0) for x in _element(self.text, self.p, item, m.start(1), m.end(1))]

    def _append(self, block: str, item: str, element: str) -> int:
        """Append (or reuse an identical) element; returns its index."""
        items = self._items(block, item)
        if element in items:
            return items.index(element)
        m = self._block(block)
        close = m.end(1)
        self.text = self.text[:close] + element + self.text[close:]
        m = self._block(block)
        opening = self.text[m.start() : m.start(1)]
        self.text = self.text[: m.start()] + _set_attr(opening, "count", str(len(items) + 1)) + self.text[m.start(1) :]
        return len(items)

    def font(self, index: int) -> str:
        fonts = self._items("fonts", "font")
        if index >= len(fonts):
            raise PatchError("The cell refers to a font that does not exist.")
        return fonts[index]

    def xf(self, index: int) -> str:
        xfs = self._items("cellXfs", "xf")
        if index >= len(xfs):
            raise PatchError("The cell refers to a cell format that does not exist.")
        return xfs[index]

    def derived_xf(self, xf_index: int, before: FontSpec, after: FontSpec) -> int:
        key = (xf_index, before, after)
        if key in self._xf_cache:
            return self._xf_cache[key]
        xf = self.xf(xf_index)
        xf_attrs = _attrs(_opening(xf))
        new_xf = xf
        font_change = after.name is not None or after.size is not None or after.bold is not None
        if font_change:
            new_font = self._changed_font(self.font(int(xf_attrs.get("fontId", "0"))), after)
            font_id = self._append("fonts", "font", new_font)
            new_xf = self._replace_opening(
                new_xf, _set_attr(_set_attr(_opening(new_xf), "fontId", str(font_id)), "applyFont", "1")
            )
        if after.wrap is not None:
            new_xf = self._with_wrap(new_xf, after.wrap)
        index = self._append("cellXfs", "xf", new_xf)
        self._xf_cache[key] = index
        return index

    @staticmethod
    def _replace_opening(element: str, opening: str) -> str:
        return opening + element[len(_opening(element)) :]

    def _changed_font(self, font: str, after: FontSpec) -> str:
        p = self.p
        if font.endswith("/>"):
            font = font[:-2].rstrip() + f"></{p}font>"
        close = f"</{p}font>"
        body = font[len(_opening(font)) : -len(close)]
        pe = re.escape(p)
        if after.name is not None:
            # A theme 'scheme' would override the explicit name; drop it so the new name applies.
            body = re.sub(rf"<{pe}scheme\b[^>]*/>", "", body)
            body = re.sub(rf"<{pe}family\b[^>]*/>", "", body)
            name = f'<{p}name val="{html.escape(after.name, quote=True)}"/>'
            body = re.sub(rf"<{pe}name\b[^>]*/>", name, body) if re.search(rf"<{pe}name\b", body) else body + name
        if after.size is not None:
            sz = f'<{p}sz val="{_fmt_num(after.size)}"/>'
            body = re.sub(rf"<{pe}sz\b[^>]*/>", sz, body) if re.search(rf"<{pe}sz\b", body) else sz + body
        if after.bold is not None:
            body = re.sub(rf"<{pe}b(?=[\s/>])[^>]*/>", "", body)
            if after.bold:
                body = f"<{p}b/>" + body
        return _opening(font) + body + close

    def _with_wrap(self, xf: str, wrap: bool) -> str:
        p = self.p
        if xf.endswith("/>"):
            xf = xf[:-2].rstrip() + f"></{p}xf>"
        opening = _set_attr(_opening(xf), "applyAlignment", "1")
        body = xf[len(_opening(xf)) : -len(f"</{p}xf>")]
        m = re.search(rf"<{re.escape(p)}alignment\b[^>]*/>", body)
        if m:
            alignment = _set_attr(m.group(0), "wrapText", "1") if wrap else _remove_attr(m.group(0), "wrapText")
            body = body[: m.start()] + alignment + body[m.end() :]
        elif wrap:
            body = f'<{p}alignment wrapText="1"/>' + body
        return opening + body + f"</{p}xf>"


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    text = zf.read("xl/sharedStrings.xml").decode("utf-8")
    p = _prefix(text, "sst")
    return [m.group(0) for m in _element(text, p, "si")]


# ---------------------------------------------------------------- build


@dataclass
class BuildResult:
    data: bytes
    edited_parts: set[str] = field(default_factory=set)
    removed_parts: set[str] = field(default_factory=set)
    formula_cells: list[tuple[str, str]] = field(default_factory=list)


Change = SetValue | SetFormula | SetFont | SetColumnWidth


def build(original: bytes, changes: Sequence[Change], reveals: Sequence[VisibilityRequest] = ()) -> BuildResult:
    """Return a new package with the changes applied in order. The original bytes are never modified."""
    zf = zipfile.ZipFile(io.BytesIO(original))
    sheets = {s.name: s for s in workbook_sheets(zf)}
    edited: dict[str, str] = {}
    sheet_objs: dict[str, _Sheet] = {}
    styles: _Styles | None = None
    shared: list[str] | None = None
    result = BuildResult(data=b"")
    cell_content_changed = False

    def sheet(name: str) -> tuple[str, _Sheet]:
        entry = sheets.get(name)
        if entry is None:
            raise PatchError(f"Sheet '{name}' is not in the workbook.")
        if entry.part is None or entry.rel_type != WORKSHEET_TYPE:
            raise PatchError(f"'{name}' is not a worksheet.")
        if entry.part not in sheet_objs:
            sheet_objs[entry.part] = _Sheet(zf.read(entry.part).decode("utf-8"))
        return entry.part, sheet_objs[entry.part]

    for change in changes:
        if isinstance(change, (SetValue, SetFormula)):
            _, sh = sheet(change.sheet)
            ref = change.cell
            if isinstance(change, SetValue):
                sh.set_value(ref, change.after)
            else:
                sh.set_formula(ref, change.after)
                result.formula_cells.append((change.sheet, ref))
            cell_content_changed = True
        elif isinstance(change, SetFont):
            _, sh = sheet(change.sheet)
            if styles is None:
                styles = _Styles(zf.read("xl/styles.xml").decode("utf-8"))
            if shared is None:
                shared = _shared_strings(zf)
            bounds = parse_range(change.target)
            if bounds is None:
                raise PatchError(f"'{change.target}' is not a valid target.")
            for ref in bounds.cells():
                if (change.after.name or change.after.size or change.after.bold is not None) and sh.cell_has_rich_text(
                    ref, shared
                ):
                    raise PatchError(f"Cell {ref} contains rich text with its own fonts; change it in Excel.")
                sh.set_cell_style(ref, styles.derived_xf(sh.cell_style(ref), change.before, change.after))
        elif isinstance(change, SetColumnWidth):
            _, sh = sheet(change.sheet)
            sh.set_column_width(change.column, change.after)
        else:  # pragma: no cover - guarded by the type
            raise PatchError(f"Unsupported change: {change!r}")

    wb_text: str | None = None
    for req in reveals:
        if req.what == "sheet":
            wb_text = _reveal_sheet(wb_text or zf.read("xl/workbook.xml").decode("utf-8"), req.sheet)
        else:
            _, sh = sheet(req.sheet)
            if req.what == "row":
                sh.unhide_row(int(req.ref))
            else:
                sh.unhide_column(req.ref)

    for part, sh in sheet_objs.items():
        edited[part] = sh.text
    if styles is not None:
        edited["xl/styles.xml"] = styles.text
    if result.formula_cells:
        wb_text = _full_calc_on_load(wb_text or zf.read("xl/workbook.xml").decode("utf-8"))
    if wb_text is not None:
        edited["xl/workbook.xml"] = wb_text
    names = set(zf.namelist())
    if cell_content_changed and CALC_CHAIN in names:
        result.removed_parts.add(CALC_CHAIN)
        edited["xl/_rels/workbook.xml.rels"] = re.sub(
            r'<Relationship\b[^>]*Target="(?:/xl/)?calcChain\.xml"[^>]*/>',
            "",
            zf.read("xl/_rels/workbook.xml.rels").decode("utf-8"),
        )
        edited["[Content_Types].xml"] = re.sub(
            r'<Override\b[^>]*PartName="/xl/calcChain\.xml"[^>]*/>', "", zf.read("[Content_Types].xml").decode("utf-8")
        )

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zf.infolist():
            if info.filename in result.removed_parts:
                continue
            original_part = zf.read(info.filename)
            if info.filename in edited:
                new_part = edited[info.filename].encode("utf-8")
                if new_part != original_part:
                    result.edited_parts.add(info.filename)
                zout.writestr(info, new_part)
            else:
                zout.writestr(info, original_part)
    result.data = out.getvalue()
    return result


def _reveal_sheet(wb_text: str, name: str) -> str:
    p = _prefix(wb_text, "workbook")
    for m in _element(wb_text, p, "sheet"):
        if _attrs(m.group(0)).get("name") == name:
            return wb_text[: m.start()] + _remove_attr(m.group(0), "state") + wb_text[m.end() :]
    raise PatchError(f"Sheet '{name}' is not in the workbook.")


def _full_calc_on_load(wb_text: str) -> str:
    p = re.escape(_prefix(wb_text, "workbook"))
    m = re.search(rf"<{p}calcPr\b[^>]*?/?>", wb_text)
    if m:
        return wb_text[: m.start()] + _set_attr(m.group(0), "fullCalcOnLoad", "1") + wb_text[m.end() :]
    raw_p = _prefix(wb_text, "workbook")
    anchor = re.search(rf"</{p}definedNames>", wb_text) or re.search(rf"</{p}sheets>", wb_text)
    if not anchor:
        raise PatchError("The workbook has no sheet list.")
    return wb_text[: anchor.end()] + f'<{raw_p}calcPr fullCalcOnLoad="1"/>' + wb_text[anchor.end() :]


# ---------------------------------------------------------------- reading helpers used by the stale check


def column_width(original: bytes, sheet_name: str, letter: str) -> tuple[float | None, bool]:
    zf = zipfile.ZipFile(io.BytesIO(original))
    entry = next((s for s in workbook_sheets(zf) if s.name == sheet_name), None)
    if entry is None or entry.part is None:
        raise PatchError(f"Sheet '{sheet_name}' is not in the workbook.")
    return _Sheet(zf.read(entry.part).decode("utf-8")).column_width(letter)
