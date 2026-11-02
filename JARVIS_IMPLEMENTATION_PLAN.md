# JARVIS — Local Voice Assistant for Windows
### End-to-end implementation plan (free, offline-first)

> Target: Windows 11 laptop. Everything runs locally and costs nothing.
> The internet is used only by the services you ask for (Gmail, YouTube, Spotify, web).

---

## 1. What it does

| # | Capability | Example command |
|---|---|---|
| 1 | Wake on voice, animated overlay window | "Hey Jarvis" |
| 2 | Open folders and files | "Open my Downloads folder", "Open the Q3 proposal PDF" |
| 3 | Open any installed application | "Open Excel", "Launch VS Code" |
| 4 | Browser control | "Open Chrome", "Search Google for Odoo 18 release notes", "Open github.com" |
| 5 | Play YouTube video | "Play Arijit Singh latest song on YouTube" |
| 6 | Play Spotify song | "Play Blinding Lights on Spotify" |
| 7 | Announce new emails | (automatic) "New email from Ahmed Raza: Proposal review" |
| 8 | Read inbox on demand | "Who emailed me today?" |
| 9 | Draft and send email | "Email Sara that the demo moves to Friday 3pm" → reads draft → "Send it" |
| 10 | Small talk / general Q&A | "What's 18% of 4,500?" |

Non-goals for v1: controlling other people's accounts, deleting files/emails, running arbitrary shell commands from voice.

---

## 2. Architecture

Two processes talk over a local WebSocket:

```
┌──────────────────────────── Python backend (the brain) ─────────────────────────────┐
│                                                                                      │
│  Mic ─► AudioIn (16 kHz) ─┬─► WakeWord (openWakeWord "hey_jarvis")                   │
│                           ├─► VAD (Silero) ─► STT (faster-whisper) ─► transcript     │
│                           └─► Level/FFT meter ──────────────┐                        │
│                                                             │                        │
│  transcript ─► Brain (Ollama LLM + tool calling) ─► ToolRouter ─► tools/*           │
│                     │                                        │                       │
│                     └─► reply text ─► TTS (Kokoro) ─► AudioOut ─► Level/FFT meter ──┤
│                                                                                      │
│  Background: GmailWatcher (poll every 45 s) ─► announcer queue                      │
│                                                                                      │
│  UIBridge (WebSocket ws://127.0.0.1:8765) ◄──── state, levels, transcripts, cards ──┘
└──────────────────────────────────────────────────────────────────────────────────────┘
                                       ▲
                                       │ JSON events (30–60 fps for levels)
                                       ▼
┌──────────────────── Electron overlay (the face) ────────────────────┐
│ Frameless, transparent, always-on-top window                        │
│ Three.js orb (GLSL noise shader + bloom) reacting to voice levels   │
│ Live captions, email/draft cards, confirmation buttons              │
└─────────────────────────────────────────────────────────────────────┘
```

**Why this split?**
- Python owns the best free audio/AI libraries (Whisper, openWakeWord, Kokoro, Ollama client, Gmail API).
- Web tech (Three.js + shaders) gives the best-looking animation with the least effort; Electron gives a proper transparent, click-through, always-on-top window on Windows. (PySide6/QML was considered: fine, but harder to make look premium. Tauri was considered: lighter, but adds a Rust toolchain.)
- The WebSocket keeps them independent, so the UI can be restarted or redesigned without touching the brain.

---

## 3. Technology choices (all free)

| Layer | Choice | Why | Fallback |
|---|---|---|---|
| Wake word | **openWakeWord** — ships a pretrained `hey_jarvis` model | Free, offline, ~1–2% CPU, no API key (unlike Porcupine) | Global hotkey `Ctrl+Alt+J` |
| Voice activity | **Silero VAD** | Accurate end-of-speech detection, tiny | WebRTC VAD |
| Speech-to-text | **faster-whisper** (CTranslate2) | Best free accuracy; handles English + Urdu/Hinglish names | `small.en` int8 on CPU; `distil-large-v3` on NVIDIA GPU |
| Brain | **Ollama** + `qwen2.5:7b-instruct` (tool calling) | Local, free, reliable JSON tool calls | `qwen2.5:3b` / `llama3.2:3b` on low-RAM laptops |
| Text-to-speech | **Kokoro-82M** (`kokoro-onnx`) | Near-human quality, runs real-time on CPU, Apache-2.0 | **Piper** (`en_GB-alan-medium` for a British "Jarvis" feel) |
| Audio I/O | `sounddevice` (PortAudio) | Low latency, callback-based | `pyaudio` |
| Email | **Gmail API** (your Google Workspace account) | Official, free quota is far beyond personal use | IMAP + app password (if API blocked by admin) |
| Contacts | Google **People API** | "Email Sara" → resolves address | Local `contacts.yaml` |
| YouTube | **yt-dlp** `ytsearch1:` → video ID → open in browser | No API key, robust | `youtube-search-python` |
| Spotify | Spotify desktop app + `spotify:` URIs; Web API for search | Works on Free plan (see §6.6) | Spotify Web API playback (Premium only) |
| Apps | Start Menu `.lnk` scan + `Get-StartApps` (UWP) + fuzzy match (`rapidfuzz`) | Finds Win32 *and* Store apps | `os.startfile` by name |
| Files | **Everything** (voidtools) via `es.exe` | Instant search across whole disk | Own index with `os.scandir` + SQLite FTS |
| Overlay UI | **Electron + Vite + Three.js** (`postprocessing` bloom) | Best visuals, transparent window | — |
| Secrets | `keyring` (Windows Credential Manager / DPAPI) | Tokens never in plain files or code | — |
| Config | `pydantic-settings` + `config.yaml` | Typed, validated config | — |
| Logging | `structlog` → rotating file | Debug voice issues later | — |

**Hardware sizing**

| Laptop | STT | LLM | Expected response time |
|---|---|---|---|
| 8 GB RAM, no GPU | whisper `small.en` int8 | `qwen2.5:3b` | ~2.5–3.5 s |
| 16 GB RAM, no GPU | whisper `small.en` int8 | `qwen2.5:7b` (Q4) | ~3–4 s |
| NVIDIA GPU (6 GB+) | `distil-large-v3` fp16 | `qwen2.5:7b` | ~1.2–2 s |

`scripts/detect_hw.py` picks these automatically on first run.

---

## 4. Folder structure

```
jarvis/
├─ README.md
├─ config.yaml                  # user settings (voice, model, hotkey, mail poll interval)
├─ contacts.yaml                # optional nicknames → email
├─ scripts/
│  ├─ setup.ps1                 # one-shot installer (Python venv, Node deps, Ollama models, voices)
│  ├─ detect_hw.py
│  ├─ google_auth.py            # one-time Gmail OAuth consent
│  └─ install_startup.ps1       # registers Task Scheduler entry to start at logon
├─ backend/
│  ├─ pyproject.toml
│  └─ jarvis/
│     ├─ __main__.py            # entry: python -m jarvis
│     ├─ config.py
│     ├─ events.py              # typed event bus (asyncio queues)
│     ├─ state.py               # state machine
│     ├─ audio/
│     │  ├─ mic.py              # 16 kHz mono stream, ring buffer
│     │  ├─ wakeword.py
│     │  ├─ vad.py
│     │  ├─ stt.py
│     │  ├─ tts.py              # Kokoro primary, Piper fallback, sentence streaming
│     │  ├─ player.py           # output stream + level metering + ducking
│     │  ├─ meter.py            # RMS + 32-band FFT for the UI
│     │  └─ sounds/             # earcons: wake.wav, done.wav, error.wav
│     ├─ brain/
│     │  ├─ llm.py              # Ollama client, streaming, tool-calling
│     │  ├─ prompts.py          # system prompt, persona
│     │  ├─ router.py           # tool registry + argument validation
│     │  ├─ fastpath.py         # regex shortcuts that skip the LLM
│     │  └─ memory.py           # short conversation memory (last 6 turns)
│     ├─ tools/
│     │  ├─ base.py             # @tool decorator, pydantic schemas, risk level
│     │  ├─ files.py
│     │  ├─ apps.py
│     │  ├─ browser.py
│     │  ├─ youtube.py
│     │  ├─ spotify.py
│     │  ├─ gmail.py
│     │  └─ system.py           # volume, mute, lock screen, time/date
│     ├─ services/
│     │  ├─ gmail_watcher.py
│     │  └─ app_index.py        # builds/refreshes app list at startup
│     └─ ui_bridge.py           # WebSocket server
├─ ui/
│  ├─ package.json
│  ├─ electron/
│  │  ├─ main.ts                # window creation, tray icon, hotkey, show/hide
│  │  └─ preload.ts
│  └─ src/
│     ├─ main.ts                # WebSocket client, state store
│     ├─ orb/
│     │  ├─ Orb.ts              # Three.js scene
│     │  ├─ orb.vert.glsl       # simplex-noise vertex displacement
│     │  └─ orb.frag.glsl       # fresnel glow + color mix
│     ├─ components/
│     │  ├─ Captions.ts
│     │  ├─ EmailCard.ts
│     │  └─ ConfirmBar.ts
│     └─ theme.ts               # state → color palettes
└─ tests/
   ├─ fixtures/audio/*.wav      # recorded commands for regression tests
   ├─ test_router.py
   ├─ test_fastpath.py
   └─ test_tools_*.py
```

---

## 5. Core pipeline in detail

### 5.1 State machine

```
IDLE ──wake word / hotkey──► LISTENING ──VAD silence 700 ms──► TRANSCRIBING
  ▲                              │ (8 s max / no speech 4 s → IDLE)
  │                              ▼
  │                         THINKING ──tool call──► ACTING ──► SPEAKING
  │                                                              │
  └────── FOLLOW_UP (6 s window: talk again without "Hey Jarvis") ◄┘
          │ "send it" / "cancel" handled here for confirmations
```

Each transition emits `{"type":"state","value":"listening"}` to the UI.

### 5.2 Audio input
- One `sounddevice.InputStream` at 16 kHz, 512-sample blocks (32 ms), feeding a ring buffer.
- Every block goes to: wake word (only in IDLE), VAD (in LISTENING), meter (always while the window is visible).
- Keep 1.5 s of pre-roll so a fast "Hey Jarvis open Chrome" (no pause) still captures "open Chrome".
- Noise suppression: optional `noisereduce`/RNNoise on the captured utterance before STT.

### 5.3 Wake word
- openWakeWord `hey_jarvis` model, threshold 0.5 (tunable in config), 2 s cooldown.
- On trigger: play `wake.wav` chime (≈150 ms), duck system volume to 30% (pycaw), show overlay.
- Optional: train a custom wake word ("Jarvis" alone) with openWakeWord's synthetic-data notebook.

### 5.4 Speech-to-text
- faster-whisper with `vad_filter=True`, `beam_size=1` for speed, `language="en"`.
- `initial_prompt` seeded with your vocabulary (app names, contact names, "Odoo", "Oracle Fusion", "TMC") — big accuracy boost on names.
- Partial captions: run a quick decode every 600 ms while listening so the UI shows text as you speak.

### 5.5 Brain (LLM + tools)
1. **Fast path first** (`fastpath.py`): regexes for very common commands — `open (.+)`, `play (.+) on youtube`, `play (.+) on spotify`, `who emailed me`. These skip the LLM and respond in < 300 ms.
2. Everything else → Ollama `/api/chat` with `tools=[...]` (JSON schemas generated from pydantic models).
3. Router validates arguments, checks the tool's **risk level**:
   - `safe` (open, play, read) → run immediately.
   - `confirm` (send email) → show card + speak summary, wait for "send it" / click.
   - Nothing destructive is exposed in v1.
4. Tool result goes back to the LLM for a one-sentence spoken reply (or a template reply for fast-path).

System prompt essentials (persona + rules):
```
You are JARVIS, a concise, witty British assistant running on the user's Windows laptop.
Reply in 1–2 short spoken sentences. Never read URLs or IDs aloud.
Use tools for any action. If a request is ambiguous, ask one short question.
Never send an email without the user's explicit confirmation.
```

### 5.6 Text-to-speech ("best sound")
- Kokoro voice `bm_george` or `bm_lewis` (British male) — closest free match to the movie Jarvis. Speed 1.05.
- **Sentence streaming**: split LLM output on sentence boundaries; synthesize sentence 1 while the LLM still generates sentence 2 → first audio in ~300 ms.
- Output 24 kHz → light processing chain: high-pass 80 Hz, gentle compressor, −16 LUFS loudness normalization, *subtle* room reverb (pedalboard library) for the "AI in the room" feel.
- Earcons: soft chime on wake, short tick on success, low tone on error.
- **Barge-in**: while speaking, the wake word stays active; "Hey Jarvis" or "stop" cuts playback (with echo suppression by raising the wake threshold while TTS is playing). Headphones make this perfect; speakers work with the higher threshold.

### 5.7 Latency budget (CPU laptop target)

| Stage | Budget |
|---|---|
| End-of-speech detection | 700 ms |
| STT (small.en int8, 3 s utterance) | 400–700 ms |
| Fast path / LLM first tokens | 50 ms / 600–1200 ms |
| Tool execution | 50–800 ms |
| TTS first audio | 250–400 ms |
| **Total** | **~1.5 s (fast path) to ~3.5 s (LLM)** |

---

## 6. Tools in detail

All tools use one decorator so the LLM schema, validation and risk level come from one place:

```python
@tool(risk="safe", description="Play a video on YouTube by search query")
async def play_youtube(query: str) -> ToolResult: ...
```

### 6.1 Open folders & files — `tools/files.py`
- Known folders map: desktop, documents, downloads, pictures, music, videos, OneDrive → `os.startfile(path)`.
- File search: `es.exe -n 10 -sort date-modified "<query>"` (Everything CLI) → rank with rapidfuzz on filename + recency.
- One clear match → open it. Several → show top 3 on a card and say "I found three — the first, second or third?" (answered in FOLLOW_UP).
- Scope: search only under the user profile and chosen drives (configurable `search_roots`).

### 6.2 Open any application — `tools/apps.py`, `services/app_index.py`
- On startup build an index:
  - `.lnk` files from `%ProgramData%\Microsoft\Windows\Start Menu` and `%AppData%\Microsoft\Windows\Start Menu`.
  - UWP/Store apps: `powershell Get-StartApps` → `Name`, `AppID`.
  - Aliases in config: `"vs code": "Visual Studio Code"`, `"teams": "Microsoft Teams"`.
- Match with `rapidfuzz.process.extractOne(score_cutoff=80)`.
- Launch: `.lnk` → `os.startfile`; UWP → `explorer.exe shell:AppsFolder\<AppID>`.
- Also: "close Spotify" → `psutil` terminate by process name (risk `confirm` if it has unsaved-work risk, e.g. Office apps).

### 6.3 Browser — `tools/browser.py`
- `open_website(url_or_name)`: name → URL map + "go to X.com".
- `web_search(query, engine="google")`.
- `open_browser(name)`: Chrome / Edge / Brave / Firefox via app index; default browser via `webbrowser`.
- Optional v2: Playwright on a dedicated Chrome profile for "read me the headlines on X".

### 6.4 YouTube — `tools/youtube.py`
```python
from yt_dlp import YoutubeDL
with YoutubeDL({"quiet": True, "extract_flat": True}) as ydl:
    info = ydl.extract_info(f"ytsearch1:{query}", download=False)
video_id = info["entries"][0]["id"]
webbrowser.open(f"https://www.youtube.com/watch?v={video_id}&autoplay=1")
```
- Speaks back the title: "Playing *Tum Hi Ho* by Arijit Singh."
- Variants: "play the next one", "pause" (send `k` key to the browser window via `pywinauto`), "full screen" (`f`).

### 6.5 Media controls — `tools/system.py`
- Play/pause/next/previous via Windows virtual media keys (`keyboard` / `win32api` VK_MEDIA_*) — works for YouTube and Spotify alike.
- Volume set/up/down/mute via `pycaw`.

### 6.6 Spotify — `tools/spotify.py`
Important constraint: **Spotify Web API playback control requires Premium.** Plan for both:

- **Free plan (default):**
  1. Search via Web API (search is allowed on Free) using Client-Credentials flow — free developer app at developer.spotify.com → get `spotify:track:<id>`.
  2. `os.startfile("spotify:track:<id>")` opens the track in the desktop app.
  3. Focus the Spotify window (`pywinauto`) and send `Enter`/`Space` to start playback; verify with the window title (Spotify shows "Artist - Song" while playing). Retry once if needed.
  4. Free-plan ads/shuffle limits still apply — that's Spotify's rule, not ours.
- **Premium (auto-detected):** `spotipy` with user OAuth → `start_playback(uris=[...])`, plus queue, "play my liked songs", "play my Discover Weekly".

### 6.7 Email — `tools/gmail.py`, `services/gmail_watcher.py`
**One-time setup** (`scripts/google_auth.py`): Google Cloud Console → new project → enable Gmail API + People API → OAuth client "Desktop app" → download `credentials.json` → consent in browser once. Refresh token stored in Windows Credential Manager via `keyring`. Scopes: `gmail.readonly`, `gmail.compose`, `gmail.send`, `contacts.readonly`.
> Your account is Google Workspace (tmcltd.com). If the admin blocks unverified apps, ask IT to allow the app, or use IMAP with an app password as a fallback.

**New-mail announcements**
- Poll `users.history.list(startHistoryId=…)` every 45 s (cheap; no Pub/Sub needed locally).
- For each new INBOX message: fetch `From`, `Subject`, snippet.
- Filters in config: only `CATEGORY_PRIMARY`, VIP list always announced, quiet hours, "do not disturb" while in a Teams/Zoom call (detect by process + mic in use).
- Overlay shows a small card; Jarvis says: "New email from Ahmed Raza — *Q3 proposal review*." Batches if more than 3 arrive: "You have four new emails, two from Sara."

**Read inbox on demand:** "Who emailed me today?" → `q="in:inbox newer_than:1d"` → summarise senders; "Read the one from Sara" → LLM summary of body in 2 sentences.

**Draft and send (with confirmation)**
1. "Email Sara that the demo moves to Friday 3pm."
2. Resolve "Sara" via People API / `contacts.yaml`; if several Saras → ask.
3. LLM writes subject + body (your signature appended from config).
4. Create a real **Gmail draft** (`drafts.create`) so it's also visible in Gmail.
5. Overlay shows an editable **EmailCard** (To, Subject, Body) + Jarvis reads a short summary.
6. User says "send it" (or clicks Send) → `drafts.send`. "Change the time to 4pm" → redraft. "Cancel" → keep as draft.
7. Never sends without that explicit confirmation — hard rule in the router, not just the prompt.

---

## 7. The overlay window (the "wow" part)

### 7.1 Window behaviour (Electron `main.ts`)
- `BrowserWindow({ transparent: true, frame: false, alwaysOnTop: true, skipTaskbar: true, resizable: false, focusable: false })`, size ~420×420, bottom-center of the primary display.
- Appears on wake: opacity 0→1 + scale 0.6→1 with a spring curve (≈350 ms). Hides 4 s after returning to IDLE, with fade + shrink.
- Click-through when no card is shown (`setIgnoreMouseEvents(true, {forward: true})`); becomes clickable when an email card or choice list appears.
- Tray icon: mute mic, pause announcements, settings, quit.
- Global hotkey `Ctrl+Alt+J` as an alternative to the wake word.

### 7.2 The orb (Three.js)
- High-subdivision icosphere; vertex shader displaces vertices with 3D simplex noise:
  `displacement = noise(position * freq + time) * (0.05 + level * 0.45)`.
- Fragment shader: fresnel rim glow + mix between two state colors + inner gradient.
- `UnrealBloomPass` / `postprocessing` Bloom for the glow; subtle particle ring orbiting the orb.
- Uniforms driven by backend events: `u_level` (RMS, smoothed with attack 30 ms / release 200 ms), `u_bands[32]` (FFT → ripple frequencies), `u_colorA`, `u_colorB`, `u_time`.
- Caption text under the orb: your words live (white), Jarvis's words (state color), typewriter effect synced to TTS.

### 7.3 Colors follow who is speaking

| State | Palette | Motion |
|---|---|---|
| Listening (you) | Cyan `#22D3EE` → Electric blue `#3B82F6` | Spikes with your voice; louder = brighter, bluer → shifts toward white at peaks; pitch (dominant FFT band) shifts hue ±20° |
| Thinking | Violet `#8B5CF6` → Magenta `#D946EF` | Slow swirl, rotating gradient, no level response |
| Acting | Teal `#14B8A6` | Quick pulse + small icon (YouTube / Spotify / mail / folder) |
| Speaking (Jarvis) | Amber `#F59E0B` → Orange `#F97316` (Iron-Man gold) | Pulses with TTS output amplitude & bands |
| Email alert | Soft red `#F43F5E` accent ring | Gentle double pulse |
| Error | Red `#EF4444` | Short shake |

Color transitions are tweened over 250 ms in linear RGB so they blend smoothly instead of snapping.

### 7.4 UI ↔ backend protocol (WebSocket JSON)

```jsonc
{"type":"state","value":"listening"}
{"type":"level","source":"mic","rms":0.42,"bands":[...32 floats]}      // 30–60 fps
{"type":"level","source":"tts","rms":0.31,"bands":[...]}
{"type":"transcript","text":"play blinding lights on spot","final":false}
{"type":"reply","text":"Playing Blinding Lights by The Weeknd."}
{"type":"card","kind":"email_draft","id":"d1","to":"sara@…","subject":"…","body":"…"}
{"type":"card","kind":"choices","id":"c1","items":["Q3 proposal.pdf","Q3 proposal v2.pdf"]}
{"type":"notify","kind":"email","from":"Ahmed Raza","subject":"Q3 proposal review"}
// UI → backend
{"type":"confirm","id":"d1","action":"send"}
{"type":"edit","id":"d1","body":"…"}
{"type":"choose","id":"c1","index":1}
```

---

## 8. Security & privacy

- All audio is processed locally; nothing is recorded to disk unless `debug.save_audio: true`.
- OAuth tokens and Spotify secrets live in Windows Credential Manager (`keyring`), never in the repo. `.gitignore` covers `credentials.json`, `token*`, `.env`.
- Tool allowlist only — no "run any command" tool. File search limited to configured roots.
- `confirm` risk level enforced in code for send-email and closing apps with unsaved work.
- WebSocket bound to `127.0.0.1` with a random per-session token passed to Electron via command line, so other local programs can't drive Jarvis.
- Mic indicator: Windows already shows it; the tray icon also shows listening state.

---

## 9. Build plan — phases & acceptance criteria

| Phase | Work | Done when |
|---|---|---|
| **0. Setup** (½ day) | `setup.ps1`: Python 3.11 venv, Node 20, Ollama + model pull, Kokoro/Piper voices, Everything | `python -m jarvis --selftest` passes: mic, speaker, Ollama, STT, TTS |
| **1. Voice loop** (1–2 days) | mic → wake word → VAD → STT → echo back via TTS | "Hey Jarvis, hello" → hears "You said hello" in < 2 s |
| **2. Brain + first tools** (1–2 days) | Ollama tool calling, fast path, router, apps/files/browser/system tools | 20 scripted commands succeed ≥ 90% |
| **3. Media** (1 day) | YouTube, Spotify (Free + Premium paths), media keys, volume | "Play X on YouTube/Spotify" plays within 4 s |
| **4. Overlay UI** (2–3 days) | Electron window, orb shader, color states, captions, show/hide animation | Orb reacts visibly to your voice and to Jarvis's voice; 60 fps on integrated GPU |
| **5. Email** (2 days) | OAuth, watcher, announcements, inbox Q&A, draft card, confirm-to-send | New mail announced within 60 s; draft never sent without "send it" |
| **6. Polish** (1–2 days) | Sentence-streamed TTS, audio chain, earcons, barge-in, follow-up mode, ducking | Natural back-and-forth without repeating the wake word |
| **7. Hardening** (1 day) | Tests, logs, crash auto-restart, Startup task, PyInstaller + electron-builder | Starts at login, survives sleep/wake and mic unplug |

Total: roughly **10–14 working days** for one developer.

---

## 10. Testing strategy

- **Unit**: router/argument validation, fast-path regexes, app fuzzy matching, contact resolution, email-confirmation guard (assert `send` impossible without confirm token).
- **Audio regression**: `tests/fixtures/audio/*.wav` (your recorded commands) → expected tool call; run after any model/threshold change. Track word-error rate.
- **LLM eval**: 100 command phrasings → expected tool+args; target ≥ 95% on the 7B model.
- **Wake word**: 30 min of normal office/YouTube audio → false-trigger count (target ≤ 1/hour).
- **UI**: a mock backend that replays recorded event streams so the orb can be tuned without speaking.
- **Manual smoke test** checklist before each release (all 10 capabilities).

---

## 11. Install & run (what the finished README will say)

```powershell
git clone <your repo> jarvis; cd jarvis
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1   # installs everything, pulls models
python scripts\google_auth.py                                 # one-time Gmail consent
python -m jarvis                                              # starts backend + overlay
powershell -File scripts\install_startup.ps1                  # optional: start at login
```

Disk: ~6–8 GB (LLM 4.7 GB, Whisper 0.5–1.5 GB, voices 0.3 GB). RAM while idle: ~300 MB (models load on first use and stay warm).

---

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Workspace admin blocks Gmail API app | Ask IT to allow it, or IMAP + app password fallback |
| Spotify Free can't be controlled via API | URI + keystroke method; Premium path auto-enabled when available |
| False wake-ups from videos/meetings | Tunable threshold, cooldown, auto-mute during calls, custom-trained model |
| Slow on CPU-only laptop | Fast path for common commands, 3B model, small.en STT, sentence-streamed TTS |
| Jarvis hears itself through speakers | Raise wake threshold during TTS, prefer headset; v2: WebRTC AEC |
| Wrong recipient for email | Contact disambiguation + mandatory spoken/clicked confirmation |
| Library breaking changes | Pin versions in `pyproject.toml` / `package-lock.json`; self-test on startup |

---

## 13. v2 ideas
- Custom "Jarvis" wake word and a cloned voice of your choice (XTTS/F5-TTS, local).
- Calendar ("What's my next meeting?") via Google Calendar API.
- Screen awareness ("summarise this page") with a local vision model (`qwen2.5-vl`).
- WhatsApp Desktop / Teams messages through UI automation.
- Long-term memory of preferences ("my usual playlist").
