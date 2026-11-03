"""Deterministic intent engine: the primary path for commands.

Fast (<1 ms), works without the LLM, and predictable. Anything it can't parse goes to the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Intent:
    tool: str                       # a tool name, or: stop | help | open_any
    args: dict = field(default_factory=dict)


_WAKE = re.compile(r"^(?:(?:hey|hi|okay|ok|hello|yo|a)\s+)?(?:jarvis|jervis|travis|service|jarviss|jarvi|jarvas|"
                   r"jervais|javis|jarwis|derivis|derivi|devis)\b[\s,.!]*")
# Whisper's other spellings of "Jarvis" for this voice are also real names, so they are only dropped when a
# command follows: "Davis, play Dilruba on Spotify" -> "play Dilruba on Spotify", but "Davis is late" stays.
_WAKE_NAME_CMD = re.compile(
    r"^(?:davis|dervis|purvis|harvis|charvis)[\s,.!]+(?=(?:please |can you |could you )?(?:open|close|play|played|"
    r"pause|stop|search|google|find|send|email|check|show|tell|what|what's|how|lock|mute|unmute|set|turn|go|"
    r"launch|start|read|reply|whatsapp|volume|next|skip)\b)")
_POLITE_PRE = re.compile(
    r"^(?:please|kindly|just|now|and|so|um|uh|hey|can you|could you|would you|will you|would you mind|"
    r"i want you to|i'd like you to|i would like you to|i want to|i would like to|i'd like to|"
    r"go ahead and|let's|lets|i need you to|i need to|you can)\b[\s,]*")
_POLITE_POST = re.compile(r"[\s,]*\b(?:please|for me|now|thanks|thank you|jarvis|right now|quickly)$")


def normalize(text: str) -> str:
    t = text.lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)            # 4,500 -> 4500
    t = re.sub(r"[^\w\s@.'%\-:]", " ", t)
    t = re.sub(r"(?<!\w)\.|\.(?!\w)", " ", t)          # drop sentence dots, keep dots inside domains/emails
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\b([b-hj-z]) ([b-hj-z])\b", r"\1\2", t)   # spelled-out letters: "v s code" -> "vs code"
    t = re.sub(r"\s*\b(?:for me|on my behalf)\b", "", t)    # "open youtube for me on chrome"
    t = re.sub(r"\b(can|could|would|will) u\b", r"\1 you", t)  # typed shorthand: "can u send an email"
    t = re.sub(r"\bwhat'?s ?app\b", "whatsapp", t)  # Whisper's spelling of WhatsApp (not "what's up")
    for _ in range(4):  # "so, jarvis, can you play..." peels off in layers
        new = _POLITE_POST.sub("", _POLITE_PRE.sub("", _WAKE.sub("", _WAKE_NAME_CMD.sub("", t)))).strip()
        if new == t:
            break
        t = new
    # "Davis played Dilruba's song on Spotify": Whisper hears the request as past tense
    t = re.sub(r"^played\b(?=.*\bon (?:spotify|youtube)$)", "play", t)
    # "Jarvis opens Spotify", "playing Dilruba", "closes Chrome": the command verb heard with an ending
    # ("opening hours..." is a question, so only the "-s" forms, and "playing", are taken as commands)
    t = re.sub(r"^(open|play|close|search|launch)(?:s|es)\b", r"\1", t)
    t = re.sub(r"^playing\b(?=.+\bon (?:spotify|youtube)$)", "play", t)
    return t


_ORDINALS = {"first": 1, "1st": 1, "one": 1, "1": 1, "second": 2, "2nd": 2, "two": 2, "2": 2, "to": 2, "too": 2,
             "third": 3, "3rd": 3, "three": 3, "3": 3, "fourth": 4, "4th": 4, "four": 4, "4": 4,
             "last": -1, "top": 1}

# Confirmations as people actually say them: "yes, please send it", "okay go ahead and send it", "send it now".
# (A strict list once turned "yes, please send it" into an edit request that ended up inside an email.)
_YES_LEAD = r"(?:(?:yes|yeah|yep|yup|sure|ok|okay|alright|all right|please|go ahead|right|great|perfect|fine|and|so)\s+)*"
_YES_CORE = (r"(?:send|send it|send that|send this|send the (?:e-?mail|mail|message|reply)|send it (?:now|off|over)|"
             r"go ahead|do it|confirm|confirmed|please do|yes|yeah|yep|yup|sure|ok|okay|alright|sounds good|perfect|"
             r"looks good|looks fine|correct|that's right|right|absolutely|go for it|ship it|that's fine|fine)")
YES = re.compile(r"^" + _YES_LEAD + _YES_CORE + r"(?:\s+(?:please|now|then|sir))*$")
NO = re.compile(r"^(?:no|nope|nah|cancel|cancel it|don't|do not|don't send|don't send it|do not send|stop|"
                r"never ?mind|leave it|keep it|save it|keep it as a draft|discard|forget it|not now|no thanks)$")
STOP = re.compile(r"^(?:stop|stop talking|cancel|never ?mind|nothing|shut up|quiet|be quiet|silence|"
                  r"that's all|that is all|forget it|no thanks|no thank you|go away|dismiss|enough|ok stop|"
                  r"okay stop|hush|bye|goodbye|good bye|bye bye|bye for now|end call|end the call|hang up|"
                  r"that's it|that will be all|that'll be all|go to sleep|dismissed|you can go|see you|see you later)$")


# "Hey Jarvis" as Whisper writes it for different accents. Names that are also real names (Travis, Davis)
# only count after a greeting, so "Travis is late" never wakes it.
_NAME = r"(?:jarvis|jarviss|jarvi|jarvas|jervis|jervais|javis|jarwis|derivis|derivi|devis|dear ?wiz|dear ?viss)"
WAKE_TEXT = re.compile(
    r"^\W*(?:(?:(?:hey|hi|hay|hey there|okay|ok|hello|yo|a)[\s,]+(?:" + _NAME[3:-1] + r"|travis|davis|service))"
    r"|" + _NAME + r")\b[\s,.!?]*(?P<rest>.*)$", re.I)


def wake_phrase(text: str) -> str | None:
    """None if the text doesn't start with "Hey Jarvis"; else whatever was said after it ("" if nothing)."""
    m = WAKE_TEXT.match(text.strip())
    return None if m is None else m.group("rest").strip()


def _asks_capabilities(t: str) -> bool:
    """Any short phrasing of "what can you do": spoken word order varies ("what you can do for me",
    "tell me what all you can do", "what things can you do")."""
    if len(t.split()) > 12:
        return False
    return bool(re.search(r"\bwhat\b.*\b(?:can|could) you\b.*\b(?:do|help|handle|offer)\b"
                          r"|\bwhat\b.*\byou (?:can|could|are able to) (?:do|help|handle|offer)\b"
                          r"|\b(?:your|you'?re) (?:capabilities|abilities|features|skills|powers)\b"
                          r"|\bwhat are you (?:capable|good) (?:of|at)\b", t))


# Short "bye <anything>": Whisper spells "Jarvis" many ways for different accents ("bye derivi", "bye dear-wiz")
_BYE = re.compile(r"^(?:ok |okay |thanks |thank you )?(?:(?:bye|goodbye|good bye|bye bye|by by)(?: [\w'-]+){0,2}|"
                  r"buy (?:jarvis|jervis|javis|travis|service|services))$")
# checking the line is open: answer, don't act
_ACK = re.compile(r"^(?:listen to me|are you listening|are you listening to me|can you hear me|hear me|do you hear me|"
                  r"you there|are you there|hello|hi|hey|testing|test|mic test|listen)$")


def parse_choice(t: str) -> int | None:
    m = re.match(r"^(?:the |number |option |no )?(first|second|third|fourth|1st|2nd|3rd|4th|one|two|three|four|"
                 r"[1-4]|last|top)(?: one| option| file| result)?$", t)
    if m:
        return _ORDINALS[m.group(1)]
    m = re.match(r"^(?:open |pick |choose |select )(?:the |number )?(first|second|third|fourth|1st|2nd|3rd|4th|"
                 r"one|two|three|four|[1-4]|last|top)(?: one)?$", t)
    return _ORDINALS[m.group(1)] if m else None


_FILE_KIND = (r"pdf|file|document|doc|docx|spreadsheet|sheet|excel file|excel sheet|excel|word file|"
              r"word document|word doc|presentation|deck|slides|slide deck|ppt|powerpoint|image|photo|picture|"
              r"screenshot|video|zip|csv|text file")
_KIND_NORMAL = {"excel file": "excel", "excel sheet": "excel", "word file": "word", "word document": "word",
                "word doc": "word", "slide deck": "deck", "docx": "word", "text file": "text", "file": ""}


def _media(t: str) -> Intent | None:
    if re.match(r"^(?:pause|resume|unpause|continue|continue playing|play|stop the music|stop music|stop playing|"
                r"stop the video|pause (?:the |this )?(?:music|song|video|track|playback|it)|"
                r"resume (?:the )?(?:music|song|video|playback)|play (?:the )?music)$", t):
        return Intent("media_control", {"action": "play_pause"})
    if re.match(r"^(?:next|skip|skip it|skip this|skip (?:this |the )?(?:song|track|video)|"
                r"(?:play )?(?:the )?next (?:one|song|track|video)|next please)$", t):
        return Intent("media_control", {"action": "next"})
    if re.match(r"^(?:previous|go back|back|(?:play )?(?:the )?(?:previous|last) (?:one|song|track|video)|"
                r"play (?:that|it) again)$", t):
        return Intent("media_control", {"action": "previous"})
    return None


def _volume(t: str) -> Intent | None:
    m = re.match(r"^(?:set |change |turn |put )?(?:the )?(?:volume|sound) (?:to |at |level )?(\d{1,3})(?: ?%| percent)?$", t) \
        or re.match(r"^(?:set |change |put )?(?:the )?volume (?:level )?(?:to|at) (\d{1,3})(?: ?%| percent)?$", t)
    if m:
        return Intent("set_volume", {"action": "set", "level": m.group(1)})
    if re.match(r"^(?:volume up|sound up|louder|(?:a bit |little )?louder|turn (?:it|the volume|the sound|volume) up|"
                r"increase (?:the )?volume|raise (?:the )?volume|turn up (?:the )?(?:volume|sound)|pump it up)$", t):
        return Intent("set_volume", {"action": "up"})
    if re.match(r"^(?:volume down|sound down|quieter|softer|(?:a bit |little )?quieter|turn (?:it|the volume|the sound|volume) down|"
                r"decrease (?:the )?volume|lower (?:the )?volume|reduce (?:the )?volume|turn down (?:the )?(?:volume|sound))$", t):
        return Intent("set_volume", {"action": "down"})
    m = re.match(r"^(mute|unmute)(?: the)?(?: volume| sound| audio| computer| laptop| speakers| it)?$", t)
    if m:
        return Intent("set_volume", {"action": m.group(1)})
    if re.match(r"^(?:what(?:'s| is) (?:the )?volume|current volume)$", t):
        return Intent("set_volume", {"action": "set", "level": ""})
    return None


_EMAIL_LINK = (r"that|saying|to say|and say|and tell (?:him|her|them)|telling (?:him|her|them)|to tell (?:him|her|them)|"
               r"and let (?:him|her|them) know|letting (?:him|her|them) know|to let (?:him|her|them) know|"
               r"asking (?:him|her|them)|and ask (?:him|her|them)|to ask (?:him|her|them)|about|regarding|re")
_EMAIL = re.compile(
    r"^(?:send |write |compose |draft |shoot |drop )?(?:an |a )?(?:e-?mail|mail)(?: to)? "
    r"(?P<to>[\w.@'+-]+(?: [\w.@'+-]+){0,2}?) (?P<link>" + _EMAIL_LINK + r") (?P<about>.+)$")
_EMAIL2 = re.compile(r"^(?:e-?mail|mail|write to)(?P<to>[\w.@'+-]+(?: [\w.@'+-]+){0,2}?) (?P<link>"
                     + _EMAIL_LINK + r") (?P<about>.+)$")


_WA = r"(?:whatsapp|whats app)"
_SAYING = r"(?:that|saying|to say|and say|and tell (?:him|her|them)|telling (?:him|her|them)|to tell (?:him|her|them))"


def _whatsapp(t: str) -> Intent | None:
    """'WhatsApp Ali that I'm running late', 'send a WhatsApp message to Sara', 'message Basit on WhatsApp'."""
    if not re.search(r"\b" + _WA + r"\b", t) and not re.match(r"^(?:message|text) ", t):
        return _texting(t)
    m = re.match(r"^(?:send |write )?(?:a |an )?" + _WA + r"(?: message)? (?:to )?(?P<to>[\w .'-]+?)"
                 r"(?: on " + _WA + r")? " + _SAYING + r" (?P<msg>.+)$", t) \
        or re.match(r"^(?:message|text) (?P<to>[\w .'-]+?) on " + _WA + r" " + _SAYING + r" (?P<msg>.+)$", t)
    if m:
        return Intent("whatsapp_message", {"contact": m.group("to").strip(), "message": m.group("msg").strip()})
    m = re.match(r"^(?:send |write )?(?:a |an )?" + _WA + r"(?: message)?(?: to (?P<to>[\w .'-]+))?$", t) \
        or re.match(r"^(?:message|text) (?P<to>[\w .'-]+?) on " + _WA + r"$", t)
    if m:
        to = (m.groupdict().get("to") or "").strip()
        return Intent("whatsapp_ask", {"contact": "" if to in ("someone", "somebody", "anyone") else to})
    return _texting(t)


_NOBODY = {"", "someone", "somebody", "anyone", "him", "her", "them", "a friend"}


def _texting(t: str) -> Intent | None:
    """Everyday ways of asking for a message, from the log: "find Mustafa and send him a text hi",
    "send a text to someone on WhatsApp", "send Ali a message on WhatsApp saying hello", "text Ali that
    I'm late". Email has its own words, so anything mentioning mail is left to the email parser."""
    if re.search(r"\b(?:e-?mails?|mail|inbox|gmail)\b", t):
        return None
    has_wa = re.search(r"\b" + _WA + r"\b", t)
    s = re.sub(r"\s*\b(?:on|in|via|through|using|over) " + _WA + r"\b", "", t).strip()
    if has_wa and re.search(r"\b(?:check|read|any|new|received|unread|got|show)\b", s) \
            and not re.search(r"\b(?:send|write|drop|text|message)\b(?! i have)", s.replace("text i have", "")):
        return Intent("say", {"text": "I can't read your WhatsApp messages yet, sir. I can send one for you: "
                                      "just tell me who to message."})
    to = ""
    m = re.match(r"^(?:find|search for|look for|look up|go to|open) (?P<to>[\w .'-]+?)(?:'s chat)? and (?P<rest>.+)$", s)
    if m:
        to, s = m.group("to"), m.group("rest")
    msg = ""
    m = re.match(r"^(?:send|write|drop)(?: (?P<to>(?!an?\b|the\b)[\w'-]+(?: (?!an?\b)[\w'-]+)?))? (?:a |an )?(?:quick )?"
                 r"(?:text|message|msg|text message)\b(?P<after>.*)$", s)
    if m:
        to = to if (m.group("to") or "") in _NOBODY else m.group("to")
        after = m.group("after").strip()
        k = re.match(r"^to (?P<to>[\w .'-]+?)(?: " + _SAYING + r" (?P<msg>.+))?$", after)
        if k:
            to = k.group("to") if to in _NOBODY else to
            msg = k.group("msg") or ""
        else:
            msg = re.sub(r"^(?:" + _SAYING + r"|saying|says|:|,)\s*", "", after)
    else:
        m = re.match(r"^(?:text|message) (?P<to>(?!me\b)[\w'-]+(?: [\w'-]+)?)(?:(?: " + _SAYING + r"|,|:) (?P<msg>.+))?$", s)
        if not m or not (has_wa or m.group("msg")):  # "message Ali" alone is too vague without WhatsApp
            return None
        to, msg = m.group("to"), m.group("msg") or ""
    to = "" if to.strip() in _NOBODY else to.strip()
    msg = msg.strip(" -,:")
    if to and msg:
        return Intent("whatsapp_message", {"contact": to, "message": msg})
    return Intent("whatsapp_ask", {"contact": to, "message": msg})


def _email(t: str) -> Intent | None:
    # "which emails do I need to reply to", "any emails I have to answer", "what's waiting for my reply"
    if len(t.split()) <= 14 and (
            re.search(r"\b(?:e-?mails?|mails?|messages?|inbox)\b", t) and re.search(r"\b(?:reply|replies|respond|answer|get back)", t)
            or re.search(r"\bwaiting (?:for|on) (?:my |a )?(?:reply|response|answer)\b", t)):
        return Intent("emails_to_reply")
    m = _EMAIL.match(t) or _EMAIL2.match(t)
    if m:
        link, about = m.group("link"), m.group("about")
        if link.startswith(("ask", "and ask", "to ask")):
            about = "ask them " + about
        elif link in ("about", "regarding", "re"):
            about = "about " + about
        return Intent("draft_email", {"to": m.group("to"), "about": about})
    short = len(t.split()) <= 14
    # "can you send email to someone?" is a question about the ability, not a command
    if short and re.search(r"\b(?:can|could|will) you (?:send|write|draft|compose)\b.*\b(?:e-?mails?|mails?)\b", t) \
            and not re.search(r"\b(?:that|saying|about|telling|asking)\b", t):
        m = re.search(r"\bto (?P<to>(?!someone|anyone|somebody|people)[\w.@'-]+(?: [\w.@'-]+)?)$", t)
        return Intent("email_ask_content", {"to": m.group("to") if m else ""})
    if re.match(r"^(?:send|write|draft|compose)(?: an| a| new| a new)? (?:e-?mail|mail)"
                r"(?: for me| now| to (?:someone|somebody|anyone|anybody))?$", t) \
            or re.match(r"^(?:e-?mail|mail) (?:someone|somebody|anyone|anybody)$", t):
        return Intent("email_ask_content", {"to": ""})
    m = re.match(r"^(?:send|write|draft|compose)(?: an| a)? (?:e-?mail|mail) to "
                 r"(?P<to>(?!(?:someone|somebody|anyone|anybody)\b)[\w.@'-]+(?: [\w.@'-]+)?)$", t)
    if m:  # recipient but no message yet: ask what to say
        return Intent("email_ask_content", {"to": m.group("to")})
    # "what emails have I received", "tell me about my emails", "read my emails", "what did I get today"
    if short and not re.match(r"^(?:read|summari[sz]e|open)\b", t) and not re.search(r"\bfrom \w", t) \
            and re.search(r"\b(?:e-?mails?|mails?|inbox)\b", t) and re.search(
            r"\b(?:received|receive|got|get|getting|came|come|arrived|new|unread|today|tell me|show|read|list|"
            r"what|which|any|latest|recent|check|see)\b", t):
        period = "unread" if re.search(r"\b(?:unread|new)\b", t) else "today"
        if re.search(r"\b(?:this week|recently|lately|recent)\b", t):
            period = "recent"
        return Intent("check_email", {"period": period})
    if re.search(r"\b(?:who(?:'s| has| have)? (?:e-?mailed|mailed|sent me|written)|anyone (?:e-?mail|mail)|"
                 r"any (?:new |unread )?(?:e-?mails?|mails?|messages in my inbox)|check (?:my |the )?(?:e-?mails?|mail|inbox)|"
                 r"do i have (?:any )?(?:new |unread )?(?:e-?mails?|mail)|what(?:'s| is) in my inbox|"
                 r"how many (?:new |unread )?(?:e-?mails|mails)|(?:new|unread) (?:e-?mails|mail)|my inbox)\b", t):
        period = "unread" if re.search(r"\b(?:unread|new)\b", t) else "today"
        if re.search(r"\b(?:this week|recently|lately)\b", t):
            period = "recent"
        return Intent("check_email", {"period": period})
    # "every Cheamills I have received in past 24 hours": the word "emails" misheard, but nothing else
    # Jarvis reads arrives like that (WhatsApp reading isn't supported and says so before this)
    if short and re.search(r"\b(?:i have|i've|have i|did i|i) (?:received|got)\b", t) \
            and re.search(r"\b(?:what|which|every|any|all|anything|tell me|show|list)\b", t) \
            and re.search(r"\b(?:today|yesterday|this morning|(?:past|last) \d+ hours?|(?:past|last) (?:day|hour))\b", t):
        return Intent("check_email", {"period": "today"})
    m = re.match(r"^(?:read|summari[sz]e|open)(?: me)?(?: the| my)? (?:latest|last|newest|most recent|recent|new)? ?"
                 r"(?:e-?mail|mail|message)(?: from (?P<who>.+))?$", t) \
        or re.match(r"^what did (?P<who>.+?) (?:say|send|e-?mail|write)(?: me)?(?: about)?$", t)
    if m:
        return Intent("read_email", {"sender": (m.groupdict().get("who") or "").strip()})
    return None


# "play that song" has nothing to search for; ask instead of playing whatever YouTube returns
_VAGUE = re.compile(r"^(?:that|this|the|a|any|some|one|my|your)?\s*(?:song|track|video|music|tune|number)s?"
                    r"(?: (?:for me|please|again|now))?$")


def _song_target(q: str) -> str:
    """Drop filler so "any bollywood song" searches as "bollywood song"."""
    return re.sub(r"^(?:any|some|a|an|the|me)\s+", "", q).strip()


def _claude(t: str) -> Intent | None:
    """Claude Code projects: list them, a project's status, or a task for one."""
    # a prompt for Claude Code. Without a project named, Jarvis asks which project (and which chat) first.
    m = re.match(r"^(?:ask|tell|get|have) claude(?: code)? (?:in|for|on|with) (?:the |my )?(?P<p>[\w .'-]+?)"
                 r"(?: project)? to (?P<task>.+)$", t)
    if m:
        return Intent("claude_code_task", {"project": m.group("p"), "task": m.group("task")})
    m = re.match(r"^(?:ask|tell|get|have) claude code to (?P<task>.+)$", t) \
        or re.match(r"^(?:give|send|pass) (?:this |the |a |my )?(?:prompt|task|command|request) to claude code"
                    r"(?: in (?:the |my )?(?P<p>[\w .'-]+?)(?: project)?)?[:,]? (?P<task>.+)$", t) \
        or re.match(r"^(?:give|send) claude code (?:this |the |a )?(?:prompt|task|command)[:,]? (?P<task>.+)$", t) \
        or re.match(r"^open claude code and (?:ask it to |tell it to |give it this prompt[:,]? |make it )?(?P<task>.+)$", t) \
        or re.match(r"^claude code[:,]? (?!(?:projects?|status|update|updates)\b)(?P<task>\w+ .+)$", t)
    if m:
        return Intent("claude_code_task", {"project": (m.groupdict().get("p") or "").strip(),
                                           "task": m.group("task").strip()})
    if re.search(r"^(?:what(?:'s| is| are)? )?(?:the )?(?:status|state|update|updates) (?:of|on) (?:all )?my projects"
                 r"|^(?:what are|show|list|tell me(?: about)?|show me)(?: me)?(?: all)? (?:my|the) (?:claude(?: code)? )?projects"
                 r"|^(?:what am i working on|my projects|claude(?: code)? projects|project status|projects status)$"
                 r"|\bmy projects (?:in|on) claude\b|\bprojects in claude(?: code)?\b", t):
        return Intent("claude_projects")
    # any other short question about "my/those projects" ("how many projects am I doing in Claude Code",
    # "name those projects", "recall them, I want the names of the projects") lists them
    if len(t.split()) <= 16 and re.search(r"\bprojects\b", t) and re.search(
            r"\b(?:claude|my|mine|i am|i'm|we are|we're|doing|working|name|names|list|how many|which|what|recall|"
            r"those|these|all|status|tell|show)\b", t):
        return Intent("claude_projects")
    m = re.match(r"^(?:(?:tell me|give me|what(?:'s| is)|show me|let me know|i want)(?: about)? )?(?:the )?"
                 r"(?:latest )?(?:status|state|progress|update|updates) (?:of|on|for) (?:the |my )?(?P<p>.+?)"
                 r"(?: project)?(?: in claude(?: code)?)?$", t) \
        or re.match(r"^how(?:'s| is) (?:the |my )?(?P<p>.+?) (?:project )?(?:going|doing|coming along)$", t) \
        or re.match(r"^what(?:'s| is) (?:happening|going on) (?:in|with) (?:the |my )?(?P<p>.+?)(?: project)?$", t)
    if m:
        return Intent("claude_project_status", {"project": m.group("p")})
    return None


_AI = r"(?:chat ?gpt|gpt|claude|both(?: of them)?|chat ?gpt and claude|claude and chat ?gpt)"
_NOT_RESEARCH = re.compile(r"\b(?:google|the web|online|internet|youtube|spotify|files?|folders?|documents?|pdf|"
                           r"e-?mails?|inbox|my computer|laptop|whatsapp|contacts?)\b")


def _ai_services(name: str) -> str:
    name = (name or "").replace(" ", "")
    return "chatgpt" if "gpt" in name and "claude" not in name else \
        "claude" if "claude" in name and "gpt" not in name else ""


def _research(t: str) -> Intent | None:
    """Questions to research in new ChatGPT and Claude chats: "research X", "search about X", "ask ChatGPT
    X", "open ChatGPT and research X", "search X on Claude". Naming one assistant asks only that one."""
    if re.search(r"\bclaude code\b", t):
        return None
    t2 = re.sub(r"[\s,]*(?:and |then )?(?:tell|give|read|send) me (?:a |the )?(?:short |quick |brief )?summary"
                r"(?: of (?:it|that|the answers?))?$|[\s,]*(?:and |then )?summari[sz]e(?: it| that)?$", "", t)
    # on Google / the web: open the results and read back what they say
    g = re.search(r"\s+(?:on|in|using|with|via|from|through) (?:google|the web|the internet)$|\s+online$", t2)
    m = re.match(r"^(?:do (?:some |a )?(?:quick )?research|research|look into|find out|search|look up|find)"
                 r"(?: about| on| for| into| up)? (?P<q>.+)$", t2[:g.start()]) if g else None
    if m:
        return Intent("web_research", {"query": m.group("q")})
    m = re.match(r"^(?:google research|research on google|research online|use google to research|"
                 r"research (?:it |this )?(?:on|using) google)(?: about| on| for)? (?P<q>.+)$", t2) \
        or (t2 != t and re.match(r"^(?:search google for|google search for|google|search the web for|"
                                 r"search online for) (?P<q>.+)$", t2))  # "search Google for X and tell me the summary"
    if m:
        return Intent("web_research", {"query": m.group("q")})
    svc = ""
    m = re.search(r"\s+(?:on|in|using|with|via|from|through) (?P<svc>" + _AI + r")$", t2)
    if m:
        svc, t2 = m.group("svc"), t2[:m.start()]
    m = re.match(r"^open (?P<svc>chat ?gpt|claude)(?: and| then|,)? (?:ask(?: it)?(?: to)?|research|search|find out|"
                 r"look up|tell me)(?: about| for| on| into)? (?P<q>.+)$", t2) \
        or re.match(r"^ask (?P<svc>" + _AI + r")(?: to (?:research|search|find out|look up)(?: about| for)?| about| for|"
                    r":|,)? (?P<q>.+)$", t2)
    if m:  # "ask Claude to explain recursion" keeps "explain recursion"
        return Intent("ai_research", {"query": re.sub(r"^to ", "", m.group("q")),
                                      "services": _ai_services(m.group("svc"))})
    m = re.match(r"^(?:do (?:some |a )?(?:quick )?research|research|look into|find out|dig into)(?: about| on| into|"
                 r" for| more about)? (?P<q>.+)$", t2)
    if m:
        return Intent("ai_research", {"query": m.group("q"), "services": _ai_services(svc)})
    m = re.match(r"^(?:search|look up|find|google)(?: about| for| up)? (?P<q>.+)$", t2)
    if m and svc:  # "search X on ChatGPT"
        return Intent("ai_research", {"query": m.group("q"), "services": _ai_services(svc)})
    # plain "search X": research it, unless it names Google, YouTube, files, email...
    m = re.match(r"^(?:search for information (?:on|about)|search about|search for|search|look up) (?P<q>.+)$", t2)
    if m and not _NOT_RESEARCH.search(t2) and not re.search(r"\b(?:" + _FILE_KIND + r")$", t2) \
            and not m.group("q").startswith("my "):  # "search for my CV" is a file
        return Intent("ai_research", {"query": m.group("q"), "services": ""})
    return None


def _liked(t: str) -> Intent | None:
    # no trailing \b on purpose: Whisper writes "play top" as "playtop" surprisingly often
    if re.search(r"\b(?:liked songs?|liked music|liked tracks|my likes|favou?rite songs)\b", t) \
            and re.search(r"\b(?:play|start|put on|open|go|listen|shuffle)", t):
        first = re.search(r"(?:top|first|1st|number one)", t)
        return Intent("play_spotify_liked", {"which": "first" if first else "all"})
    return None


def _play_raw(t: str, default_service: str) -> Intent | None:
    m = re.match(r"^(?:play|put on|watch|show me|find|search for|search) (?P<q>.+?) (?:on|in|from|using|at) youtube$", t) \
        or re.match(r"^(?:search )?youtube (?:for )?(?P<q>.+)$", t) \
        or re.match(r"^(?:play|watch|put on) (?P<q>.+?) videos?$", t) \
        or re.match(r"^(?:play|put on) (?:a |the )?(?:youtube |video )(?:video )?(?:of |for |about )?(?P<q>.+)$", t)
    if m:
        return Intent("play_youtube", {"query": m.group("q")})
    m = re.match(r"^(?:play|put on|start|listen to) (?P<q>.+?) (?:on|in|from|using) spotify$", t) \
        or re.match(r"^spotify (?:play )?(?P<q>.+)$", t) \
        or re.match(r"^(?:play|put on) (?P<q>.+?) (?:song|track)s? on spotify$", t)
    if m:
        return Intent("play_spotify", {"query": m.group("q")})
    m = re.match(r"^(?:play|put on|listen to) (?:me )?(?:some |the |a )?(?P<q>.+)$", t)
    # "listen to me" is conversation, not a song called "me"
    if m and m.group("q") not in ("it", "music", "something", "that", "me", "this", "you", "us", "him", "her",
                                  "them", "what i say", "what i'm saying", "carefully"):
        tool = "play_spotify" if default_service == "spotify" else "play_youtube"
        return Intent(tool, {"query": m.group("q")})
    return None


def _play(t: str, default_service: str) -> Intent | None:
    intent = _play_raw(t, default_service)
    if intent is None:
        return None
    q = intent.args["query"]
    if _VAGUE.match(q) or q in ("random song", "random music", "a random song"):
        if "random" in q:  # an explicit "random song" is a fine request
            return intent
        return Intent("ask_song", {"service": intent.tool})
    intent.args["query"] = _song_target(q)
    return intent


def _open(t: str, resolve_open: Callable[[str, str], Intent] | None) -> Intent | None:
    m = re.match(r"^(?:find|search for|look for|locate)(?: me)? (?:the |my |a )?(?P<q>.+?)(?: (?P<kind>" + _FILE_KIND +
                 r"))?(?: on (?:my |this )?(?:computer|laptop|pc|drive))?$", t)
    if m:
        kind = _KIND_NORMAL.get(m.group("kind") or "", m.group("kind") or "")
        return Intent("open_file", {"query": m.group("q"), "kind": kind})
    m = re.match(r"^(?P<verb>open|launch|start|run|bring up|show me|show|go to|take me to|navigate to|visit|load|"
                 r"fire up|pull up|switch to|get me|open up) (?:up )?(?P<target>.+)$", t)
    if not m:
        return None
    target = re.sub(r"^(?:the|my|a|an|up)\s+", "", m.group("target")).strip()
    verb = m.group("verb")
    fm = re.match(r"^(?P<q>.+?) (?P<kind>" + _FILE_KIND + r")$", target)
    if fm and fm.group("kind") in ("file", "pdf", "document", "doc", "docx", "spreadsheet", "sheet", "presentation",
                                   "deck", "slides", "slide deck", "ppt", "csv", "excel file", "excel sheet",
                                   "word file", "word document", "word doc", "image", "photo", "picture",
                                   "screenshot", "zip", "text file") \
            and not re.match(r"^(?:file|files) explorer$", target):
        kind = _KIND_NORMAL.get(fm.group("kind"), fm.group("kind"))
        return Intent("open_file", {"query": fm.group("q"), "kind": kind})
    if re.search(r"\b(?:website|site|dot com|\.com|\.org|\.net|\.io|\.pk|\.ai)\b", target) or verb in (
            "go to", "take me to", "navigate to", "visit"):
        if not re.search(r"\b(?:folder|directory)\b", target):
            return Intent("open_website", {"site": target})
    if resolve_open:
        return resolve_open(target, verb)
    return Intent("open_any", {"name": target})


_IN_BROWSER = re.compile(r"\s+(?:on|in|using|with|through)\s+(?:the |my )?"
                         r"(google chrome|chrome|microsoft edge|edge|firefox|brave|web browser|browser)$")
_WEB_TOOLS = {"open_website", "web_search", "play_youtube", "web_research"}


def parse(text: str, default_service: str = "youtube",
          resolve_open: Callable[[str, str], Intent] | None = None) -> Intent | None:
    """Like _parse, plus "... on/in Chrome": the browser becomes an argument instead of part of the name."""
    t = normalize(text)
    m = _IN_BROWSER.search(t)
    if not m or not t[:m.start()].strip():
        return _parse(text, default_service, resolve_open)
    browser = "" if m.group(1) in ("browser", "web browser") else m.group(1)
    intent = _parse(t[:m.start()], default_service, resolve_open)
    if intent is None:
        return None
    if intent.tool in ("open_app", "open_any", "open_file"):  # "open whatsapp on chrome" means the website
        name = intent.args.get("name") or intent.args.get("query", "")
        intent = Intent("open_website", {"site": name})
    if intent.tool == "ai_research":  # "search for X in Edge": a browser named means a web search
        intent = Intent("web_search", {"query": intent.args["query"]})
    if intent.tool in _WEB_TOOLS:
        intent.args["browser"] = browser
    return intent


def _parse(text: str, default_service: str = "youtube",
           resolve_open: Callable[[str, str], Intent] | None = None) -> Intent | None:
    t = normalize(text)
    if not t:
        return None
    if STOP.match(t) or _BYE.match(t):
        return Intent("stop")
    if _ACK.match(t):
        return Intent("ack")
    if re.match(r"^(?:what (?:all |else )?can you do(?: for me)?|what (?:all |else )?can you help (?:me )?with|help|help me|"
                r"what do you do|what are you capable of|how can you help(?: me)?|what are your (?:skills|commands|"
                r"features|capabilities|abilities|powers)|(?:list|show|tell me)(?: me)? (?:your |all )?(?:of )?(?:your )?"
                r"(?:features|commands|skills|capabilities|abilities)|what can i (?:ask|say|tell)(?: you)?|"
                r"what are you able to do|what do you know how to do)$", t) or _asks_capabilities(t):
        return Intent("help")
    if re.search(r"^(?:what(?:'s| is) the time|what time is it|tell me the time|time please|current time|"
                 r"what time(?:'s| is) it(?: now)?|time now|the time)$", t):
        return Intent("tell_time", {"what": "time"})
    if re.search(r"^(?:what(?:'s| is) (?:the |today's )?date(?: today)?|what day is (?:it|today)|today's date|"
                 r"what(?:'s| is) today|which day is (?:it|today)|what is the day today|date today)$", t):
        return Intent("tell_time", {"what": "date"})
    if re.match(r"^lock (?:the |my |this )?(?:screen|computer|laptop|pc|workstation|system)$", t):
        return Intent("lock_screen")
    from .calc import evaluate

    if evaluate(_WAKE.sub("", text.lower().strip())) is not None:
        return Intent("calculate", {"expression": _WAKE.sub("", text.lower().strip())})
    for f in (_volume, _media):
        hit = f(t)
        if hit:
            return hit
    hit = _whatsapp(t) or _claude(t) or _email(t) or _liked(t) or _play(t, default_service) or _research(t)
    if hit:
        return hit
    m = re.match(r"^(?:search google for|search the web for|google search for|search online for|look online for|"
                 r"web search for|search google|search for|web search|look up|google|search) (?P<q>.+?)"
                 r"(?: on google| on the web| online| on the internet)?$", t)
    if m and not re.search(r"\b(?:youtube|spotify)$", t) and not re.search(r"\b(?:" + _FILE_KIND + r")$", t):
        return Intent("web_search", {"query": m.group("q")})
    m = re.match(r"^(?:close|quit|exit|kill|shut down|shut) (?:the |my )?(?P<app>.+?)(?: app| application| window)?$", t)
    if m and m.group("app") not in ("it", "this", "that", "everything", "all"):
        return Intent("close_app", {"name": m.group("app")})
    return _open(t, resolve_open)
