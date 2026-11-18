"""Limits and constants. Chosen for a local, single-user MVP on ordinary workpapers.

Workpapers are usually well under 5 MB; 25 MB leaves room for large lead schedules while keeping
openpyxl loading (two loads per workbook) within a few seconds.
"""

MiB = 1024 * 1024

MAX_WORKBOOK_BYTES = 25 * MiB
MAX_REPORT_BYTES = 5 * MiB
# Zip-bomb guard: total uncompressed size and number of package parts.
MAX_UNZIPPED_BYTES = 250 * MiB
MAX_ZIP_ENTRIES = 5000

MAX_FINDINGS = 5000
MAX_SCHEMA_ERRORS = 20

PREVIEW_CELLS = 100
EXPANDED_PREVIEW_CELLS = 400
CONTEXT_RADIUS = 2
MAX_TEXT_CHARS = 120
MAX_FONT_TARGET_CELLS = 100

# Only schema versions listed here are accepted (docs/API_review_json.md section 2: before 1.0,
# accept only known minor versions). skill_version is never used for compatibility.
SUPPORTED_SCHEMA_VERSIONS: tuple[str, ...] = ("0.3",)

WRITABLE_EXTENSIONS: tuple[str, ...] = (".xlsx", ".xlsm")

# Workbook view (docs/superpowers/specs/2026-11-15-workbook-view-design.md).
SHEET_RENDER_MAX_CELLS = 20_000
LARGE_SHEET_WINDOW_ROWS = 200
RANGE_HIGHLIGHT_MAX = 400
# Both columns are as tall as the browser window (minus the page padding) and scroll on their own.
PANE_HEIGHT_CSS = "calc(100vh - 3rem)"
PANE_MIN_HEIGHT_PX = 480
# Skill validator: largest cell range accepted as the place of a missed problem.
MISS_RANGE_MAX_CELLS = 10_000
