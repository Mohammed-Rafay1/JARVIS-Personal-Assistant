"""Excel and Word by voice: open a new or an existing file, change it in plain words, save it under a name.

Office is driven through its own automation interface (COM). COM objects belong to the thread that created
them, so every Office call runs on one dedicated thread (`run`). Edits said in plain words go two ways:
common ones ("write 500 in B2", "add headings Name, Age", "make row 1 bold", "total column B") are read
directly and done at once; anything else is turned into a short list of operations by the local language
model and applied one by one. Nothing is ever saved or closed without being asked.
"""
from __future__ import annotations

import asyncio
import json
import concurrent.futures
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

KINDS = {
    "excel": {"progid": "Excel.Application", "ext": ".xlsx", "fmt": 51, "label": "Excel", "thing": "workbook"},
    "word": {"progid": "Word.Application", "ext": ".docx", "fmt": 16, "label": "Word", "thing": "document"},
}
EXCEL_EXT = {".xlsx", ".xls", ".xlsm", ".csv"}
WORD_EXT = {".docx", ".doc", ".rtf"}


class OfficeError(Exception):
    """Something to tell the user in plain words."""


def _init_thread() -> None:
    import pythoncom

    pythoncom.CoInitialize()


_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="office", initializer=_init_thread)


async def run(fn, *args):
    """Run an Office call on the Office thread."""
    return await asyncio.get_running_loop().run_in_executor(_POOL, fn, *args)


@dataclass
class Session:
    """The file being worked on."""
    kind: str
    doc: Any                 # the COM Workbook / Document: touch it only on the Office thread
    path: str = ""           # "" until a new file has been saved
    changed: bool = False
    undo: list = field(default_factory=list)
    at: float = field(default_factory=time.monotonic)

    @property
    def label(self) -> str:
        return KINDS[self.kind]["label"]

    @property
    def title(self) -> str:
        return Path(self.path).name if self.path else f"the new {self.label} {KINDS[self.kind]['thing']}"


SESSION: Session | None = None


def active() -> Session | None:
    """The file in use, if it was touched in the last hour."""
    return SESSION if SESSION and time.monotonic() - SESSION.at < 3600 else None


def kind_of(path: str) -> str | None:
    ext = Path(path).suffix.lower()
    return "excel" if ext in EXCEL_EXT else "word" if ext in WORD_EXT else None


def save_folder(cfg: dict | None = None) -> Path:
    folder = (cfg or {}).get("office", {}).get("save_folder") or "~/Documents"
    return Path(os.path.expandvars(os.path.expanduser(folder)))


def file_name(spoken: str, kind: str) -> str:
    """'call it budget 2026 dot xlsx' -> 'budget 2026.xlsx' (the right extension, nothing Windows refuses)."""
    s = re.sub(r"^(?:call it|name it|save it as|save as|it's|its|the name is|name)\s+", "", spoken.strip(), flags=re.I)
    s = re.sub(r"\s+dot\s+(\w+)$", r".\1", s)
    s = re.sub(r"\.(?:xlsx?|xlsm|csv|docx?|rtf)$", "", s, flags=re.I)
    s = re.sub(r'[<>:"/\\|?*]', "", s).strip(" .")
    return (s or "Untitled")[:80] + KINDS[kind]["ext"]


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 100):
        p = path.with_name(f"{path.stem} {i}{path.suffix}")
        if not p.exists():
            return p
    return path


# ---------------------------------------------------------------------------------------------- COM (Office thread)
def _com(kind: str, create: bool):
    import pythoncom
    import win32com.client.dynamic

    progid = KINDS[kind]["progid"]
    if not create:
        unk = pythoncom.GetActiveObject(progid)  # raises when it isn't running
        return win32com.client.dynamic.Dispatch(unk.QueryInterface(pythoncom.IID_IDispatch))
    try:
        return _com(kind, False)  # reuse the running Excel / Word
    except Exception:
        return win32com.client.dynamic.Dispatch(progid)


def _front(kind: str, app, doc) -> None:
    try:
        app.Visible = True
        app.WindowState = -4137 if kind == "excel" else 1  # maximised
        hwnd = int(app.Hwnd) if kind == "excel" else int(doc.ActiveWindow.Hwnd)
    except Exception:
        return
    try:
        from .whatsapp import _bring_to_front

        class _W:
            handle = hwnd
        _bring_to_front(_W(), KINDS[kind]["label"])
    except Exception:
        pass


def com_new(kind: str) -> Session:
    try:
        app = _com(kind, True)
        app.Visible = True
        doc = app.Workbooks.Add() if kind == "excel" else app.Documents.Add()
    except Exception as e:
        log.exception("office: new %s failed", kind)
        raise OfficeError(f"I couldn't start {KINDS[kind]['label']}. Is it installed and activated?") from e
    _front(kind, app, doc)
    return Session(kind, doc)


def com_open(path: str) -> Session:
    kind = kind_of(path)
    if not kind:
        raise OfficeError("That isn't an Excel or Word file.")
    try:
        app = _com(kind, True)
        app.Visible = True
        doc = app.Workbooks.Open(path) if kind == "excel" else app.Documents.Open(path)
    except Exception as e:
        log.exception("office: open %s failed", path)
        raise OfficeError(f"{KINDS[kind]['label']} couldn't open {Path(path).name}. It may be open somewhere "
                          "else or read-only.") from e
    _front(kind, app, doc)
    return Session(kind, doc, path=path)


def com_attach() -> Session | None:
    """The workbook or document already open on screen (opened by hand, or with "open budget.xlsx")."""
    for kind in ("excel", "word"):
        try:
            app = _com(kind, False)
            doc = app.ActiveWorkbook if kind == "excel" else app.ActiveDocument
            if doc is None:
                continue
            return Session(kind, doc, path=str(doc.FullName) if str(doc.Path) else "")
        except Exception:
            continue
    return None


def com_alive(s: Session) -> bool:
    try:
        _ = s.doc.Name
        return True
    except Exception:
        return False


def com_save(s: Session, path: str = "") -> str:
    app = s.doc.Application
    try:
        if path:
            if s.kind == "excel":
                app.DisplayAlerts = False
                try:
                    s.doc.SaveAs(path, KINDS["excel"]["fmt"] if path.lower().endswith(".xlsx") else None)
                finally:
                    app.DisplayAlerts = True
            else:
                s.doc.SaveAs2(path, KINDS["word"]["fmt"])
            s.path = path
        else:
            s.doc.Save()
    except Exception as e:
        log.exception("office: save failed")
        raise OfficeError(f"I couldn't save it: {str(e)[:80]}") from e
    s.changed = False
    return s.path


def com_close(s: Session, save: bool) -> None:
    app = s.doc.Application
    try:
        if s.kind == "excel":
            s.doc.Close(bool(save))
            if app.Workbooks.Count == 0:
                app.Quit()
        else:
            s.doc.Close(-1 if save else 0)
            if app.Documents.Count == 0:
                app.Quit()
    except Exception:
        log.exception("office: close failed")


# ---------------------------------------------------------------------------------------------- what's in it
def _col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _col_number(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n


def _cell(ref: str) -> tuple[int, int]:
    m = re.fullmatch(r"\$?([A-Za-z]{1,3})\$?(\d{1,7})", ref.strip())
    if not m:
        raise OfficeError(f"{ref} isn't a cell I know.")
    return int(m.group(2)), _col_number(m.group(1))


def _used(ws) -> tuple[int, int, int, int]:
    """(first row, first col, last row, last col) of the cells in use; (1,1,0,0) for an empty sheet."""
    ur = ws.UsedRange
    r1, c1 = int(ur.Row), int(ur.Column)
    r2, c2 = r1 + int(ur.Rows.Count) - 1, c1 + int(ur.Columns.Count) - 1
    if r1 == r2 == 1 and c1 == c2 == 1 and ws.Cells(1, 1).Formula in (None, ""):
        return 1, 1, 0, 0
    return r1, c1, r2, c2


def _grid(rng) -> list[list]:
    v = rng.Formula
    if not isinstance(v, tuple):
        return [[v]]
    return [list(row) for row in v]


def com_info(s: Session) -> dict:
    """Where the data is, for the quick edits: last row and column, and whether the last row is a total."""
    if s.kind != "excel":
        return {}
    ws = s.doc.ActiveSheet
    _, _, r2, c2 = _used(ws)
    total = bool(r2 > 2 and any(str(ws.Cells(r2, c).Formula or "").startswith("=") for c in range(1, c2 + 1)))
    return {"rows": r2, "cols": c2, "total_row": total}


def com_snapshot(s: Session, rows: int = 20, cols: int = 10) -> str:
    """The current content, briefly, so the language model knows where things are."""
    if s.kind == "excel":
        ws = s.doc.ActiveSheet
        r1, c1, r2, c2 = _used(ws)
        if r2 == 0:
            return f"Sheet '{ws.Name}' is empty."
        lines = [f"Sheet '{ws.Name}', cells in use {_col_letter(c1)}{r1}:{_col_letter(c2)}{r2}, "
                 f"next empty row {r2 + 1}:"]
        last_r, last_c = min(r2, r1 + rows - 1), min(c2, c1 + cols - 1)
        grid = _grid(ws.Range(ws.Cells(r1, c1), ws.Cells(last_r, last_c)))
        for i, row in enumerate(grid):
            cells = [f"{_col_letter(c1 + j)}{r1 + i}={v}" for j, v in enumerate(row) if v not in (None, "")]
            if cells:
                lines.append("  " + ", ".join(cells))
        if r2 > last_r:
            lines.append(f"  ... {r2 - last_r} more rows")
        return "\n".join(lines)
    text = str(s.doc.Content.Text).replace("\r", "\n").strip()
    if not text:
        return "The document is empty."
    return f"The document has {int(s.doc.Paragraphs.Count)} paragraphs. It ends with:\n" + text[-900:]


# ---------------------------------------------------------------------------------------------- Excel edits
_COLORS = {"yellow": (255, 255, 0), "light yellow": (255, 242, 204), "red": (255, 0, 0), "light red": (255, 199, 206),
           "green": (0, 176, 80), "light green": (198, 239, 206), "blue": (0, 112, 192),
           "light blue": (221, 235, 247), "orange": (255, 192, 0), "purple": (112, 48, 160), "pink": (255, 153, 204),
           "grey": (191, 191, 191), "gray": (191, 191, 191), "light grey": (242, 242, 242),
           "light gray": (242, 242, 242), "black": (0, 0, 0), "white": (255, 255, 255), "dark blue": (31, 56, 100)}
_FORMATS = {"currency": "#,##0.00", "money": "#,##0.00", "percent": "0%", "percentage": "0%",
            "date": "dd-mmm-yyyy", "number": "#,##0", "decimal": "#,##0.00", "text": "@"}
_CHARTS = {"column": 51, "bar": 57, "line": 4, "pie": 5, "area": 1, "scatter": -4169}


def _rgb(name: str) -> int:
    r, g, b = _COLORS.get(str(name).lower().strip(), _COLORS["yellow"])
    return r + g * 256 + b * 65536


def _value(v):
    """'1,200' -> 1200; '=SUM(B2:B5)' stays a formula; everything else stays text."""
    if isinstance(v, (int, float)) or v is None:
        return v
    s = str(v).strip()
    if re.fullmatch(r"-?[\d,]*\.?\d+", s) and not re.fullmatch(r"0\d+", s):  # keep phone numbers like 0300...
        n = float(s.replace(",", ""))
        return int(n) if n.is_integer() else n
    return s


def _range(ws, ref: str):
    ref = str(ref).strip().upper().replace(" ", "")
    if re.fullmatch(r"[A-Z]{1,3}", ref):
        ref = f"{ref}:{ref}"
    elif re.fullmatch(r"\d{1,7}", ref):
        ref = f"{ref}:{ref}"
    return ws.Range(ref)


def _rows(op: dict) -> list[list]:
    """A table's rows however the model wrote them: lists, {"Month": "Jan", "Sales": 100} records, or the
    heading row and the data under separate keys ("rows": [heading], "values": [data] - seen 6 Nov)."""
    rows: list = []
    head = op.get("headers") or op.get("header") or op.get("headings")
    if isinstance(head, list) and head and not isinstance(head[0], (list, tuple, dict)):
        rows.append(head)
    for key in ("rows", "data", "values"):
        part = op.get(key)
        if isinstance(part, list) and part and isinstance(part[0], (list, tuple, dict)):
            rows += part
    if rows and all(isinstance(x, dict) for x in rows):
        keys = list(rows[0].keys())
        return [keys] + [[x.get(k) for k in keys] for x in rows]
    return [list(x.values()) if isinstance(x, dict) else list(x) if isinstance(x, (list, tuple)) else [x]
            for x in rows]


def _block(ws, r: int, c: int, rows: list[list]) -> None:
    width = max(len(row) for row in rows)
    data = tuple(tuple(_value(row[j]) if j < len(row) else None for j in range(width)) for row in rows)
    ws.Range(ws.Cells(r, c), ws.Cells(r + len(rows) - 1, c + width - 1)).Value = data


def _excel_op(ws, op: dict) -> str:
    o = str(op.get("op", "")).lower()
    if o in ("set", "formula"):
        r, c = _cell(op["cell"])
        ws.Cells(r, c).Value = _value(op.get("value", op.get("formula", "")))
        return f"{op['cell'].upper()}"
    if o == "row":
        r, c = _cell(op.get("cell", "A1"))
        _block(ws, r, c, [list(op["values"])])
        return "a row"
    if o == "column":
        r, c = _cell(op.get("cell", "A1"))
        _block(ws, r, c, [[v] for v in op["values"]])
        return "a column"
    if o == "table":
        r, c = _cell(op.get("cell", "A1"))
        _block(ws, r, c, _rows(op))
        return "a table"
    rng = lambda: _range(ws, op["range"])  # noqa: E731
    if o in ("bold", "italic", "underline"):
        setattr(rng().Font, o.capitalize(), True)
        return o
    if o in ("fill", "highlight"):
        rng().Interior.Color = _rgb(op.get("color", "yellow"))
        return "the colour"
    if o == "font_color":
        rng().Font.Color = _rgb(op.get("color", "red"))
        return "the text colour"
    if o == "font_size":
        rng().Font.Size = float(op.get("size", 12))
        return "the size"
    if o == "number_format":
        fmt = str(op.get("format", "number"))
        rng().NumberFormat = _FORMATS.get(fmt.lower(), fmt)
        return "the number format"
    if o == "autofit":
        ws.UsedRange.Columns.AutoFit()
        return "the column widths"
    if o == "width":
        _range(ws, op.get("columns", op.get("range", "A"))).ColumnWidth = float(op.get("width", 15))
        return "the width"
    if o == "clear":
        rng().Clear()
        return "cleared cells"
    if o == "insert_row":
        ws.Rows(int(op["row"])).Insert()
        return "a new row"
    if o == "delete_row":
        ws.Rows(int(op["row"])).Delete()
        return "a deleted row"
    if o == "delete_column":
        ws.Columns(str(op["column"]).upper()).Delete()
        return "a deleted column"
    if o == "sort":
        r = rng()
        key = ws.Range(f"{str(op.get('by', 'A')).upper()}{int(r.Row)}")
        r.Sort(Key1=key, Order1=2 if str(op.get("order", "asc")).lower().startswith("desc") else 1, Header=1)
        return "the order"
    if o == "chart":
        r = rng()
        _, _, _, c2 = _used(ws)
        left = float(ws.Cells(1, max(c2, 1) + 2).Left)
        shape = ws.Shapes.AddChart2(-1, _CHARTS.get(str(op.get("type", "column")).lower(), 51), left, 20, 420, 260)
        shape.Chart.SetSourceData(r)
        if op.get("title"):
            shape.Chart.HasTitle = True
            shape.Chart.ChartTitle.Text = str(op["title"])
        return "a chart"
    if o == "borders":
        rng().Borders.LineStyle = 1
        return "borders"
    if o == "merge":
        r = rng()
        r.Merge()
        r.HorizontalAlignment = -4108
        return "merged cells"
    if o == "align":
        rng().HorizontalAlignment = {"left": -4131, "center": -4108, "centre": -4108,
                                     "right": -4152}.get(str(op.get("align", "center")).lower(), -4108)
        return "the alignment"
    if o == "sheet_name":
        ws.Name = str(op["name"])[:31]
        return "the sheet name"
    if o == "new_sheet":
        new = ws.Parent.Worksheets.Add(None, ws.Parent.Worksheets(ws.Parent.Worksheets.Count))
        if op.get("name"):
            new.Name = str(op["name"])[:31]
        return "a new sheet"
    if o == "freeze_top_row":
        ws.Activate()
        w = ws.Application.ActiveWindow
        w.SplitColumn, w.SplitRow, w.FreezePanes = 0, 1, True
        return "the top row frozen"
    raise OfficeError(f"unknown operation {o!r}")


# ---------------------------------------------------------------------------------------------- Word edits
def _last_para(doc, fresh: bool):
    """The paragraph to write into: the empty last one, or a new one after text."""
    last = doc.Paragraphs(doc.Paragraphs.Count)
    if fresh and str(last.Range.Text).strip():
        doc.Content.InsertParagraphAfter()
        last = doc.Paragraphs(doc.Paragraphs.Count)
    return last


def _add_para(doc, text: str, style: int = -1, listed: str = "") -> None:
    p = _last_para(doc, True)
    p.Range.InsertBefore(str(text))
    p = doc.Paragraphs(doc.Paragraphs.Count)
    p.Style = style
    lf = p.Range.ListFormat
    if listed == "bullet" and int(lf.ListType) == 0:
        lf.ApplyBulletDefault()
    elif listed == "number" and int(lf.ListType) == 0:
        lf.ApplyNumberDefault()
    elif not listed and int(lf.ListType) != 0:
        lf.RemoveNumbers()


def _word_op(doc, op: dict) -> str:
    o = str(op.get("op", "")).lower()
    if o in ("heading", "title"):
        level = int(op.get("level", 1) or 1)
        style = -63 if o == "title" else {1: -2, 2: -3, 3: -4}.get(level, -2)
        _add_para(doc, op["text"], style)
        return "a heading"
    if o in ("paragraph", "text", "write"):
        for chunk in [c for c in re.split(r"\n\s*\n|\n", str(op["text"])) if c.strip()]:
            _add_para(doc, chunk.strip())
        return "the text"
    if o in ("bullets", "numbered"):
        for item in op.get("items", []):
            _add_para(doc, item, -1, "bullet" if o == "bullets" else "number")
        return "a list"
    if o == "table":
        rows = _rows(op)
        width = max(len(r) for r in rows)
        p = _last_para(doc, True)
        t = doc.Tables.Add(p.Range, len(rows), width)
        t.Borders.Enable = True
        for i, row in enumerate(rows, 1):
            for j, v in enumerate(row, 1):
                t.Cell(i, j).Range.Text = str(v)
        t.Rows(1).Range.Font.Bold = True
        return "a table"
    if o == "page_break":
        p = _last_para(doc, True)
        p.Range.InsertBreak(7)
        return "a page break"
    # Word's Find.Execute ignores named arguments over late-bound COM (tested 5 Nov): pass them in order.
    # FindText, MatchCase, MatchWholeWord, MatchWildcards, MatchSoundsLike, MatchAllWordForms, Forward,
    # Wrap (1 = continue), Format, ReplaceWith, Replace (2 = all)
    if o == "replace":
        f = doc.Content.Find
        f.ClearFormatting()
        f.Replacement.ClearFormatting()
        if not f.Execute(str(op["find"]), False, False, False, False, False, True, 1, False,
                         str(op.get("replace", "")), 2):
            raise OfficeError(f"couldn't find {op['find']!r}")
        return "the wording"
    if o in ("bold_text", "italic_text", "underline_text"):
        f = doc.Content.Find
        f.ClearFormatting()
        f.Replacement.ClearFormatting()
        setattr(f.Replacement.Font, o.split("_")[0].capitalize(), True)
        f.Execute(str(op["text"]), False, False, False, False, False, True, 1, True, "^&", 2)
        return "the formatting"
    if o == "font":
        doc.Content.Font.Name = str(op.get("name", "Calibri"))
        return "the font"
    if o == "font_size":
        doc.Content.Font.Size = float(op.get("size", 12))
        return "the size"
    if o == "align":
        a = {"left": 0, "center": 1, "centre": 1, "right": 2, "justify": 3}.get(str(op.get("align", "left")).lower(), 0)
        target = doc.Content if str(op.get("target", "last")) == "all" else _last_para(doc, False).Range
        if str(op.get("target", "last")) != "all":  # the last paragraph with text
            for i in range(int(doc.Paragraphs.Count), 0, -1):
                if str(doc.Paragraphs(i).Range.Text).strip():
                    target = doc.Paragraphs(i).Range
                    break
        target.ParagraphFormat.Alignment = a
        return "the alignment"
    if o in ("delete_last", "remove_last"):
        for i in range(int(doc.Paragraphs.Count), 0, -1):
            if str(doc.Paragraphs(i).Range.Text).strip():
                doc.Paragraphs(i).Range.Delete()
                return "removed the last paragraph"
        return "nothing to remove"
    raise OfficeError(f"unknown operation {o!r}")


# ---------------------------------------------------------------------------------------------- apply + undo
def com_apply(s: Session, ops: list[dict]) -> tuple[int, list[str]]:
    """Apply operations in order; a bad one is skipped, not fatal. Returns (done, problems)."""
    if s.kind == "excel":
        ws = s.doc.ActiveSheet
        r1, c1, r2, c2 = _used(ws)
        s.undo.append(("excel", ws.Name, (r1, c1, r2, c2),
                       _grid(ws.Range(ws.Cells(r1, c1), ws.Cells(r2, c2))) if r2 else None))
    else:
        s.undo.append(("word", str(s.doc.Content.Text)))
    del s.undo[:-10]
    done, problems = 0, []
    for op in ops:
        if not isinstance(op, dict):
            continue
        try:
            if s.kind == "excel":
                _excel_op(s.doc.ActiveSheet, op)
            else:
                _word_op(s.doc, op)
            done += 1
        except Exception as e:
            log.warning("office op failed %s: %s", op, e)
            problems.append(str(op.get("op", "?")))
    if done:
        s.changed = True
    s.at = time.monotonic()
    return done, problems


def com_undo(s: Session) -> bool:
    if not s.undo:
        return False
    snap = s.undo.pop()
    if snap[0] == "excel":
        _, name, (r1, c1, r2, c2), grid = snap
        ws = s.doc.Worksheets(name)
        ws.UsedRange.ClearContents()
        if grid:
            ws.Range(ws.Cells(r1, c1), ws.Cells(r2, c2)).Formula = tuple(tuple(r) for r in grid)
    else:
        target = snap[1]
        for _ in range(200):  # Word keeps its own undo list: step back until the text is as it was
            if str(s.doc.Content.Text) == target or not s.doc.Undo():
                break
    s.changed = True
    return True


# ---------------------------------------------------------------------------------------------- plain words -> edits
_REF = r"[a-z]{1,2}\s?\d{1,5}"
_NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "top": 1}
_GENERATIVE = re.compile(
    r"\b(?:letter|paragraph|email|essay|summary|introduction|conclusion|story|note|report|article|description|"
    r"poem|bullet points|points|cover letter|application|agenda|outline|proposal|example|sample|dummy|"
    r"random|data for|list of|ideas)\b")


def _split_list(s: str) -> list[str]:
    parts = re.split(r"\s*,\s*|\s+and\s+|\s*;\s*", s.strip())
    return [p.strip(" .") for p in parts if p.strip(" .")]


def _num(word: str) -> int | None:
    word = word.strip().lower()
    return int(word) if word.isdigit() else _NUMWORDS.get(word)


def quick_ops(kind: str, text: str, info: dict | int | None = None) -> tuple[list[dict], str] | None:
    """Common edits read straight from the words (no model, instant). None: ask the model.
    info (Excel): com_info() - the last used row and column, and whether the last row is a total."""
    info = {"rows": info} if isinstance(info, int) else (info or {})
    snapshot_rows = int(info.get("rows", 0))
    t = re.sub(r"[.!?]+$", "", text.strip())
    low = t.lower()
    if kind == "excel":
        m = re.match(r"^(?:make|add|create|insert|draw|show|give me|put)(?: me)?(?: a| an| the)?"
                     r"(?: (?P<type>column|bar|line|pie))? (?:chart|graph)(?: of (?:it|this|that|the data|the table))?$",
                     low)
        if m and snapshot_rows >= 2:  # the data on the sheet, without a total row at the bottom
            last = snapshot_rows - 1 if info.get("total_row") else snapshot_rows
            cols = max(int(info.get("cols", 2)), 2)
            kind_ = m.group("type") or "column"
            return ([{"op": "chart", "range": f"A1:{_col_letter(cols)}{last}", "type": kind_}],
                    f"Added a {kind_} chart.")
        m = re.match(rf"^(?:please )?(?:write|put|type|enter|add|set|insert)\s+(?P<v>.+?)\s+(?:in|into|at|to|on)\s+"
                     rf"(?:cell\s+)?(?P<c>{_REF})$", t, re.I)
        if m:
            cell = m.group("c").replace(" ", "").upper()
            return [{"op": "set", "cell": cell, "value": m.group("v").strip(" '\"")}], f"Done, {cell} is set."
        m = re.match(rf"^(?:cell\s+)?(?P<c>{_REF})\s+(?:is|=|equals|should be|to)\s+(?P<v>.+)$", t, re.I)
        if m:
            cell = m.group("c").replace(" ", "").upper()
            return [{"op": "set", "cell": cell, "value": m.group("v").strip(" '\"")}], f"Done, {cell} is set."
        m = re.match(r"^(?:add|make|create|put|write|give it)(?: the| some)? (?:columns?|headings?|headers?|titles?)"
                     r"(?: called| named| as| for)?[:,]?\s+(?P<l>.+)$", low)
        if m and not _GENERATIVE.search(low) and not re.fullmatch(
                r"(?:bold|italic|underlined?|bigger|smaller|larger|cent(?:er|re)d?|colou?red|highlighted)", m.group("l")):
            names = [n.title() if n.islower() else n for n in _split_list(t[m.start("l"):])]
            ops = [{"op": "row", "cell": "A1", "values": names}, {"op": "bold", "range": f"A1:{_col_letter(len(names))}1"},
                   {"op": "autofit"}]
            return ops, f"Added the headings {', '.join(names)}."
        m = re.match(r"^(?:add|insert|put|write)(?: a| another| the)?(?: new)? row(?: with| of| saying)?[:,]?\s+(?P<l>.+)$",
                     low)
        if m and not _GENERATIVE.search(low):
            values = _split_list(t[m.start("l"):])
            row = snapshot_rows + 1 if snapshot_rows else 1
            return [{"op": "row", "cell": f"A{row}", "values": values}], f"Added row {row}."
        m = re.match(r"^(?:make|set|turn)(?: the)? (?P<what>.+?) (?P<style>bold|italic|underlined?)$", low)
        if m:
            what, style = m.group("what"), m.group("style").replace("underlined", "underline")
            ref = None
            if re.fullmatch(r"(?:first row|top row|headings?|headers?|header row|heading row|titles?)", what):
                ref = "1:1"
            elif re.fullmatch(r"row (\w+)", what) and _num(what.split()[1]):
                ref = f"{_num(what.split()[1])}:{_num(what.split()[1])}"
            elif re.fullmatch(r"column ([a-z])", what):
                ref = what.split()[1].upper()
            elif re.fullmatch(_REF, what):
                ref = what.replace(" ", "").upper()
            if ref:
                return [{"op": style, "range": ref}], f"Made it {style}."
        if re.fullmatch(r"(?:auto ?fit|fit|resize|adjust)(?: the)?(?: all)?(?: columns?| column widths?| width)?"
                        r"(?: to fit)?(?: the text)?", low):
            return [{"op": "autofit"}], "Columns resized to fit."
        m = re.match(r"^(?:add |give me |put |calculate |show )?(?:a |the )?(?P<f>total|sum|average|mean|max(?:imum)?|"
                     r"min(?:imum)?)(?: of| for)?(?: column| the column)? (?P<col>[a-z])(?: column)?$", low)
        if m and snapshot_rows >= 2:
            col = m.group("col").upper()
            func = {"total": "SUM", "sum": "SUM", "average": "AVERAGE", "mean": "AVERAGE"}.get(
                m.group("f"), "MAX" if m.group("f").startswith("max") else "MIN")
            row = snapshot_rows + 1
            return ([{"op": "set", "cell": f"{col}{row}", "value": f"={func}({col}2:{col}{snapshot_rows})"},
                     {"op": "bold", "range": f"{col}{row}"}],
                    f"Added the {m.group('f')} of column {col} in {col}{row}.")
        m = re.match(r"^(?:rename|call|name)(?: the| this)? sheet(?: to| as)? (?P<n>.+)$", low)
        if m:
            return [{"op": "sheet_name", "name": t[m.start("n"):].strip()}], "Renamed the sheet."
        return None
    # Word
    m = re.match(r"^(?:add|insert|write|put|make|give it)(?: a| the)? (?P<kind>heading|title|subheading|sub heading)"
                 r"(?: called| saying| that says| as)?[:,]?\s+(?P<x>.+)$", low)
    if m:  # before the check below: "add a title Monthly Report" is a title, not a report to write
        txt = t[m.start("x"):].strip(" '\"")
        kind_ = m.group("kind")
        op = {"op": "title" if kind_ == "title" else "heading", "text": txt,
              "level": 2 if "sub" in kind_ else 1}
        return [op], f"Added the {kind_.replace('sub heading', 'subheading')}."
    m = re.match(r"^(?:make|set|change|use)(?: the)?(?: whole| document)? font(?: to| as)? (?P<f>[a-z][a-z ]{1,30})$", low)
    if m and m.group("f").strip() not in ("bigger", "smaller", "bold", "size"):
        name = " ".join(w.capitalize() for w in m.group("f").split())
        return [{"op": "font", "name": name}], f"The font is now {name}."
    if _GENERATIVE.search(low) and re.match(r"^(?:write|draft|compose|create|add|make|give me|put)\b", low):
        return None  # "write a leave letter": the model writes it
    m = re.match(r"^(?:type|write|add|insert|put|say)(?:(?: the| this| a)? (?:text|line|sentence|words))?[:,]?\s+"
                 r"(?P<x>.+)$", text.strip(), re.I)
    if m:  # dictation: written as said, full stop and all ("write The team..." keeps "The")
        return [{"op": "paragraph", "text": m.group("x").strip(" '\"")}], "Written."
    if re.fullmatch(r"(?:add |insert )?(?:a )?(?:page break|new page)", low):
        return [{"op": "page_break"}], "Added a page break."
    m = re.match(r"^(?:make|set|turn)(?: the)? (?:whole |entire )?(?:document|text|everything)(?: font)? "
                 r"(?:size )?(?P<n>\d{1,2})(?: point| pt)?$", low)
    if m:
        return [{"op": "font_size", "size": int(m.group("n"))}], f"Text size is now {m.group('n')}."
    m = re.match(r"^(?:replace|change) (?P<a>.+?) (?:with|to) (?P<b>.+)$", t, re.I)
    if m:
        return [{"op": "replace", "find": m.group("a").strip(" '\""), "replace": m.group("b").strip(" '\"")}], "Replaced."
    if re.fullmatch(r"(?:delete|remove|erase)(?: the)? (?:last (?:line|paragraph|sentence)|that line)", low):
        return [{"op": "delete_last"}], "Removed the last paragraph."
    m = re.match(r"^(?:center|centre|align)(?: the)?(?: last)?(?: line| paragraph| heading| title| text)?(?: to the)?"
                 r"(?: (?P<a>center|centre|left|right))?$", low)
    if m and (m.group("a") or low.startswith(("center", "centre"))):
        return [{"op": "align", "align": m.group("a") or "center"}], "Aligned."
    return None


# Every operation is shown as real JSON: a short notation ("set{cell,value}") made the 3B model write the
# operations as top-level keys instead of a list (tested 6 Nov). Each line costs prompt time on this CPU.
EXCEL_PROMPT = """Turn the instruction into Excel edits. Reply with compact JSON: {"ops":[...],"say":"short sentence"}
Each item of "ops" is one of these:
{"op":"table","cell":"A1","rows":[["Month","Sales"],["January",12000]]}
{"op":"row","cell":"A5","values":["Ali",30]}
{"op":"column","cell":"C2","values":[10,20]}
{"op":"set","cell":"B8","value":"=SUM(B2:B7)"}
{"op":"bold","range":"A1:B1"}
{"op":"fill","range":"A1:B1","color":"light blue"}
{"op":"number_format","range":"B2:B7","format":"currency"}
{"op":"chart","range":"A1:B7","type":"column","title":"Sales"}
{"op":"sort","range":"A1:B7","by":"B","order":"desc"}
{"op":"delete_row","row":3}
{"op":"autofit"}
Empty sheet: headings in row 1, data below. New data goes under the last used row. Totals use formulas.
Invent realistic values when asked for sample data.
Example: "monthly sales January to March" ->
{"ops":[{"op":"table","cell":"A1","rows":[["Month","Sales"],["January",12000],["February",13500],["March",12800]]},
{"op":"bold","range":"A1:B1"},{"op":"autofit"}],"say":"Added sales for January to March."}"""

WORD_PROMPT = """Turn the instruction into Word edits. Reply with compact JSON: {"ops":[...],"say":"short sentence"}
Each item of "ops" is one of these:
{"op":"title","text":"Monthly Report"}
{"op":"heading","text":"Summary","level":1}
{"op":"paragraph","text":"One whole paragraph of text."}
{"op":"bullets","items":["First point","Second point"]}
{"op":"table","rows":[["Name","Role"],["Ali","Manager"]]}
{"op":"replace","find":"old words","replace":"new words"}
{"op":"font","name":"Arial"}
When asked to write something (a letter, paragraph, summary), write all of it in good English: greeting, body
and sign-off, one paragraph item each. No title unless asked.
Example: "short note asking for leave on Friday" ->
{"ops":[{"op":"paragraph","text":"Dear Sir,"},{"op":"paragraph","text":"I would like to request leave on Friday
for personal reasons. My work will be up to date before then."},{"op":"paragraph","text":"Kind regards,"}],
"say":"Written a short leave note."}"""


def parse_plan(text: str) -> tuple[list[dict], str]:
    """The model's reply -> (operations, what to say). A reply cut off at the length limit still gives every
    operation that arrived whole (5 Nov: a pretty-printed table ran out of room and nothing was done)."""
    try:
        out = json.loads(text)
    except ValueError:
        out, ops, dec = {}, [], json.JSONDecoder()
        start = text.find("[", max(text.find('"ops"'), 0))
        pos = start + 1 if start >= 0 else len(text)
        while (k := text.find("{", pos)) >= 0:
            try:
                obj, pos = dec.raw_decode(text, k)
            except ValueError:
                break
            ops.append(obj)
        out["ops"] = ops
        m = re.search(r'"say"\s*:\s*"([^"]*)"', text)
        out["say"] = m.group(1) if m else ""
    if not isinstance(out, dict):
        return [], ""
    ops = out.get("ops")
    if not ops and isinstance(out.get("op"), str):
        ops = [out]  # a single operation on its own
    if not isinstance(ops, list):
        ops = []
    if not ops:  # operations written as keys: {"row": {"cell": "A1", ...}, "table": {...}, "autofit": "..."}
        for key, val in out.items():
            if key in ("ops", "say"):
                continue
            if isinstance(val, dict):
                ops.append({"op": key, **val})
            elif isinstance(val, list) and val and all(isinstance(v, dict) for v in val):
                ops += [{"op": key, **v} for v in val]
            elif key in ("autofit", "freeze_top_row", "page_break"):
                ops.append({"op": key})
    say = str(out.get("say", "")).strip()
    clean = []
    for o in ops:
        if not isinstance(o, dict) or not o.get("op"):
            continue
        if o["op"] == "say":  # the model sometimes puts its sentence in as an operation
            say = say or str(o.get("value") or o.get("text") or "")
            continue
        if "say" in o:  # or tucks it inside one
            say = say or str(o.pop("say"))
        clean.append(o)
    return clean, say


async def plan(kind: str, text: str, snapshot: str, llm) -> tuple[list[dict], str]:
    """The language model's edit plan for an instruction the quick rules didn't cover."""
    system = EXCEL_PROMPT if kind == "excel" else WORD_PROMPT
    user = f"Current {'sheet' if kind == 'excel' else 'document'}:\n{snapshot}\n\nInstruction: {text}"
    reply = await llm.json_text(system, user, max_tokens=700 if kind == "word" else 600, timeout=240)
    ops, say = parse_plan(reply)
    log.info("office plan for %r: %d ops (%d chars)", text, len(ops), len(reply))
    return ops, say or ("Written." if kind == "word" else "Done.")
