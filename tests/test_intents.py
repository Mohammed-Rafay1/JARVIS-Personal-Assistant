import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.brain.intents import NO, YES, normalize, parse, parse_choice  # noqa: E402

CASES = [
    # (spoken, tool, args subset)
    ("Hey Jarvis, open my Downloads folder.", "open_any", {"name": "downloads folder"}),
    ("Jarvis open Excel please", "open_any", {"name": "excel"}),
    ("Can you launch VS Code?", "open_any", {"name": "vs code"}),
    ("Open the Q3 proposal PDF", "open_file", {"query": "q3 proposal", "kind": "pdf"}),
    ("open the budget spreadsheet", "open_file", {"query": "budget", "kind": "spreadsheet"}),
    ("find my CV", "open_file", {"query": "cv"}),
    ("Open github.com", "open_website", {"site": "github.com"}),
    ("go to linkedin", "open_website", {"site": "linkedin"}),
    ("Search Google for Odoo 18 release notes", "web_search", {"query": "odoo 18 release notes"}),
    ("google weather in Lahore", "web_search", {"query": "weather in lahore"}),
    ("Play Arijit Singh latest song on YouTube", "play_youtube", {"query": "arijit singh latest song"}),
    ("search youtube for lofi beats", "play_youtube", {"query": "lofi beats"}),
    ("Play Blinding Lights on Spotify.", "play_spotify", {"query": "blinding lights"}),
    ("play tum hi ho", "play_youtube", {"query": "tum hi ho"}),
    # real phrasings from the log
    ("Playtop Song in the Liked Songs.", "play_spotify_liked", {"which": "first"}),
    ("Go in the Spotify in Liked Songs and play the Top 1 song.", "play_spotify_liked", {"which": "first"}),
    ("play my liked songs", "play_spotify_liked", {"which": "all"}),
    ("Open Supportify.", "open_any", {"name": "supportify"}),
    # misfires seen in the log
    ("Can you listen to me?", "ack", {}),
    ("Can you hear me, Jarvis?", "ack", {}),
    ("Bye, Derivi.", "stop", {}),
    ("Bye, dear-wiz.", "stop", {}),
    ("Buy Service,", "stop", {}),
    ("buy milk on amazon", None, {}),
    ("play that song", "ask_song", {}),
    # Claude Code projects
    ("What is the status of my projects in Claude Code?", "claude_projects", {}),
    ("show me my projects", "claude_projects", {}),
    ("what am I working on", "claude_projects", {}),
    ("Tell me how many projects I am doing in Claude Code.", "claude_projects", {}),
    ("Please name those projects you are working on Claude Code.", "claude_projects", {}),
    ("Yes, please recall them and I want name of those projects.", "claude_projects", {}),
    ("What's the status of the Solvix project?", "claude_project_status", {"project": "solvix"}),
    ("how is the hackathon project going", "claude_project_status", {"project": "hackathon"}),
    ("ask Claude Code in Solvix to fix the login bug", "claude_code_task", {"project": "solvix", "task": "fix the login bug"}),
    ("open claude code", "open_any", {"name": "claude code"}),
    ("Play any Bollywood song on YouTube.", "play_youtube", {"query": "bollywood song"}),
    ("So, Jarvis, play a random song on Spotify.", "play_spotify", {"query": "a random song"}),
    ("Jarvis, can you listen to me?", "ack", {}),
    ("listen to arijit singh", "play_youtube", {"query": "arijit singh"}),
    ("Can you open YouTube for me on Google Chrome?", "open_website", {"site": "youtube", "browser": "google chrome"}),
    ("search for weather in lahore in edge", "web_search", {"query": "weather in lahore", "browser": "edge"}),
    ("play lofi beats on youtube in chrome", "play_youtube", {"query": "lofi beats", "browser": "chrome"}),
    ("open google chrome", "open_any", {"name": "google chrome"}),
    ("open v s code", "open_any", {"name": "vs code"}),
    ("Who emailed me today?", "check_email", {"period": "today"}),
    ("any new emails", "check_email", {"period": "unread"}),
    ("check my inbox", "check_email", {}),
    ("which emails do I need to reply to", "emails_to_reply", {}),
    # exact sentences from the 01:24 session
    ("So, can you tell me about what emails I have received?", "check_email", {}),
    ("So Jarvis tell me, can you send email to someone?", "email_ask_content", {"to": ""}),
    ("Tell me the status of Hackathon.", "claude_project_status", {"project": "hackathon"}),
    ("can you send an email to Sara", "email_ask_content", {"to": "sara"}),
    ("send an email to Ahmed", "email_ask_content", {"to": "ahmed"}),
    ("what emails did I get today", "check_email", {}),
    ("send an email", "email_ask_content", {"to": ""}),
    # WhatsApp
    ("WhatsApp Ali that I'm running late", "whatsapp_message", {"contact": "ali", "message": "i'm running late"}),
    ("send a WhatsApp message to Basit saying the meeting is at 5", "whatsapp_message",
     {"contact": "basit", "message": "the meeting is at 5"}),
    ("message Sara on WhatsApp that I'll call her tonight", "whatsapp_message", {"contact": "sara"}),
    ("send a WhatsApp message", "whatsapp_ask", {"contact": ""}),
    ("send a what's app message to Ahmed", "whatsapp_ask", {"contact": "ahmed"}),
    ("message Ali on WhatsApp", "whatsapp_ask", {"contact": "ali"}),
    ("open whatsapp", "open_any", {"name": "whatsapp"}),
    ("I want to write an email", "email_ask_content", {"to": ""}),
    ("Tell me about Hackathon.", None, {}),
    ("Jarvis, tell me the emails I have to reply", "emails_to_reply", {}),
    ("any emails I need to answer?", "emails_to_reply", {}),
    ("what's waiting for my reply", "emails_to_reply", {}),
    ("read the latest email from Ahmed", "read_email", {"sender": "ahmed"}),
    ("read my last email", "read_email", {"sender": ""}),
    ("Email Sara that the demo moves to Friday 3pm", "draft_email",
     {"to": "sara", "about": "the demo moves to friday 3pm"}),
    ("send an email to Ahmed Raza saying I'll be late", "draft_email", {"to": "ahmed raza", "about": "i'll be late"}),
    ("email sara about the Q3 review", "draft_email", {"to": "sara", "about": "about the q3 review"}),
    ("What's 18% of 4,500?", "calculate", {}),
    ("what is 25 times 4", "calculate", {}),
    ("what time is it", "tell_time", {"what": "time"}),
    ("what's the date today", "tell_time", {"what": "date"}),
    ("volume 40", "set_volume", {"action": "set", "level": "40"}),
    ("set the volume to 30 percent", "set_volume", {"action": "set", "level": "30"}),
    ("turn it up", "set_volume", {"action": "up"}),
    ("mute", "set_volume", {"action": "mute"}),
    ("pause", "media_control", {"action": "play_pause"}),
    ("next song", "media_control", {"action": "next"}),
    ("skip this track", "media_control", {"action": "next"}),
    ("stop", "stop", {}),
    ("never mind", "stop", {}),
    ("close Spotify", "close_app", {"name": "spotify"}),
    ("lock the screen", "lock_screen", {}),
    ("what can you do", "help", {}),
    ("Jarvis, what can you do for me?", "help", {}),
    ("what are your capabilities", "help", {}),
    ("how can you help me", "help", {}),
    ("Ok Jarvis, what you can do for me?", "help", {}),
    ("tell me what all you can do", "help", {}),
    ("what things can you do", "help", {}),
    ("what are you capable of", "help", {}),
    ("what can you tell me about black holes", None, {}),
    ("tell me a joke", None, {}),
    ("who is the prime minister of pakistan", None, {}),
]


@pytest.mark.parametrize("text,tool,args", CASES)
def test_parse(text, tool, args):
    got = parse(text)
    if tool is None:
        assert got is None, got
        return
    assert got is not None and got.tool == tool, got
    for k, v in args.items():
        assert got.args.get(k) == v, (k, got.args)


def test_normalize():
    assert normalize("Hey Jarvis, could you please open Chrome for me?") == "open chrome"
    assert normalize("OK Jarvis. Open github.com") == "open github.com"


def test_followups():
    for y in ("yes", "send it", "go ahead", "yeah send it"):
        assert YES.match(normalize(y))
    for n in ("no", "don't send it", "cancel", "keep it as a draft"):
        assert NO.match(normalize(n))
    assert parse_choice("the second one") == 2
    assert parse_choice("third") == 3
    assert parse_choice("open the first one") == 1
    assert parse_choice("hello") is None


@pytest.mark.parametrize("text,value", [
    ("What's 18% of 4,500?", 810), ("what is 25 times 4", 100), ("12*7", 84), ("100 divided by 8", 12.5),
    ("square root of 144", 12), ("2 to the power of 10", 1024), ("15 plus 27", 42), ("Calculate 45 minus 12", 33),
])
def test_calc(text, value):
    from jarvis.brain.calc import evaluate

    assert evaluate(text) == pytest.approx(value)


@pytest.mark.parametrize("text", ["open excel", "play 22 by taylor swift", "who wrote 1984", "volume 40", ""])
def test_calc_ignores_non_math(text):
    from jarvis.brain.calc import evaluate

    assert evaluate(text) is None


def test_resolver_hook():
    from jarvis.brain.intents import Intent

    got = parse("open spotify", resolve_open=lambda target, verb: Intent("open_app", {"name": target}))
    assert got.tool == "open_app" and got.args["name"] == "spotify"


@pytest.mark.parametrize("spoken,tool", [
    # Whisper's spellings of "Jarvis" for this voice, copied from the log
    ("Davis, Play Dilruba Song on YouTube.", "play_youtube"),
    ("Davis Close, Spotify,", "close_app"),
    ("Davis played Dilruba's song on Spotify.", "play_spotify"),
    ("Jervais open chrome", "open_any"),
    ("Derivi, what time is it?", "tell_time"),
])
def test_misheard_wake_name_is_dropped(spoken, tool):
    got = parse(normalize(spoken))
    assert got is not None and got.tool == tool


def test_real_name_without_command_is_kept():
    assert normalize("Davis is late today") == "davis is late today"


@pytest.mark.parametrize("spoken,tool,args", [
    # everyday phrasings from the log that used to become a file search or a slow model call
    ("Jarvis find Mustafa and send him a text hi.", "whatsapp_message", {"contact": "mustafa", "message": "hi"}),
    ("Find Mustafa on WhatsApp and send him text-high.", "whatsapp_message", {"contact": "mustafa"}),
    ("Can you send a text to someone on WhatsApp?", "whatsapp_ask", {"contact": ""}),
    ("Please send a text on WhatsApp.", "whatsapp_ask", {"contact": ""}),
    ("send a message to Ali", "whatsapp_ask", {"contact": "ali"}),
    ("text Mustafa that I am late", "whatsapp_message", {"contact": "mustafa", "message": "i am late"}),
    ("send Ali a message on WhatsApp saying hello", "whatsapp_message", {"contact": "ali", "message": "hello"}),
    ("send a text to Ali Khan saying see you at 5", "whatsapp_message", {"contact": "ali khan"}),
    ("send a text saying I'm on my way", "whatsapp_ask", {"contact": "", "message": "i'm on my way"}),
    ("Check WhatsApp and tell me which text I have received", "say", {}),
    ("send an email to Ali", "email_ask_content", {"to": "ali"}),
    ("find my CV", "open_file", {"query": "cv"}),
])
def test_texting_phrasings(spoken, tool, args):
    got = parse(spoken)
    assert got is not None and got.tool == tool
    for k, v in args.items():
        assert got.args.get(k) == v
