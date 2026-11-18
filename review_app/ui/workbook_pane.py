"""The workbook pane: an Excel-like, clickable view of the active sheet (Streamlit inline component, no build step).

Security: the component is trusted code but is not sandboxed. Workbook content therefore travels only as JSON
`data` and is put on the page with `textContent`; CSS values come from validated values (sheet_view._css).
The HTML, CSS and JS below are constants and never contain workbook content."""

from __future__ import annotations

import re
from typing import Any

import streamlit as st

from review_app import limits
from review_app import review_session as rs
from review_app.addresses import col_letter, parse_cell, parse_range
from review_app.sheet_view import sheet_payload, sheet_tabs, visible_cell
from review_app.ui.state import get_state

COMPONENT_KEY = "workbook"
_SELECTION = re.compile(r"^[A-Z]{1,3}\d+(:[A-Z]{1,3}\d+)?$")

HTML = '<div class="rai-root"></div>'

CSS = f"""
.rai-root {{ display: flex; flex-direction: column; height: {limits.PANE_HEIGHT_CSS};
  min-height: {limits.PANE_MIN_HEIGHT_PX}px; border: 1px solid #D2D0CE;
  border-radius: 6px; overflow: hidden; background: #FFFFFF; color: #1F1F1F;
  font: 13px "Segoe UI", system-ui, -apple-system, sans-serif; }}
.rai-bar {{ display: flex; align-items: center; gap: 6px; padding: 4px 8px; background: #FAF9F8;
  border-bottom: 1px solid #D2D0CE; }}
.rai-name {{ width: 150px; border: 1px solid #D2D0CE; background: #FFF; padding: 2px 6px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }}
.rai-fx {{ color: #605E5C; font-style: italic; font-family: Georgia, serif; padding: 0 4px; }}
.rai-content {{ flex: 1; border: 1px solid #D2D0CE; background: #FFF; padding: 2px 6px; min-height: 20px;
  white-space: pre-wrap; overflow: hidden; max-height: 60px; overflow-y: auto; }}
.rai-format {{ padding: 2px 8px 3px; font-size: 12px; color: #605E5C; background: #FAF9F8;
  border-bottom: 1px solid #D2D0CE; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-height: 16px; }}
.rai-toggle {{ font-size: 12px; color: #323130; white-space: nowrap; cursor: pointer; }}
.rai-note {{ padding: 3px 8px; font-size: 12px; background: #FFF4CE; border-bottom: 1px solid #D2D0CE; }}
.rai-scroll {{ flex: 1; overflow: auto; position: relative; }}
table.rai-grid {{ border-collapse: collapse; table-layout: fixed;
  font: 11pt Calibri, Carlito, Arial, sans-serif; }}
.rai-grid th {{ background: #F3F2F1; color: #605E5C; font: 11px "Segoe UI", sans-serif; font-weight: 400;
  border: 1px solid #D2D0CE; position: sticky; z-index: 2; }}
.rai-grid thead th {{ top: 0; height: 20px; }}
.rai-grid th.rai-rh {{ left: 0; width: 40px; z-index: 3; }}
.rai-grid thead th.rai-rh {{ z-index: 4; }}
.rai-grid td {{ border: 1px solid #E1DFDD; padding: 0 3px; white-space: nowrap; overflow: hidden;
  vertical-align: bottom; position: relative; cursor: cell; }}
.rai-grid td.rai-num {{ text-align: right; }}
.rai-grid td.rai-grey {{ color: #8A8886; font-style: italic; }}
.rai-grid td.rai-hl {{ background-color: #FFF6C9 !important; }}
.rai-grid td.rai-target {{ outline: 2px solid #217346; outline-offset: -2px; z-index: 1; }}
.rai-grid td.rai-sel {{ box-shadow: inset 0 0 0 2px #0F6CBD; }}
.rai-grid .rai-after-hidden-row > * {{ border-top: 3px double #8A8886; }}
.rai-grid .rai-after-hidden-col {{ border-left: 3px double #8A8886 !important; }}
.rai-flag {{ position: absolute; top: 1px; left: 1px; width: 7px; height: 7px; pointer-events: none; }}
.rai-flag.qf {{ background: #4F6482; border-radius: 1px; }}
.rai-flag.fu {{ background: #A85F00; transform: rotate(45deg); width: 6px; height: 6px; }}
.rai-flag.done {{ opacity: .35; }}
.rai-grid td.rai-flagged {{ cursor: pointer; }}
.rai-flag.miss {{ left: auto; right: 1px; background: #C50F1F; border-radius: 50%; }}
.rai-grid td.rai-pick {{ box-shadow: inset 0 0 0 2px #C50F1F; background: #FDE7E9 !important; }}
.rai-picking {{ user-select: none; }}
.rai-tabs {{ display: flex; gap: 2px; padding: 0 6px; background: #F3F2F1; border-top: 1px solid #D2D0CE;
  overflow-x: auto; min-height: 30px; align-items: stretch; }}
.rai-tab {{ border: none; background: none; padding: 5px 12px; font: 12px "Segoe UI", sans-serif; color: #323130;
  border-bottom: 3px solid transparent; white-space: nowrap; cursor: pointer; }}
.rai-tab.active {{ background: #FFFFFF; font-weight: 600; color: #217346; }}
.rai-tab.hidden {{ font-style: italic; color: #8A8886; }}
.rai-count {{ margin-left: 6px; background: #C50F1F; color: #FFF; border-radius: 8px; padding: 0 5px;
  font-size: 10px; font-weight: 600; }}
"""

JS = r"""
const HEX = /^#[0-9A-F]{6}$/;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function colLetter(n) {
  let s = "";
  while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); }
  return s;
}

export default function (component) {
  const { data, parentElement, setTriggerValue, setStateValue } = component;
  const root = parentElement.querySelector(".rai-root");
  if (!root) return;
  root.replaceChildren();
  if (!data || !data.payload) return;
  const p = data.payload;
  const flags = data.flags || {};
  const highlight = new Set(data.highlight || []);
  const selectMode = !!data.select_mode;
  const misses = selectMode ? (data.misses || {}) : {};
  let showFormulas = !!data.formulas;

  // Formula bar
  const bar = el("div", "rai-bar");
  const nameBox = el("div", "rai-name");
  const content = el("div", "rai-content");
  const toggleLabel = el("label", "rai-toggle");
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = showFormulas;
  toggleLabel.append(toggle, document.createTextNode(" Show formulas"));
  bar.append(nameBox, el("div", "rai-fx", "fx"), content, toggleLabel);
  const formatLine = el("div", "rai-format");
  root.append(bar, formatLine);
  if (p.window) {
    const w = p.window;
    root.append(el("div", "rai-note",
      `Large sheet: rows ${w.first_row} to ${w.last_row} of ${w.total_rows} and columns ` +
      `${colLetter(w.first_col)} to ${colLetter(w.last_col)} are shown, around the current finding.`));
  }

  // Grid
  const scroll = el("div", "rai-scroll");
  const table = el("table", "rai-grid");
  const hiddenRows = new Set(p.hidden_rows || []);
  const hiddenCols = new Set(p.hidden_cols || []);
  const cols = (p.cols || []).filter((c) => !hiddenCols.has(c.c));
  const rows = (p.rows || []).filter((r) => !hiddenRows.has(r.r));
  const visibleCol = new Set(cols.map((c) => c.c));
  const visibleRow = new Set(rows.map((r) => r.r));

  const colgroup = document.createElement("colgroup");
  const rh = document.createElement("col");
  rh.style.width = "40px";
  colgroup.append(rh);
  let width = 40;
  for (const c of cols) {
    const col = document.createElement("col");
    const px = Math.max(4, Math.round(Number(c.w) * 7 + 5));
    col.style.width = px + "px";
    width += px;
    colgroup.append(col);
  }
  table.style.width = width + "px";
  table.append(colgroup);

  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  hr.append(el("th", "rai-rh"));
  for (const c of cols) {
    const th = el("th", hiddenCols.has(c.c - 1) ? "rai-after-hidden-col" : "", c.letter);
    hr.append(th);
  }
  thead.append(hr);
  table.append(thead);

  // Merges: anchor -> spans over visible rows/cols; covered cells are skipped.
  const spans = new Map();
  const covered = new Set();
  for (const m of p.merges || []) {
    let rs = 0, cs = 0;
    for (let r = m.r1; r <= m.r2; r++) if (visibleRow.has(r)) rs++;
    for (let c = m.c1; c <= m.c2; c++) if (visibleCol.has(c)) cs++;
    spans.set(colLetter(m.c1) + m.r1, { rs, cs });
    for (let r = m.r1; r <= m.r2; r++)
      for (let c = m.c1; c <= m.c2; c++)
        if (r !== m.r1 || c !== m.c1) covered.add(colLetter(c) + r);
  }

  const cellNodes = new Map();
  const tbody = document.createElement("tbody");
  let previousRow = null;
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.style.height = Math.max(4, Math.round(Number(row.h) * 4 / 3)) + "px";
    if (previousRow !== null && row.r - previousRow > 1) tr.className = "rai-after-hidden-row";
    previousRow = row.r;
    tr.append(el("th", "rai-rh", row.r));
    for (const c of cols) {
      const addr = c.letter + row.r;
      if (covered.has(addr)) continue;
      const td = document.createElement("td");
      td.setAttribute("data-addr", addr);
      if (hiddenCols.has(c.c - 1)) td.classList.add("rai-after-hidden-col");
      const span = spans.get(addr);
      if (span) {
        if (span.cs > 1) td.colSpan = span.cs;
        if (span.rs > 1) td.rowSpan = span.rs;
      }
      const cell = p.cells[addr];
      if (cell) {
        const style = p.styles[String(cell.s)] || {};
        for (const [name, value] of Object.entries(style)) td.style.setProperty(name, String(value));
        if (cell.g) td.classList.add("rai-grey");
        if (cell.n && !style["text-align"]) td.classList.add("rai-num");
      }
      const text = el("span", "", "");
      td.append(text);
      const here = flags[addr];
      if (here && here.length) {
        if (!selectMode) td.classList.add("rai-flagged");
        const f = here[0];
        const done = here.every((x) => x.reviewed) ? " done" : "";
        const mark = el("span", "rai-flag " + (f.type === "quick_fix" ? "qf" : "fu") + done);
        td.append(mark);
        td.title = here.map((x) => x.id).join(", ");
      }
      const missed = misses[addr];
      if (missed && missed.length) {
        td.append(el("span", "rai-flag miss"));
        td.title = (td.title ? td.title + " · " : "") + "missed: " + missed.join(", ");
      }
      if (highlight.has(addr)) td.classList.add("rai-hl");
      if (addr === data.target) td.classList.add("rai-target");
      cellNodes.set(addr, { td, text, cell });
      tr.append(td);
    }
    tbody.append(tr);
  }
  table.append(tbody);
  scroll.append(table);
  root.append(scroll);

  function paintTexts() {
    for (const { text, cell } of cellNodes.values()) {
      text.textContent = cell ? (showFormulas && cell.f ? cell.f : cell.t) : "";
    }
  }
  let selected = null;
  function show(addr) {
    const node = cellNodes.get(addr);
    if (selected) selected.classList.remove("rai-sel");
    selected = node ? node.td : null;
    if (selected) selected.classList.add("rai-sel");
    nameBox.textContent = addr ? `${p.sheet}!${addr}` : p.sheet;
    const cell = node ? node.cell : null;
    content.textContent = cell ? (cell.f || cell.t) : "";
    formatLine.textContent = describe(addr, cell);
    formatLine.title = formatLine.textContent;
  }
  const colWidth = new Map((p.cols || []).map((c) => [colLetter(c.c), c.w]));
  const rowHeight = new Map((p.rows || []).map((r) => [String(r.r), r.h]));
  function describe(addr, cell) {
    if (!addr) return "";
    const m = /^([A-Z]+)(\d+)$/.exec(addr);
    const parts = [];
    const fmt = cell && p.formats ? p.formats[String(cell.s)] : "";
    parts.push(fmt || "default formatting");
    if (m) {
      const w = colWidth.get(m[1]);
      const h = rowHeight.get(m[2]);
      if (w !== undefined) parts.push(`column width ${Number(w).toFixed(2).replace(/\.?0+$/, "")}`);
      if (h !== undefined) parts.push(`row height ${Number(h).toFixed(2).replace(/\.?0+$/, "")} pt`);
    }
    return parts.join(" · ");
  }
  paintTexts();

  if (!selectMode) {
    table.addEventListener("click", (event) => {
      const td = event.target.closest("td[data-addr]");
      if (!td) return;
      const addr = td.getAttribute("data-addr");
      show(addr);
      if (flags[addr] && flags[addr].length) setTriggerValue("cell", { sheet: p.sheet, cell: addr });
    });
  } else {
    // Select mode (skill validator): pick a cell or a rectangle; never opens findings.
    const ADDR = /^([A-Z]{1,3})(\d+)$/;
    function colIndex(letters) {
      let n = 0;
      for (const ch of letters) n = n * 26 + (ch.charCodeAt(0) - 64);
      return n;
    }
    function pos(addr) {
      const m = ADDR.exec(addr || "");
      return m ? { c: colIndex(m[1]), r: Number(m[2]) } : null;
    }
    let anchor = null, corner = null, dragging = false, lastSent = null;
    function rangeText() {
      if (!anchor || !corner) return null;
      const c1 = Math.min(anchor.c, corner.c), c2 = Math.max(anchor.c, corner.c);
      const r1 = Math.min(anchor.r, corner.r), r2 = Math.max(anchor.r, corner.r);
      const first = colLetter(c1) + r1;
      return c1 === c2 && r1 === r2 ? first : first + ":" + colLetter(c2) + r2;
    }
    function paintPick() {
      const range = rangeText();
      const a = pos(range ? range.split(":")[0] : null);
      const b = pos(range ? range.split(":").pop() : null);
      for (const [addr, { td }] of cellNodes) {
        const q = pos(addr);
        const inside = a && b && q && q.c >= a.c && q.c <= b.c && q.r >= a.r && q.r <= b.r;
        td.classList.toggle("rai-pick", !!inside);
      }
      if (range) nameBox.textContent = `${p.sheet}!${range}`;
    }
    function send() {
      const range = rangeText();
      if (!range || range === lastSent) return;
      lastSent = range;
      setTriggerValue("selection", { sheet: p.sheet, range });
    }
    function cellAt(event) {
      const td = event.target.closest ? event.target.closest("td[data-addr]") : null;
      return td && table.contains(td) ? td.getAttribute("data-addr") : null;
    }
    table.addEventListener("mousedown", (event) => {
      if (event.button !== 0) return;
      const addr = cellAt(event);
      const here = pos(addr);
      if (!here) return;
      event.preventDefault();
      if (!(event.shiftKey && anchor)) { anchor = here; show(addr); }
      corner = here;
      dragging = true;
      table.classList.add("rai-picking");
      table.ownerDocument.addEventListener("mouseup", finish, { once: true });  // also when released outside
      paintPick();
    });
    table.addEventListener("mouseover", (event) => {
      if (!dragging) return;
      if (!(event.buttons & 1)) { finish(); return; }  // released outside the window
      const here = pos(cellAt(event));
      if (!here || (corner && here.c === corner.c && here.r === corner.r)) return;
      corner = here;
      paintPick();
    });
    function finish() {
      if (!dragging) return;
      dragging = false;
      table.classList.remove("rai-picking");
      send();
    }
    table.addEventListener("click", () => { finish(); send(); });
    const initial = typeof data.selection === "string" ? data.selection.split(":") : [];
    if (initial.length && pos(initial[0])) {
      anchor = pos(initial[0]);
      corner = pos(initial[initial.length - 1]) || anchor;
      lastSent = rangeText();
      requestAnimationFrame(paintPick);
    }
  }
  toggle.addEventListener("change", () => {
    showFormulas = toggle.checked;
    setStateValue("formulas", showFormulas);
    paintTexts();
  });

  // Sheet tabs
  const tabs = el("div", "rai-tabs");
  for (const t of data.tabs || []) {
    const label = t.name + (t.state === "hidden" ? " (hidden)" : t.state === "veryHidden" ? " (very hidden)" : "");
    const cls = "rai-tab" + (t.name === p.sheet ? " active" : "") + (t.state !== "visible" ? " hidden" : "");
    const button = el("button", cls, label);
    button.type = "button";
    if (t.color && HEX.test(t.color)) button.style.setProperty("border-bottom-color", t.color);
    if (t.open > 0) button.append(el("span", "rai-count", t.open));
    button.addEventListener("click", () => { if (t.name !== p.sheet) setTriggerValue("sheet", t.name); });
    tabs.append(button);
  }
  root.append(tabs);

  // Select and scroll to the current finding without moving the page.
  show(data.target || null);
  const target = data.target ? cellNodes.get(data.target) : null;
  if (target) {
    requestAnimationFrame(() => {
      const td = target.td;
      scroll.scrollTop = Math.max(0, td.offsetTop - scroll.clientHeight / 2 + td.offsetHeight / 2);
      scroll.scrollLeft = Math.max(0, td.offsetLeft - scroll.clientWidth / 2 + td.offsetWidth / 2);
    });
  }
}
"""

GRID = st.components.v2.component("review_ai_workbook", html=HTML, css=CSS, js=JS)


def _visible_flags(state: rs.SessionState, payload: dict[str, Any] | None, sheet: str) -> dict[str, list[dict]]:
    """Flags keyed by the cell that shows them (findings in hidden rows/columns move to the next visible cell)."""
    out: dict[str, list[dict]] = {}
    for cell, items in rs.sheet_flags(state, sheet).items():
        key = visible_cell(payload, cell) if payload else cell
        bucket = out.setdefault(key, [])
        bucket.extend(i for i in items if i not in bucket)
    return out


def _visible_misses(state: rs.SessionState, payload: dict[str, Any] | None, sheet: str) -> dict[str, list[str]]:
    """Missed problems on `sheet`, keyed by the visible cell that shows them (a range on its top-left cell)."""
    out: dict[str, list[str]] = {}
    for miss in state.validation.misses:
        bounds = parse_range(miss.ref) if miss.sheet == sheet else None
        if bounds is None:
            continue
        first = f"{col_letter(bounds.min_col)}{bounds.min_row}"  # never build the cell list of a range
        key = visible_cell(payload, first) if payload else first
        out.setdefault(key, []).append(miss.id)
    return out


def pane_data(state: rs.SessionState, formulas: bool, select_mode: bool = False) -> dict[str, Any]:
    """Everything the component needs for the active sheet. Workbook text is data only."""
    sheet = state.active_sheet
    located, highlight, target = (None, [], None)
    if state.current_id is not None:
        located, highlight, target = rs.finding_cells(state, state.current_id)
    if located != sheet:
        highlight, target = [], None
    focus = parse_cell(target) if target else None
    payload = sheet_payload(state.working_bytes, sheet, focus[1], focus[0]) if sheet and focus else None
    if sheet and payload is None:
        payload = sheet_payload(state.working_bytes, sheet)
    if payload:
        target = visible_cell(payload, target) if target else None
        highlight = list(dict.fromkeys(visible_cell(payload, c) for c in highlight))
    open_by_sheet: dict[str, int] = {}
    for fid in state.order:
        if rs.is_reviewed(state, fid) or not rs.matches_filters(state, fid):
            continue
        fsheet, _, _ = rs.finding_cells(state, fid)
        if fsheet is not None:
            open_by_sheet[fsheet] = open_by_sheet.get(fsheet, 0) + 1
    tabs = [{**t, "open": open_by_sheet.get(t["name"], 0)} for t in sheet_tabs(state.working_bytes)]
    selection = state.validation.selection
    return {
        "payload": payload,
        "tabs": tabs,
        "flags": _visible_flags(state, payload, sheet) if sheet else {},
        "highlight": highlight,
        "target": target,
        "formulas": bool(formulas),
        "select_mode": bool(select_mode),
        "misses": _visible_misses(state, payload, sheet) if select_mode and sheet else {},
        "selection": selection[1] if select_mode and selection and selection[0] == sheet else None,
    }


def handle_cell(event: Any) -> str | None:
    """A flagged cell was clicked: open one of the findings shown there."""
    if not isinstance(event, dict) or not isinstance(event.get("sheet"), str) or not isinstance(event.get("cell"), str):
        return None
    state = get_state()
    sheet, cell = event["sheet"], event["cell"]
    if state.profile is None or sheet not in state.profile.sheet_names:
        return None
    payload = sheet_payload(state.working_bytes, sheet)
    ids = [item["id"] for item in _visible_flags(state, payload, sheet).get(cell, [])]
    return rs.open_one_of(state, ids)


def handle_selection(event: Any) -> None:
    """Select mode: remember the picked cell or range as the place of a missed problem."""
    if not isinstance(event, dict):
        return
    sheet, text = event.get("sheet"), event.get("range")
    if not isinstance(sheet, str) or not isinstance(text, str):
        return
    state = get_state()
    if state.profile is None or sheet not in state.profile.sheet_names or not _SELECTION.match(text):
        return
    bounds = parse_range(text)
    if bounds is not None and bounds.size <= limits.MISS_RANGE_MAX_CELLS:
        state.validation.selection = (sheet, bounds.ref)


def handle_sheet(name: Any) -> None:
    if isinstance(name, str):
        rs.set_active_sheet(get_state(), name)


def _event(name: str) -> Any:
    result = st.session_state.get(COMPONENT_KEY)
    return result.get(name) if result is not None else None


def _on_cell() -> None:
    handle_cell(_event("cell"))


def _on_sheet() -> None:
    handle_sheet(_event("sheet"))


def _on_selection() -> None:
    handle_selection(_event("selection"))


def _noop() -> None:
    """Registering a callback is what makes the 'formulas' state persist across reruns."""


def render(state: rs.SessionState, select_mode: bool = False) -> None:
    formulas = bool(_event("formulas") or False)
    GRID(
        key=COMPONENT_KEY,
        data=pane_data(state, formulas, select_mode),
        default={"formulas": False},
        height="content",
        on_cell_change=_on_cell,
        on_sheet_change=_on_sheet,
        on_selection_change=_on_selection,
        on_formulas_change=_noop,
    )
