"""15 Nov: a few files were read as Windows-1252 and saved as UTF-8, so "’" became "â€™" (curly apostrophes stopped
being normalised, bullet lists stopped splitting, WhatsApp emojis turned to junk). Keep the source clean."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILES = [p for d in ("backend/jarvis", "ui", "tests", "scripts") for p in (ROOT / d).rglob("*")
         if p.suffix in (".py", ".js", ".css", ".html") and "node_modules" not in p.parts]
GARBLED = ("â€", "Ã©", "Ã¨", "Â·", "Â ", "ðŸ", "âœ", "â˜", "â–")


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_garbled_characters(path):
    text = path.read_text(encoding="utf-8")
    if path.name == "test_source_encoding.py":
        return
    bad = [g for g in GARBLED if g in text]
    assert not bad, f"{path.name} has garbled text {bad}: it was saved in the wrong encoding"
