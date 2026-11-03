# JARVIS: local voice assistant for Windows

Everything runs on this laptop and costs nothing. The internet is used only by the things you ask for (YouTube, Spotify, Gmail, websites).

Design notes and the review of the original plan are in [PLAN_REVIEW.md](PLAN_REVIEW.md).

On Ubuntu, follow [UBUNTU_SETUP.md](UBUNTU_SETUP.md) instead of the Windows steps below.

## Start

Double-click **`start_jarvis.bat`**. Jarvis takes about 20 seconds to warm up, says "Systems are online", then docks as a small orb in the bottom-right corner.

| To | Do this |
|---|---|
| Talk | Say **"Hey Jarvis"**, then your command (no need to pause) |
| Talk or type | Press **Ctrl+Alt+J**. A text box opens, focused, while the mic also listens. |
| Stop it talking, or cancel | Say "stop" / "never mind", press **Esc**, or click the orb |
| Mute the mic | Right-click the orb, or use the tray icon |
| Settings, logs, quit | Tray icon (bottom-right, near the clock) |

Typed commands get silent, on-screen replies, which is handy in meetings. Change this with `speak_typed_replies` in `config.yaml`.

## Things to say

| | Examples |
|---|---|
| Apps | "Open Excel", "Launch VS Code", "Close Spotify" (the app still asks to save your work) |
| Folders & files | "Open my Downloads folder", "Open the Q3 proposal PDF", "Find the budget spreadsheet" |
| Web | "Search Google for Odoo 18 release notes", "Go to github.com", "Open LinkedIn" |
| YouTube | "Play Arijit Singh latest song on YouTube", "Play lofi beats video" |
| Spotify | "Play Blinding Lights on Spotify", "Play my liked songs", "Play the top song in my liked songs" |
| Media | "Pause", "Next song", "Volume 40", "Turn it down", "Mute" |
| Email | "Who emailed me today?", "Read the latest email from Ahmed", "Email Sara that the demo moves to Friday 3pm", then "Send it", "Change it to 4pm" or "Cancel" |
| Other | "What time is it?", "Lock the screen", "What's 18% of 4,500?", "Tell me a joke" |
| Chains | "Open Chrome and play lofi on YouTube" |

"Play X" with no service named uses `music.default_service` (YouTube by default).

## How it works

```
Mic ─► wake word ("Hey Jarvis") ─► end-of-speech detection ─► Whisper (speech → text)
                                                                    │
            intent engine (instant, handles ~90% of commands) ◄─────┤
            local LLM via Ollama (questions, chat, email writing) ◄─┘
                                    │
                         tools: apps, files, browser, YouTube, Spotify, media, Gmail
                                    │
                  Piper voice ─► speakers        overlay orb ◄── WebSocket (127.0.0.1)
```

- **Backend**: Python, in `backend/jarvis/`. It owns audio, recognition, decisions and tools.
- **Overlay**: Electron + Three.js, in `ui/`. The orb's colour shows who is talking: blue/cyan is you, violet is thinking, teal is doing something, amber is Jarvis speaking, red is an error.
- **Email can never be sent without you.** Jarvis creates a Gmail draft and shows it on an editable card. Only "send it" (or the Send button) sends it, and that path is in code, not in the AI prompt.

## Connect Gmail (optional, one time)

Your account is Google Workspace, so you first need an OAuth client. This takes about 5 minutes:

1. Open <https://console.cloud.google.com/> and create a project, e.g. "Jarvis".
2. **APIs & Services → Library**: enable **Gmail API**.
3. **OAuth consent screen**: choose **Internal** and enter an app name.
4. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the JSON.
5. Save it as `secrets\credentials.json` in this folder.
6. Run **`setup_email.bat`** and approve access in the browser.
7. Restart Jarvis.

The access token is stored in Windows Credential Manager, not in a file. If your Workspace admin blocks the app, ask IT to allow the OAuth client ID you created.

## Command Jarvis from your phone (WhatsApp)

Send Jarvis a text or a **voice note** on WhatsApp. It transcribes the note, does the job on the laptop exactly as if you'd said it, and replies in the chat with text, plus a voice note if you sent one. Lists (files, projects, emails) arrive numbered, so you can answer "2". Emails and WhatsApp messages are still only sent after you reply "send it".

**Link it once:** when Jarvis starts, a page opens with a QR code. On your phone open **WhatsApp → Settings → Linked devices → Link a device** and scan it. (`whatsapp_remote.phone` in `config.yaml` gives an 8-digit pairing code instead.)

- **Same number as your phone:** message Jarvis in your **"Message yourself"** chat.
- **A second number (recommended):** link the second WhatsApp, then add your own number to `whatsapp_remote.allowed`, e.g. `["923001234567"]`, and message that number.

Only those chats can command Jarvis. Everyone else, groups, status updates and messages older than 2 minutes are ignored, and voice notes are deleted after transcription. The laptop has to be on and awake. Spotify and WhatsApp-sending need the screen **unlocked**, because they click on screen. To unlink, remove "Jarvis" under Linked devices on your phone. To link again or switch numbers, run **`link_whatsapp.bat`**.

Baileys (the library used) is unofficial. WhatsApp may ban numbers it sees automating, which is why a second number is the safer choice.

## Settings (`config.yaml`)

Common changes:

- `wake_word.threshold`: raise it to 0.6–0.7 if Jarvis wakes up by accident.
- `stt.model: small.en`: more accurate recognition, about 2× slower.
- `tts.engine: kokoro`: the most natural voice, but about 6× slower than Piper on this laptop.
- `email.vip`, `email.quiet_hours`: control which new emails are announced, and when. Announcements also wait while Teams or Zoom is using the mic.
- `apps.aliases`, `websites`: your own shortcuts.
- `contacts.yaml`: nicknames for "email *name*". People you've emailed before are found automatically.

## Maintenance

```bash
start_jarvis.bat --selftest
```

```bash
powershell -ExecutionPolicy Bypass -File scripts\install_startup.ps1
```

```bash
.venv\Scripts\python -m pytest tests -q
```

- `--selftest` checks every component and prints a fix for anything broken.
- `install_startup.ps1` starts Jarvis when you sign in. Add `-Remove` to undo it.
- Logs are in `data\logs\jarvis.log`.
- Fresh install on another PC: `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1`. This needs Python 3.12 and Node.js.

## Performance on this laptop

Measured on the i5‑10310U with the usual background load:

| Step | Time |
|---|---|
| Speech recognition (Whisper base.en) | ~1.4 s per command |
| Voice (Piper) | ~0.3 s per sentence; repeated phrases are cached |
| LLM answer (qwen2.5:3b) | ~1.5 s to start when warm. The first question after 30 min idle takes 10–60 s to load, and Jarvis says "One moment". |
| RAM | ~0.6 GB, plus ~2.3 GB while the LLM is loaded |
