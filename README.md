# JARVIS: a voice assistant for your Windows laptop

Say **"Hey Jarvis"** (or press **Ctrl+Alt+J**) and tell it what you need. Jarvis:

- opens and closes apps, files, folders and websites, and tells you where a file is
- plays music on YouTube or Spotify, and controls volume and playback
- researches questions in the ChatGPT or Claude desktop apps, or on the web, and reads you the answer
- tells you the weather
- reads your Gmail inbox and writes emails that are only sent after you say "send it"
- types WhatsApp messages that are only sent after you say "send it"
- briefs you on your Claude Code projects and hands Claude Code tasks to work on in the background
- takes commands from your phone over WhatsApp (text or voice notes)

Speech recognition, the voice and the language model all run on the laptop, and everything is free. The internet is only used for what you ask for (YouTube, Spotify, Gmail, the web, weather, ChatGPT, Claude).

---

## 1. Get the code

The latest version is on the **`rafay`** branch.

```powershell
git clone -b rafay https://github.com/Mohammed-Rafay1/JARVIS-Personal-Assistant.git jarvis
cd jarvis
```

Already cloned it? Get the latest:

```powershell
git fetch origin
git checkout rafay
git pull origin rafay
```

## 2. What you need

| | |
|---|---|
| Windows 10 or 11 (64-bit) | Jarvis drives Windows apps, so it's Windows only |
| 8 GB RAM or more (16 GB recommended) | the language model uses about 2.3 GB while loaded |
| About 5 GB of free disk | speech models ~1.2 GB, language model ~1.9 GB, plus packages |
| A microphone and speakers | a headset works best in noisy rooms |
| **Python 3.12** | `winget install Python.Python.3.12` |
| **Node.js LTS** | `winget install OpenJS.NodeJS.LTS` |
| **Git** | `winget install Git.Git` |

After installing these, close and reopen your terminal so it finds them.

Optional, only for the features that use them: Spotify desktop, WhatsApp desktop, the ChatGPT desktop app, the Claude desktop app, and Claude Code (see [section 6](#6-optional-connections)).

## 3. Install (one command, about 10–20 minutes)

In the `jarvis` folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

This:

1. creates the Python environment (`.venv`) and installs the packages
2. installs the on-screen overlay (`ui`) and the WhatsApp bridge (`whatsapp-bridge`)
3. downloads the offline models: wake word, speech recognition, voice
4. installs [Ollama](https://ollama.com) and downloads the language model (`qwen2.5:3b`)
5. runs a self-test and prints a fix for anything that's broken

## 4. Start it

Double-click **`start_jarvis.bat`**. Jarvis takes about 20 seconds to warm up, greets you, then docks as a glowing HUD orb in the bottom-right corner. Drag it anywhere, on any monitor; it remembers the spot.

| To | Do this |
|---|---|
| Talk | Say **"Hey Jarvis"**, then your command (no need to pause) |
| Talk or type | Press **Ctrl+Alt+J**. A text box opens while the mic also listens. |
| Stop it talking, or cancel | Say "stop" or "never mind", press **Esc**, or click the orb |
| End a conversation | Say "bye", or press ✕ |
| Mute the mic | Right-click the orb, or use the tray icon |
| Move the orb | Drag it (it snaps neatly to screen edges). Tray icon → **Put the orb back in the corner** to reset. |
| Settings, logs, quit | The tray icon (bottom-right, near the clock) |

Start Jarvis automatically when you sign in to Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_startup.ps1
```

Add `-Remove` to that command to undo it.

## 5. Make it yours

### Tune "Hey Jarvis" to your voice (recommended, 1 minute)

Double-click **`calibrate_wake.bat`** in a quiet room and follow the prompts. It sets the mic level and how sensitive the wake word is, then restarts Jarvis. Do this again if Jarvis often misses you or wakes up by accident.

### Settings: `config.yaml`

Open `config.yaml` in any text editor, change what you like, save, and restart Jarvis. The ones most people change:

| Setting | What it does |
|---|---|
| `address_as` | How Jarvis addresses you: `"sir"`, `"boss"`, your name… |
| `weather.city` | Your city, for "what's the weather?" |
| `music.default_service` | `youtube` or `spotify`, for "play X" with no service named |
| `hotkey` | The talk-or-type shortcut (default `Control+Alt+J`) |
| `wake_word.threshold` | Lower (0.1) if it misses you, higher (0.5) if it wakes by accident. `calibrate_wake.bat` sets it for you. |
| `files.search_roots` | Where file search looks (default: your user folder) |
| `apps.aliases`, `websites` | Your own names for apps and sites, e.g. `crm: https://crm.mycompany.com` |
| `contacts.yaml` | Nicknames for "email *name*". People you've emailed before are found automatically. |
| `email.vip`, `email.quiet_hours` | Which new emails are announced, and when |
| `claude_code.*` | Claude Code tasks: on/off, background or terminal, what it may run (see below) |
| `whatsapp_remote.*` | Commands from your phone (see below) |
| `stt.model` | `small.en` recognises better but is about 2× slower |
| `tts.engine` | `kokoro` is the most natural voice, but about 6× slower than the default Piper |
| `speak_typed_replies` | Typed commands get silent, on-screen replies, which is handy in meetings. `true` speaks them. |

## 6. Optional connections

### Gmail

Double-click **`setup_email.bat`**. It walks you through creating a Google **app password**, which is a password just for Jarvis that you can revoke any time. You'll need 2-Step Verification on your Google account, and no Google Cloud project is needed. The password is stored in Windows Credential Manager, not in a file. Some company (Workspace) accounts have app passwords turned off; ask IT to allow them.

### WhatsApp: command Jarvis from your phone

Send Jarvis a text or a **voice note** on WhatsApp. It does the job on the laptop exactly as if you'd said it, and replies in the chat (with a voice note too if you sent one). Lists arrive numbered, so you can answer "2".

**Link it once:** on the first start, a page opens with a QR code. On your phone, open **WhatsApp → Settings → Linked devices → Link a device** and scan it. To link again, or switch numbers, run **`link_whatsapp.bat`**.

- **Same number as your phone:** message Jarvis in your **"Message yourself"** chat.
- **A second number (safer):** link that WhatsApp, add your own number to `whatsapp_remote.allowed` (e.g. `["923001234567"]`), and message it.

Only those chats can command Jarvis. Everyone else, groups, status updates and messages older than 2 minutes are ignored, and voice notes are deleted once they've been transcribed. The laptop must be on and awake. Spotify, WhatsApp sending and the ChatGPT or Claude apps need the screen **unlocked**, because Jarvis uses them on screen. The bridge uses an unofficial library (Baileys), and WhatsApp may block numbers it sees automating; a second number is the safer choice. Set `whatsapp_remote.enabled: false` to turn this off.

### Research with ChatGPT and Claude

Install the **ChatGPT** and/or **Claude** desktop apps and sign in. "Research black holes on ChatGPT" opens a new chat, asks the question, waits for the answer and reads you a summary; the full answer stays in the app. If an app can't be used (not installed, or the laptop is locked), Jarvis answers from the web instead and says why. "Search Google for X" always uses the web.

### Claude Code: hand it work in your projects

1. Install Claude Code, then sign it in once. The Claude desktop app's sign-in isn't shared with the command line.

   ```powershell
   irm https://claude.ai/install.ps1 | iex
   ```

   ```powershell
   claude auth login
   ```

2. Use Claude Code in a project at least once, so Jarvis knows about the project.

Then:

- **"What are my projects?"**, **"How is the Solvix project going?"**: Jarvis reads Claude Code's own notes, on the laptop.
- **"Ask Claude Code in Solvix to add a login page"**: Jarvis asks whether to continue your latest chat or start a new one, reads the task back, and only sends it when you say **"send it"**.
- Claude Code then works **in the background**. It may create and edit files in that project. Anything else it would normally ask about first is refused and listed in the report, apart from the commands in `claude_code.allowed_tools` (git status/diff/log, tests, lint, build).
- When it finishes, Jarvis tells you what changed and what it wasn't allowed to do. It says this out loud, or sends it on WhatsApp if you sent the task from your phone or the laptop is locked. The session is saved as "Jarvis: *project*", so you can open it in Claude Code and carry on.
- **"Is Claude Code done?"** gives progress; **"Stop Claude Code"** cancels the task. Tasks still running after 30 minutes (`claude_code.max_minutes`) are stopped.

Set `claude_code.background: false` to open a visible terminal instead (where you approve each step yourself), or `claude_code.allow_tasks: false` for status only.

## 7. Things to say

| | Examples |
|---|---|
| Apps | "Open Excel", "Launch VS Code", "Close Spotify" (the app still asks to save your work) |
| Files & folders | "Open my Downloads folder", "Open flyer data.rar from my downloads", "Where is the budget spreadsheet?", "Open the Solvix folder on my desktop", then "open it" or "show it in its folder" |
| Web & research | "Search Google for Odoo 18 release notes", "Research the history of Lahore on ChatGPT", "Who was Alan Turing on Claude", "Go to github.com" |
| Weather | "What's the weather?", "Will it rain tomorrow?", "Temperature in Dubai" |
| YouTube | "Play Arijit Singh latest song on YouTube", "Play lofi beats video" |
| Spotify | "Play Blinding Lights on Spotify", "Play my liked songs" |
| Media | "Pause", "Next song", "Volume 40", "Turn it down", "Mute" |
| Email | "Who emailed me today?", "Which emails do I need to reply to?", "Email Sara that the demo moves to Friday 3pm", then "send it", "change it to 4pm" or "cancel" |
| WhatsApp | "WhatsApp Ali that I'm running late", then "send it" or "no" |
| Claude Code | "What are my projects?", "Status of Solvix", "Ask Claude Code in Solvix to fix the login bug", "Is Claude Code done?" |
| Laptop | "Lock the screen", "Put the laptop to sleep", "What time is it?" |
| Chat & advice | "I've got a presentation tomorrow and I'm nervous, any advice?", "How do you open apps without hands?", then just keep talking: it remembers the last few exchanges. "What?" or "Say that again" repeats its last answer. |
| Other | "What's 18% of 4,500?", "Tell me a joke"; after an answer, "search it" looks the question up on the web |
| Chains | "Open Chrome and play lofi on YouTube" |
| Corrections | "No, open Spotify": the new command replaces whatever Jarvis was asking |

## 8. What Jarvis never does on its own

- **Email, WhatsApp messages and Claude Code tasks are only sent after you confirm** ("send it" or the Send button). That rule is in code, not in the AI's instructions.
- It never enters passwords or creates accounts.
- It never clicks or types into an app unless that app is in front and the exact control is found; otherwise it stops and tells you.
- Claude Code tasks stay inside their project folder, and anything risky is refused and reported to you.

## 9. When something's wrong

| Problem | Fix |
|---|---|
| Jarvis doesn't hear "Hey Jarvis" | Check the mic isn't muted (orb, tray icon, Windows sound settings), then run `calibrate_wake.bat` |
| It wakes up by accident (TV, videos) | Run `calibrate_wake.bat`, or raise `wake_word.threshold` |
| "Claude Code on this laptop isn't signed in" | Run `claude auth login` in a terminal once |
| WhatsApp stopped answering | Run `link_whatsapp.bat` and scan the QR code again |
| ChatGPT research falls back to the web | Unlock the laptop, and make sure the ChatGPT app is installed and signed in |
| The first question after a long break is slow | The language model is loading (10–60 s); Jarvis says "One moment" |
| Anything else | Run the self-test, which prints a fix for each broken part. Logs are in `data\logs\jarvis.log`. |

```powershell
start_jarvis.bat --selftest
```

## 10. How it works

```
Mic ─► wake word ("Hey Jarvis") ─► end-of-speech detection ─► Whisper (speech → text)
                                                                    │
            intent engine (instant, handles most commands) ◄────────┤
            local LLM via Ollama (questions, chat, email writing) ◄─┘
                                    │
   tools: apps, files, browser, YouTube, Spotify, media, weather, Gmail, WhatsApp,
          ChatGPT / Claude research, Claude Code
                                    │
                  Piper voice ─► speakers        overlay orb ◄── WebSocket (127.0.0.1)
                                                 WhatsApp bridge (Node) ◄─► your phone
```

- **Backend:** Python, in `backend/jarvis/`. It handles audio, recognition, decisions and tools.
- **Overlay:** Electron + Three.js, in `ui/`. The orb's colour shows what's happening: blue/cyan is you talking, violet is thinking, teal is doing something, amber is Jarvis speaking, red is an error.
- **WhatsApp bridge:** Node, in `whatsapp-bridge/`, linked to WhatsApp as a device.
- Design notes: [PLAN_REVIEW.md](PLAN_REVIEW.md) and [QA_REVIEW.md](QA_REVIEW.md).

## 11. For developers

Run the tests (they don't need a mic or any accounts):

```powershell
.venv\Scripts\python -m pytest tests -q
node --test ui\position.test.js
```

The overlay writes its own problems (and where the orb was moved) to `data\logs\overlay.log`.

Work on your own branch and open a pull request, rather than pushing to `main`.

### Speed on a typical laptop

Measured on an Intel i5-10310U with 16 GB RAM:

| Step | Time |
|---|---|
| Speech recognition (Whisper base.en) | ~1.4 s per command |
| Voice (Piper) | ~0.3 s per sentence; repeated phrases are cached |
| Language model answer (qwen2.5:3b) | ~1.5 s to start when warm; 10–60 s for the first question after a long idle |
| Memory | ~0.6 GB, plus ~2.3 GB while the language model is loaded |
