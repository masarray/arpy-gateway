# ARPY Gateway Studio

**High-performance, Excel-like industrial signal mapping editor** written in Python, built for future multi-protocol gateway workflows.

> **GUI-first engineering preview, v0.1.0.** The application is an offline signal engineering editor. It does **not** yet communicate with devices, publish IEC-104 telegrams, or perform Modbus control. Those protocol engines will be independent services, not part of the GUI process.

## Try the editor

Download the latest `ARPY-Gateway-Studio-Windows-Portable` ZIP from [GitHub Actions](https://github.com/masarray/arpy-gateway/actions/workflows/studio.yml) (successful workflow run → **Artifacts**), extract it, and run `ARPY-Gateway-Studio.exe`.

Or run from source (Python 3.10+):

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -e .
arpy-studio
```

Linux or macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
arpy-studio
```

Use `arpy-studio --database /path/to/project.sqlite3` to choose a project database. The default project lives in Qt's per-user application data directory.

Select **Demo 100K** or **Demo 500K** to populate realistic-looking *synthetic* signal data, or use **New +100** / **Import CSV** for your own signals. Demo generation **replaces all existing signals in that project**, after confirmation.

## Spreadsheet-first workflow

| Action | Shortcut |
| --- | --- |
| Edit a cell | Type or double-click |
| Copy rectangular selection (TSV, including Excel compatibility) | Ctrl+C |
| Paste rectangular TSV selection | Ctrl+V |
| Undo / redo saved cell changes | Ctrl+Z / Ctrl+Y |
| Fill selected columns down / across | Ctrl+D / Ctrl+R |
| Search device, signal, address, description | Ctrl+F |
| Refresh project | F5 |
| Sort by any field | Click the column header |
| Apply consecutive Modbus addresses / IEC-104 IOAs | Select address cells → Auto-number |

You can paste thousands of rows from Excel directly into the table. A single-cell paste onto a rectangular multi-cell selection fills the full selection. Paste and fill operations run as a **single SQLite transaction**, with all-or-nothing validation. Undo/redo is backed by a durable SQLite edit journal and remains available after reopening the same project. CSV import appends rows and is atomic; CSV export respects the current search, protocol filter and sort order. Import/export and seed tasks run in workers.

**Known preview limitations:** One contiguous selection per copy/fill; max 500,000 cells per edit and 100,000 cells per clipboard copy (use CSV export for larger selections). Undo covers cell edits/fills/auto-number, not row additions, imports, or demo reseeding. No Excel `.xlsx` import yet (use Excel's CSV or tab-delimited clipboard). No duplicate-address analyzer, protocol semantics validator, live values, run-time command execution, or live gateway process in v0.1.0. Bulk actions refresh the view; ordinary edits preserve focus unless sorting/filtering makes the row move.

## Architecture and performance

```text
                             ARPY Gateway (future)
                          [separate service / process]
                                      :
                            versioned IPC/API boundary
                                      :
    +----------------------- ARPY Gateway Studio -----------------------+
    | Python / PySide6 GUI (main Qt thread; no SQLite I/O)             |
    |                 |                       |                        |
    |          Reader worker pool       Single writer pool             |
    |          2 background threads     1 background thread           |
    |                 |                       |                        |
    |          Virtual page cache    Transactions / edit journal      |
    |                 +---------- SQLite WAL ----------+              |
    +-----------------------------------------------------------------+
```

* `QAbstractTableModel` + `QTableView`: virtualized cells, **256-row pages** and bounded **48-page LRU cache** (~12,288 rows at most).
* Search, sort and page loading operate in SQLite worker threads. No `QStandardItemModel` materialization.
* Single worker serializes all write operations. Readers have independent connections and SQLite WAL supports concurrent snapshots.
* Edit history uses persisted per-cell before/after values; undo/redo replay happens in the write worker.
* The protocol engine will be a separate process; stopping/closing GUI won't stop device communication.

Run storage performance tests without installing Qt:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m arpy_gateway.benchmark --rows 100000
PYTHONPATH=src python -m arpy_gateway.benchmark --rows 500000
```

Performance depends on storage, OS and CPU. CI measures underlying **storage throughput**, not display frame rate. GUI responsiveness and Excel-like editing behavior must be validated by running the Windows executable on actual engineering hardware.

## Repository layout

```text
src/arpy_gateway/studio/store.py     # GUI-independent SQLite backend
src/arpy_gateway/studio/workers.py   # read pool and serialized write pool
src/arpy_gateway/studio/model.py     # virtual QAbstractTableModel
src/arpy_gateway/studio/window.py    # compact spreadsheet UI
src/arpy_gateway/studio/app.py       # Qt bootstrap (no gateway engine import)
src/arpy_gateway/benchmark.py        # reproducible headless benchmark
tests/                                # backend and offscreen GUI smoke tests
.github/workflows/studio.yml         # Linux tests + portable Windows EXE
```

**Release strategy:** preview builds are CI workflow artifacts. No stable release is published until Windows GUI testing and protocol integration are complete.
