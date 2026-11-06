"""Research answers read from the ChatGPT app and sent to WhatsApp (6 Nov 18:20: "Research on Adolf Hitler and
tell me summary on ChatGPT" arrived one word per line, a single tall column)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.services.whatsapp_remote import card_text, chat_layout  # noqa: E402
from jarvis.tools.ai_chat import lead, reflow  # noqa: E402


def words(text, top, h=29, kind="p", wraps=False):
    """How ChatGPT's app shows a line: one piece per word, each with its trailing space. The last word of a
    paragraph has none; a screen line that wraps on to the next keeps it (the paragraph goes on)."""
    ws = text.split(" ")
    return [(w + (" " if i < len(ws) - 1 or wraps else ""), top, h, kind) for i, w in enumerate(ws)]


# the start of the real answer, as the app exposed it (tops and heights from the live window)
PIECES = (
    [("ChatGPT said:", -536, 20, "p"), ("Adolf Hitler: A Historical Overview", -536, 50, "p"),
     ("(1889–1945)", -452, 36, "p"), ("Nazi dictator", -412, 29, "p")]
    + words("Born: 20 April 1889", -376, kind="li") + words("Died: 30 April 1945", -342, kind="li")
    + [("1. Who was Adolf Hitler?", -172, 43, "p")]
    + words("Adolf Hitler was an Austrian-born German politician who became the dictator", -115, wraps=True)
    + words("of Nazi Germany from 1933 to 1945.", -86)
    + words("His ideology was central to the outbreak of World War II in Europe.", -40)[:-1]
    + [("Europe. ", -40, 29, "p")]                          # a citation button follows: the space stays
    + [("2. Key facts and historical timeline", 20, 43, "p")]
    + [("Date", 80, 53, "cell"), ("Event", 80, 53, "cell"),
       ("20 April 1889", 133, 54, "cell"), ("Born in Braunau am Inn, Austria.", 133, 54, "cell"),
       ("1933", 187, 54, "cell"), ("Appointed Chancellor of Germany.", 187, 54, "cell")]
    + [("Source: United States Holocaust Memorial Museum.", 260, 29, "p"), ("Copy", 300, 20, "p")]
)


def test_words_become_lines_paragraphs_bullets_and_table_rows():
    text = reflow(PIECES)
    lines = text.splitlines()
    assert lines[:5] == ["Adolf Hitler: A Historical Overview", "(1889–1945)", "Nazi dictator",
                         "• Born: 20 April 1889", "• Died: 30 April 1945"]
    assert lines[5] == "1. Who was Adolf Hitler?"
    # a paragraph wrapped over two screen lines is one paragraph again
    assert lines[6] == ("Adolf Hitler was an Austrian-born German politician who became the dictator of Nazi Germany "
                        "from 1933 to 1945.")
    # the heading after "Europe. " (a citation button keeps its space) is not glued onto the paragraph
    assert lines[7].endswith("World War II in Europe.") and lines[8] == "2. Key facts and historical timeline"
    assert lines[9:12] == ["Date – Event", "• 20 April 1889 – Born in Braunau am Inn, Austria.",
                           "• 1933 – Appointed Chancellor of Germany."]
    assert lines[-1].startswith("Source:") and "Copy" not in text and "said:" not in text


def test_words_inside_sentences_are_never_dropped():
    # "Send", "Copy" or "ChatGPT" as ordinary words used to be removed as if they were buttons
    text = reflow(words("Click Send and ChatGPT will copy the text.", 0))
    assert text == "Click Send and ChatGPT will copy the text."


def test_whatsapp_layout():
    msg = chat_layout(reflow(PIECES))
    assert msg.startswith("*Adolf Hitler: A Historical Overview*")
    assert "\n\n*1. Who was Adolf Hitler?*\n\n" in msg
    assert "• Born: 20 April 1889\n• Died: 30 April 1945" in msg          # bullets stay together
    assert "*2. Key facts and historical timeline*" in msg and "*Date – Event*" in msg
    assert max(len(ln.split()) for ln in msg.splitlines()) > 5             # no more one-word lines


def test_long_answers_are_cut_at_a_whole_line():
    long = "\n".join(f"Paragraph {i} " + "word " * 40 for i in range(40))
    msg = chat_layout(long, limit=1000)
    assert msg.endswith("\n\n…") and len(msg) < 1100
    assert all(ln.endswith("word") or ln in ("", "…") for ln in msg.splitlines())


def test_research_card_on_whatsapp():
    msg = card_text({"kind": "research", "answers": [{"service": "ChatGPT", "text": reflow(PIECES)}]})
    assert msg.startswith("*ChatGPT*\n*Adolf Hitler: A Historical Overview*")


def test_spoken_fallback_starts_with_a_real_sentence():
    # no summary in time: speak the opening of the first real paragraph, not the title and date lines
    assert lead(reflow(PIECES), 1) == ("Adolf Hitler was an Austrian-born German politician who became the "
                                       "dictator of Nazi Germany from 1933 to 1945.")
