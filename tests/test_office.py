"""Excel and Word by voice (6 Nov): new or existing, edits in plain words, save with a name, close folders."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.brain.intents import parse  # noqa: E402
from jarvis.tools import office  # noqa: E402
from jarvis.tools.apps import folder_query, pick_folders  # noqa: E402


# ------------------------------------------------------------------ what you say
@pytest.mark.parametrize("said,app,mode,query", [
    ("Open Microsoft Excel.", "excel", "", None),                  # 5 Nov 20:52: just opened Excel's start screen
    ("open excel", "excel", "", None),
    ("open ms word", "word", "", None),
    ("Open blank, workbook in Excel.", "excel", "new", None),       # 20:53: "open_app 'blank workbook in excel'"
    ("Jarvis, Open a Blank Sheet in Excel.", "excel", "new", None),
    ("create a new word document", "word", "new", None),
    ("new excel file", "excel", "new", None),
    ("make a new spreadsheet", "excel", "new", None),
    ("open an existing excel file", "excel", "existing", ""),
    ("edit an existing word document", "word", "existing", ""),
    ("open my budget excel file", "excel", "existing", "budget"),
    ("open budget.xlsx", "excel", "existing", "budget.xlsx"),
    ("edit the sales report word file", "word", "existing", "sales report"),
])
def test_opening_excel_and_word(said, app, mode, query):
    i = parse(said)
    assert i.tool == "office_open" and i.args["app"] == app and i.args["mode"] == mode
    if query is not None:
        assert i.args["query"] == query


@pytest.mark.parametrize("said,tool,args", [
    ("save it", "office_save", {"name": ""}), ("save", "office_save", {"name": ""}),
    ("save the file", "office_save", {"name": ""}), ("save it as budget 2026", "office_save", {"name": "budget 2026"}),
    ("undo", "office_undo", {}), ("undo that", "office_undo", {}),
])
def test_save_and_undo(said, tool, args):
    i = parse(said)
    assert i.tool == tool and i.args == args


@pytest.mark.parametrize("said,tool", [("open youtube", "open_any"), ("open spotify", "open_any"),
                                       ("open the downloads folder", "open_folder"), ("open chrome", "open_any")])
def test_other_opens_unchanged(said, tool):
    assert parse(said).tool == tool


# ------------------------------------------------------------------ edits read straight from the words
@pytest.mark.parametrize("said,ops", [
    ("write 500 in B2", [{"op": "set", "cell": "B2", "value": "500"}]),
    ("put Ali in a2", [{"op": "set", "cell": "A2", "value": "Ali"}]),
    ("C4 is 45000", [{"op": "set", "cell": "C4", "value": "45000"}]),
    ("make row 1 bold", [{"op": "bold", "range": "1:1"}]),
    ("make the headers italic", [{"op": "italic", "range": "1:1"}]),
    ("autofit the columns", [{"op": "autofit"}]),
])
def test_excel_quick_edits(said, ops):
    assert office.quick_ops("excel", said, {"rows": 4, "cols": 3})[0] == ops


def test_excel_headings_rows_totals_and_charts():
    ops, said = office.quick_ops("excel", "add headings name, age and salary")
    assert ops[0] == {"op": "row", "cell": "A1", "values": ["Name", "Age", "Salary"]} and "Name, Age, Salary" in said
    ops, _ = office.quick_ops("excel", "add a row with Ali, 30, 50000", {"rows": 3})
    assert ops == [{"op": "row", "cell": "A4", "values": ["Ali", "30", "50000"]}]
    ops, said = office.quick_ops("excel", "total column C", {"rows": 4})
    assert ops[0] == {"op": "set", "cell": "C5", "value": "=SUM(C2:C4)"} and "C5" in said
    ops, _ = office.quick_ops("excel", "make a column chart of it", {"rows": 8, "cols": 2, "total_row": True})
    assert ops == [{"op": "chart", "range": "A1:B7", "type": "column"}]   # the total row stays out of the chart


@pytest.mark.parametrize("said", ["make a table of monthly sales from January to June", "add a total at the bottom",
                                  "highlight the highest sale"])
def test_harder_excel_requests_go_to_the_model(said):
    assert office.quick_ops("excel", said, {"rows": 3}) is None


def test_word_quick_edits():
    assert office.quick_ops("word", "add a title Monthly Report")[0] == [{"op": "title", "text": "Monthly Report",
                                                                          "level": 1}]
    assert office.quick_ops("word", "add a heading Next steps")[0][0]["op"] == "heading"
    # dictation keeps every word and the full stop (6 Nov: "The" was dropped)
    assert office.quick_ops("word", "write The team closed three new clients.")[0] == \
        [{"op": "paragraph", "text": "The team closed three new clients."}]
    assert office.quick_ops("word", "replace Friday with Monday")[0] == \
        [{"op": "replace", "find": "Friday", "replace": "Monday"}]
    assert office.quick_ops("word", "make the font Arial")[0] == [{"op": "font", "name": "Arial"}]
    assert office.quick_ops("word", "write a short letter to my manager asking for leave") is None   # the model


# ------------------------------------------------------------------ the model's replies, however they come
def test_plan_parser_handles_the_shapes_the_model_wrote():
    ops, say = office.parse_plan('{"ops":[{"op":"table","cell":"A1","rows":[["Month","Sales"]],'
                                 '"values":[["Jan",1],["Feb",2]]}],"say":"Added."}')
    assert office._rows(ops[0]) == [["Month", "Sales"], ["Jan", 1], ["Feb", 2]] and say == "Added."
    ops, _ = office.parse_plan('{"ops":[],"row":{"cell":"A1","values":["Month"]},"table":{"cell":"A2","rows":[["J",1]]},'
                               '"autofit":"A1:J1"}')
    assert [o["op"] for o in ops] == ["row", "table", "autofit"]
    ops, say = office.parse_plan('{"ops":[{"op":"paragraph","text":"Dear Sir,"},{"op":"paragraph","text":"Hi.",'
                                 '"say":"Written."}]}')
    assert len(ops) == 2 and say == "Written." and "say" not in ops[1]
    ops, say = office.parse_plan('{"ops":[{"op":"set","cell":"A1","value":"x"},{"op":"say","value":"Did it."},'
                                 '{"op":"table","cell":"A2","rows":[["a",1')                 # cut off mid-table
    assert ops == [{"op": "set", "cell": "A1", "value": "x"}] and say == "Did it."
    assert office._rows({"rows": [{"Month": "Jan", "Sales": 5}]}) == [["Month", "Sales"], ["Jan", 5]]


def test_file_names():
    assert office.file_name("Budget 2026", "excel") == "Budget 2026.xlsx"
    assert office.file_name("call it budget dot xlsx", "excel") == "budget.xlsx"
    assert office.file_name('report: "final"?', "word") == "report final.docx"
    assert office.file_name("", "word") == "Untitled.docx"


def test_saving_never_overwrites(tmp_path):
    (tmp_path / "Budget.xlsx").write_text("x")
    assert office.unique(tmp_path / "Budget.xlsx").name == "Budget 2.xlsx"


# ------------------------------------------------------------------ closing folders (5 Nov: "close Design folder")
@pytest.mark.parametrize("said,windows,picked", [
    ("design folder", ["Solvix", "Design"], [1]),
    ("the solvix folder", ["Solvix", "Design"], [0]),
    ("folder", ["Solvix", "Design"], [1]),            # "close the folder": the last one opened
    ("all folders", ["Solvix", "Design"], [0, 1]),
    ("designs folder", ["Solvix", "Design"], [1]),    # heard slightly wrong
    ("music folder", ["Solvix", "Design"], []),
])
def test_which_folder_window_to_close(said, windows, picked):
    assert pick_folders(said, windows) == picked


def test_folder_words_are_stripped():
    assert folder_query("the Design folder") == "design" and folder_query("the folder") == ""


def test_close_design_folder_parses_to_close():
    assert parse("Close Design folder.").tool == "close_app"


# ------------------------------------------------------------------ the whole conversation, with Office faked
class _UI:
    def __init__(self):
        self.sent = []

    def send(self, m):
        self.sent.append(m)


class _FakeOffice:
    def __init__(self):
        self.calls, self.applied = [], []

    def new(self, kind):
        self.calls.append(("new", kind))
        return office.Session(kind, doc=object())

    def open(self, path):
        self.calls.append(("open", path))
        return office.Session(office.kind_of(path), doc=object(), path=path)

    def apply(self, s, ops):
        self.applied += ops
        s.changed = True
        return len(ops), []

    def save(self, s, path=""):
        self.calls.append(("save", path))
        s.path, s.changed = path or s.path, False
        return s.path

    def close(self, s, save):
        self.calls.append(("close", save))


@pytest.fixture
def jarvis(monkeypatch, tmp_path):
    from jarvis import app as app_mod
    from jarvis.tools import CTX

    fake = _FakeOffice()

    async def run(fn, *args):
        return fn(*args)
    monkeypatch.setattr(office, "run", run)
    monkeypatch.setattr(office, "com_new", fake.new)
    monkeypatch.setattr(office, "com_open", fake.open)
    monkeypatch.setattr(office, "com_apply", fake.apply)
    monkeypatch.setattr(office, "com_save", fake.save)
    monkeypatch.setattr(office, "com_close", fake.close)
    monkeypatch.setattr(office, "com_alive", lambda s: True)
    monkeypatch.setattr(office, "com_attach", lambda: None)
    monkeypatch.setattr(office, "com_info", lambda s: {"rows": 1, "cols": 2})
    monkeypatch.setattr(office, "com_snapshot", lambda s: "Sheet 'Sheet1' is empty.")
    monkeypatch.setattr(office, "SESSION", None)
    CTX.cfg = {}

    class Apps:
        apps = {}

        def find(self, name):
            return None
    CTX.apps = Apps()
    j = app_mod.Jarvis({"address_as": "sir", "music": {"default_service": "youtube"}, "apps": {"aliases": {}},
                        "websites": {}, "office": {"save_folder": str(tmp_path)}}, _UI())
    j.state = "idle"
    said, chats = [], []

    async def say(text, then_idle=True, follow=False):
        said.append(text)

    async def chat(text):
        chats.append(text)

    class LLM:
        async def available(self):
            return True

    class Player:
        busy = False

        def play(self, *a):
            pass
    tools = []

    async def run_tool(name, args):
        from jarvis.tools.base import Result
        tools.append(name)
        return Result(f"ran {name}")
    j.say, j._chat, j.llm, j.player, j._speech_q = say, chat, LLM(), Player(), asyncio.Queue()
    j.run_tool, j.tools_ran = run_tool, tools
    return j, said, chats, fake, tmp_path


def _turn(j, text):
    asyncio.run(j.handle_text(text, typed=True))


def test_open_excel_asks_new_or_existing_then_edits_and_saves_with_a_name(jarvis):
    j, said, chats, fake, folder = jarvis
    _turn(j, "Open Microsoft Excel.")
    assert said[-1] == "Shall I open a new Excel file, or an existing one you want to edit?"
    _turn(j, "new")
    assert fake.calls == [("new", "excel")] and said[-1].startswith("A new Excel workbook is open, sir.")
    _turn(j, "write 500 in B2")
    assert fake.applied == [{"op": "set", "cell": "B2", "value": "500"}] and said[-1] == "Done, B2 is set."
    _turn(j, "save it")
    assert said[-1] == "What should I call it?"
    _turn(j, "Budget 2026")
    assert fake.calls[-1] == ("save", str(folder / "Budget 2026.xlsx"))
    assert said[-1].startswith("Saved as Budget 2026")
    _turn(j, "close it")
    assert fake.calls[-1] == ("close", False) and said[-1] == "Closed Budget 2026.xlsx."
    assert chats == []


def test_a_blank_sheet_opens_straight_away(jarvis):
    j, said, _, fake, _ = jarvis
    _turn(j, "Jarvis, Open a Blank Sheet in Excel.")
    assert fake.calls == [("new", "excel")]


def test_existing_file_is_found_and_opened(jarvis, monkeypatch):
    from jarvis.tools import files

    j, said, _, fake, _ = jarvis
    monkeypatch.setattr(files, "find_files", lambda q, kind=None, where="": [r"C:\Users\me\Documents\Budget.xlsx"])
    _turn(j, "open excel")
    _turn(j, "an existing one")
    assert said[-1] == "Which Excel file? Say its name."
    _turn(j, "budget")
    assert fake.calls == [("open", r"C:\Users\me\Documents\Budget.xlsx")]
    assert said[-1] == "Opened Budget. What would you like to change?"
    _turn(j, "add headings name and amount")
    _turn(j, "save it")                                  # an existing file is saved in place, no name needed
    assert fake.calls[-1] == ("save", "") and said[-1] == "Saved Budget."


def test_closing_with_unsaved_changes_asks_first(jarvis):
    j, said, _, fake, _ = jarvis
    _turn(j, "create a new word document")
    _turn(j, "add a heading Introduction")
    _turn(j, "close word")
    assert said[-1] == "Shall I save the new Word document before closing it?"
    _turn(j, "no")
    assert fake.calls[-1] == ("close", False) and said[-1] == "Closed without saving."
    assert office.SESSION is None


def test_closing_with_save_asks_the_name_then_closes(jarvis):
    j, said, _, fake, folder = jarvis
    _turn(j, "new excel file")
    _turn(j, "put Ali in A2")
    _turn(j, "close the file")
    _turn(j, "yes")
    assert said[-1] == "What should I call it?"
    _turn(j, "Team list")
    assert ("save", str(folder / "Team list.xlsx")) in fake.calls and fake.calls[-1] == ("close", False)
    assert said[-1].endswith("And closed it.")


def test_harder_edits_go_through_the_model(jarvis, monkeypatch):
    j, said, _, fake, _ = jarvis

    async def plan(kind, text, snapshot, llm):
        return [{"op": "table", "cell": "A1", "rows": [["Month", "Sales"], ["Jan", 1]]}], "Added the sales."
    monkeypatch.setattr(office, "plan", plan)
    _turn(j, "new excel file")
    _turn(j, "make a table of monthly sales from January to June")
    assert fake.applied[0]["op"] == "table" and said[-1] == "Added the sales."


def test_chat_still_works_while_a_file_is_open(jarvis):
    j, said, chats, fake, _ = jarvis
    _turn(j, "new excel file")
    _turn(j, "How are you?")
    assert chats == ["How are you?"] and fake.applied == []
    _turn(j, "open youtube")                        # and other commands are still commands
    assert fake.applied == [] and j.tools_ran


def test_save_with_nothing_open_says_so(jarvis):
    j, said, _, _, _ = jarvis
    _turn(j, "save it")
    assert said[-1] == "There's no Excel or Word file open for me to save."
