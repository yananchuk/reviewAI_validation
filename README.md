# Review AI: web app (MVP)

A local Streamlit app for working through the findings of a `<stem>.review.json` report (schema 0.3) produced by
the `workpaper-quality` skill. You upload the reviewed workbook and the report, review one finding at a time,
apply safe Quick Fixes to a copy of the workbook, record decisions for Follow-ups, and download the modified
workbook and a review log.

The app does not run the review skill, does not re-evaluate rules, never resolves a Follow-up automatically, never
changes the uploaded file and makes no network calls. It consumes the `review.json` contract in
[`shared/`](../../shared/README.md).

Design: [docs/superpowers/specs/2026-11-15-review-web-app-design.md](../../docs/superpowers/specs/2026-11-15-review-web-app-design.md).

## Run

Requires [uv](https://docs.astral.sh/uv/). From this folder:

```bash
uv sync --frozen
uv run --frozen streamlit run app.py
```

Open http://localhost:8501. The server listens on `localhost` only, and usage statistics are off (`.streamlit/config.toml`).

## Workflow

1. **Upload and validate.** Upload the workbook (`.xlsx` or `.xlsm`) and the report (`.json`). The report must have a
   supported `schema_version` (0.3); `skill_version` is shown but never decides compatibility. If the report names a
   different workbook, you must confirm before the review starts.
2. **Review.** The page has two parts, like the prototype `workpaper-review-V6_2`:
   - **Workbook (left):** the sheet as Excel shows it: fonts, colours (theme colours included), borders, alignment,
     column widths, row heights and merged cells, with sheet tabs at the bottom (hidden sheets greyed, with the
     number of open findings per sheet). Cells show their saved results with common number formats; *Show formulas*
     shows the formulas instead. Cells with findings carry a marker (■ Quick fix, ◆ Follow-up, faded once
     reviewed); the current finding is shaded, outlined and scrolled into view. Clicking a marked cell opens its
     finding; clicking any cell shows its address and formula in the formula bar.
   - **Decision panel (right):** Quick fixes / Follow-up switch, progress, *Filter* (status, rule, worksheet,
     theme), the finding list, *Finish review*, and the finding card: message, location chip, possible duplicates
     and other findings at the same cell, the proposed change, the decision controls, the comment, hidden-content
     choices, *Cell details* (value, formula, formatting and visibility as text), and Back / Next / Next open.
3. **Decide.**
   - Quick Fix: *Apply fix* (only when the change is deterministic), *Reject*, *Mark as manually resolved*, *Keep open*,
     and *Undo* after applying. If the workbook no longer matches the report you see "The workbook has changed since
     this report was created." with *Cancel*, *Apply anyway*, *Mark as manually resolved* and *Keep open*.
   - Follow-up: *Resolved*, *Not applicable* or *Keep open*. Resolved and Not applicable need an explanation of at
     least 10 characters; placeholders such as TBD, TODO or N/A are refused.
4. **Finish.** *Generate outputs* rebuilds the workbook from the original plus your applied changes, runs the
   integrity checks and offers the downloads:
   `<stem>_reviewed.xlsx|.xlsm`, `<stem>_review_log.json` and `<stem>_review_package.zip`.

## Validate the skill

On the upload page, choose *Validate the skill (label findings and score it)* instead of *Review the workpaper*. The
validator measures the skill against the workpaper; it never changes the workbook. Four steps, shown as
*1 Label → 2 Missed → 3 Check → 4 Results*:

1. **Label:** mark each finding *Real problem*, *False alarm* or *Duplicate*, with an optional comment.
2. **Missed:** select a cell or range in the workbook (click, shift+click or drag), pick the rule (or *Other*) and add
   a note. These are problems the skill did not report.
3. **Check:** all labels and misses on one page, with warnings for unlabelled findings and open suggestions.
4. **Results:** F2, recall and precision, the outcome counts, a per-rule table and the report download.

What is counted: **TP** = real problems labelled *Real problem*; **FP** = *False alarm*; **FN** = accepted misses.
Duplicate and unlabelled findings are not counted. Recall = TP ÷ (TP + FN), precision = TP ÷ (TP + FP),
F2 = 5PR ÷ (4P + R). A ratio that cannot be computed (zero denominator) shows "n/a"; F2 and F1 are 0 when recall
and precision are both 0.

On a trigger workpaper, rule tags in column A form an answer key. Tags with no matching finding appear as suggested
misses; confirm or dismiss each one. Nothing counts until it is confirmed. On a real workpaper there is no answer key,
so recall is measured against the problems the validator found.

*Download report* saves `<stem>_skill_validation.md`: run details, the score, the outcome and per-rule tables, the
labelled findings, the misses and notes. Nothing is written to the workbook, and the validation lives only in the
browser tab: download the report before closing it. Method: [docs/VALIDATION_METHODOLOGY.md](../../docs/VALIDATION_METHODOLOGY.md);
design: [docs/superpowers/specs/2026-11-17-validator-mode-design.md](../../docs/superpowers/specs/2026-11-17-validator-mode-design.md).

## What is applied automatically

| Change in the report | Example | Applied |
|---|---|---|
| Text or number in one cell | `The invoices was checked.` → `…were checked.` | yes, if the cell still holds the 'before' value |
| Formula in one cell | `=SUM(B67:B68)` → `=SUM(B67:B69)`; typed `120` → `=A75*B75` (case `typed_value_replacing_formula`) | yes; Excel recalculates on open |
| Font name, size, bold, wrap | `font Carlito 11` → `font Arial 10`, `Calibri 16 bold` → `Arial 11 bold`, `…, no wrap` → `…, wrap text enabled` | yes, per cell, only the named properties |
| Column width | `column H width 2.0` → `column H width 15.375` | yes, if the column has its own width setting |
| Anything else | alternatives ("… or …"), ranges for values, narrower targets than reported, merged-cell interiors, protected sheets, sheet renames | no: *Mark as manually resolved* after changing it in Excel |

## How the workbook is written

The workbook is **never re-saved through openpyxl**, which would drop parts it does not know (sensitivity-label
metadata, threaded comments, slicers, custom XML, …). `review_app/package_patch.py` copies every package part byte
for byte and replaces only the exact XML element text of the changed cells or columns; fonts and cell formats are
appended to `styles.xml`. After cell edits `calcChain.xml` is removed and `fullCalcOnLoad` is set, so Excel recalculates
on open. openpyxl is used only to read.

Before a modified workbook can be downloaded these checks must pass: the output reopens; sheet names, order and
visibility are unchanged (except reveals you asked for); every formula and value outside the approved targets is
unchanged; the targets hold the approved change; merged ranges and defined names are unchanged; every other package
part is byte-identical; the VBA project of an `.xlsm` is unchanged; the extension is kept; the uploaded bytes are
unchanged. If any check fails, only the review log can be downloaded.

## Formats and limits

| | |
|---|---|
| `.xlsx`, `.xlsm` | review and write |
| digitally signed workbooks | review only (log export) |
| `.xls`, `.xlsb`, password-protected files | refused: save as `.xlsx` / `.xlsm` first |
| Workbook / report size | 25 MB / 5 MB |
| Findings per report | 5,000 |
| Workbook view | whole sheet up to 20,000 cells; larger sheets show 200 rows around the current finding |

Not shown in the workbook view: conditional formatting, data-validation arrows, comments and notes (a reported
comment location is still marked), images, charts, text boxes, freeze panes, outline groups and zoom. Uncommon number
formats show the raw value. Nothing is recalculated: a formula edited by a fix shows its formula until Excel opens the
file.

## Data handling

Everything stays in memory in this process: nothing is written to disk and nothing is sent anywhere. Logging records
metadata only (counts, ids), never cell values or comments. **The review is lost when the browser tab is closed or
refreshed**: download the review log before you leave.

## Development

```bash
uv run --frozen pytest -q          # unit, workbook and Streamlit AppTest tests
uv run --frozen ruff check .
uv lock --check
```

Tests use synthetic workbooks only. To also run them on a real report and workbook (never commit those files):

```bash
REVIEWAI_EXAMPLE_JSON=/path/to/WP.review.json REVIEWAI_EXAMPLE_XLSX=/path/to/WP.xlsx uv run --frozen pytest -q
```

`review_app/schema/review-report.schema.json` is a copy of `shared/review-report.schema.json`; a test fails if they
differ. When the skill schema changes: skill schema → `shared/` → this copy.

## Manual acceptance (in Excel)

For the Trigger example and two Cash `.xlsm` dummies, apply a value, a formula, a font and a column-width fix, generate
the outputs and open the reviewed workbook in Excel Desktop:

- [ ] no repair prompt;
- [ ] edited formulas show results after opening (for example `Lead!B70` = 600);
- [ ] fonts and column widths changed only where approved;
- [ ] sensitivity label and macros (for `.xlsm`) are still present;
- [ ] the uploaded workbook is unchanged.

## Manual acceptance (browser)

Not yet run (the automated tests cannot drive a browser; the workbook component was checked in a simulated DOM).
With the Trigger pair and one Cash `.xlsm` dummy:

- [ ] the current finding's sheet is shown, its cell outlined and scrolled into view;
- [ ] markers are visible; clicking a marked cell opens its finding; clicking another cell updates the formula bar;
- [ ] *Show formulas* switches the cells and stays on when moving to another finding;
- [ ] sheet tabs switch sheets; hidden sheets appear greyed with "(hidden)";
- [ ] *Apply fix* shows the new value or format in the grid at once; *Undo* restores it;
- [ ] the largest Cash sheet (about 4,500 cells) appears within about two seconds;
- [ ] the page fits a laptop screen without scrolling the whole page.
