# JARVIS plan: review and v1.1 revisions

This reviews `JARVIS_IMPLEMENTATION_PLAN.md` against the laptop it will actually run on, then lists the changes that went into the build.

## The machine

| | Measured |
|---|---|
| CPU | Intel Core i5‑10310U, 4 cores / 8 threads, 15 W low-power part (2019) |
| RAM | 16 GB, but only **~3 GB free** with Chrome, WSL and Teams open |
| GPU | Intel UHD (integrated). No CUDA |
| Disk | 23 GB free on C: |
| Python | 3.14 (default) and 3.12 |
| Already installed | Node 24, Chrome, Edge, Spotify desktop, Office, Teams, Windows Search (running) |
| Mail | the work mail domain is hosted by Google (Google Workspace) |

## What the plan gets right

- The split (Python brain, web-tech face, local WebSocket) is the correct one.
- openWakeWord + faster-whisper + Kokoro are the best free offline picks today.
- Hard confirmation for sending email is enforced in code, not only in the prompt.
- Tool allowlist, no "run any command" tool, tokens in Windows Credential Manager.
- Spotify Free vs Premium constraint is called out honestly.

## Problems, and what changed

### 1. The latency numbers do not hold on this CPU (biggest issue)
The plan routes most requests through `qwen2.5:7b` and then makes a **second** LLM call to phrase the reply. On a 15 W 4-core CPU a 7B Q4 model runs at about 3–5 tokens/s, with 1–3 s of prompt processing per call. A plain "open Excel" would take 8–15 s, and the 4.7 GB model does not fit in the ~3 GB of free RAM without swapping.

**Change:** a deterministic **intent engine** is now the primary path, not a "fast path" shortcut. It covers all ten capabilities with many phrasings plus fuzzy matching, and answers from templates. The LLM (`qwen2.5:3b`, 1.9 GB) is used only for:
- small talk and general questions (streamed sentence by sentence into TTS),
- writing email bodies,
- classifying a command the intent engine could not parse (one short JSON call, no second "phrase the reply" call).

Most actions are now about 1.5–2.5 s from end of speech to Jarvis answering, and they work even when Ollama is not running.

### 2. STT model is sized for a faster CPU
`small.en` takes about 1.5–2.5 s for a 3 s command on this CPU. Live partial captions (a decode every 600 ms) would compete for the same 4 cores and slow everything down.

**Change:** default to `base.en` int8 (configurable to `small.en`), with an `initial_prompt` built from the real app list. No partial decoding. While you speak, the orb reacts to your voice and the caption reads "Listening…"; the transcript appears as soon as it is final.

### 3. No way to use it silently
In an office or a meeting you often cannot talk to your laptop. The plan has no text input.

**Change:** the overlay has a **type-to-Jarvis** box. `Ctrl+Alt+J` opens it already focused: talk *or* type, whichever comes first.

### 4. A disappearing window hides whether the mic is live
The plan fades the overlay out completely when idle. You then cannot tell if Jarvis is listening, muted or crashed.

**Change:** when idle, the overlay shrinks to a small **docked orb** in the bottom-right corner that breathes slowly. Its colour shows status (dim blue = waiting for "Hey Jarvis", grey = mic muted, red = backend down). Click it to talk. Right-click mutes and unmutes. It expands on wake.

### 5. Too many installs and accounts before anything works
The plan needs Everything, a Spotify developer app, a Google Cloud project, Vite and TypeScript before the first command runs.

**Changes:**
- **Files:** use the built-in **Windows Search index** (already running here) instead of installing Everything, with a folder scan as fallback.
- **Apps:** `Get-StartApps` alone lists all 164 Start-menu apps (Win32 and Store) with launch IDs, so the `.lnk` scan is unnecessary. Launch with `shell:AppsFolder\<AppID>`.
- **Spotify:** no developer account needed. Open `spotify:search:<query>` in the desktop app, then press the top result's *Play* button through Windows UI Automation. Spotify Web API (and Premium playback) remain an optional upgrade.
- **UI build:** plain ES modules + Three.js in Electron. No Vite/TypeScript build step.
- **TTS:** Windows' built-in voice is used automatically until the Kokoro files are present, so Jarvis can speak from the first run.
- **Email contacts:** instead of the People API (another scope and API to enable), "email Sara" is resolved from the people you have actually emailed (Gmail sent headers, `gmail.readonly`) plus `contacts.yaml`.

### 6. Gmail on a Workspace account is the riskiest dependency
An unverified OAuth app on a Workspace domain is often blocked by the admin. The plan's IMAP fallback is also weak, because Google has retired password-based IMAP for most Workspace tenants.

**Change:** email is an optional module. Everything else works without it, and the self-check states clearly whether Gmail is connected. If IT blocks the app, the fix is to ask them to trust the OAuth client ID. Scopes are cut to `gmail.readonly` + `gmail.compose` (compose is enough to create and send drafts).

### 7. Over-engineered audio polish
A pedalboard chain (compressor, LUFS normalisation, reverb) adds a dependency and latency for very little gain over Kokoro's already clean output.

**Change:** dropped. Kept peak normalisation and earcons. The earcons are synthesised in code, so there are no sound files to manage.

### 8. Python and packaging details
- Python 3.14 has no wheels yet for several audio/ML packages. The venv uses **Python 3.12**.
- PyInstaller + electron-builder is a lot of work for a single-user tool. **Start at login** uses a Startup-folder shortcut instead.
- Silero VAD normally pulls in PyTorch (~2 GB). The build uses **WebRTC VAD** (tiny) for end-of-speech detection; faster-whisper's own Silero ONNX filter still trims silence before transcription.

### 9. Smaller UX gaps
- **No feedback in the first 100 ms:** an earcon plus an orb colour change on wake and on end-of-speech, so you know you were heard before the answer is ready.
- **No universal escape:** "stop", "cancel", "never mind" and `Esc` always work and cut speech immediately.
- **Disambiguation:** choice cards can be clicked or answered by voice ("the second one").
- **Meeting etiquette:** email announcements are held while Teams/Zoom is using the mic and released as a batch afterwards.
- **No self-check:** `start_jarvis.bat --selftest` checks mic, speaker, wake word, STT, TTS, Ollama, Gmail and Spotify, and gives a fix hint for each one that fails.

## Revised latency budget (this laptop)

| Path | Example | Expected |
|---|---|---|
| Intent engine | "open Excel", "volume 40" | ~1.5–2 s after you stop talking |
| Intent + network | "play X on YouTube" | ~2.5–4 s (yt-dlp search) |
| LLM chat | "what's 18% of 4,500" | ~3–6 s to the first spoken sentence |
| Email draft | "email Sara that…" | ~6–10 s (LLM writes the body) |

## RAM budget

| Part | RAM |
|---|---|
| Backend idle (wake word, VAD, app index) | ~150 MB |
| Whisper `base.en` int8 | ~200 MB |
| Kokoro int8 | ~300 MB |
| Electron overlay | ~150–200 MB |
| Ollama `qwen2.5:3b`, loaded on demand and unloaded after 10 min idle | ~2.3 GB |

## Build scope delivered in this pass

Phases 0–4 and 6 from the original plan, plus the email module (it needs your one-time Google OAuth consent to switch on). See `README.md` to run it.
