# JARVIS on Ubuntu

| | Command |
|---|---|
| **Start** | `./start_jarvis.sh` |
| **Stop completely** | `./stop_jarvis.sh` |
| **Check status** | `./status_jarvis.sh` |

Run them from the project folder. JARVIS never starts by itself: not at boot, not at login, and not after a crash.

The Windows instructions in [README.md](README.md) still apply on Windows. Nothing here changes how JARVIS runs there.

## 1. What you need first

- Ubuntu 24.04 with the normal GNOME desktop. Tested on **"Ubuntu on Xorg"** (X11). See [Wayland](#wayland).
- About 8 GB of free disk space while installing (about 6 GB once installed).
- 4 GB of free RAM while the language model is loaded.
- One system library, which you install yourself. It's the only `sudo` step:

  ```bash
  sudo apt install libportaudio2
  ```

  It's the audio library the microphone code uses: a small shared library from Ubuntu's own archive, and it changes or upgrades nothing else. To remove it later: `sudo apt remove libportaudio2`.

Everything else ships with Ubuntu 24.04 (Python 3.12, PipeWire, GNOME Keyring, `xdg-open`, `gio`, `curl`, `zstd`) or gets installed **inside the project folder** by the setup script.

## 2. Install

```bash
cd JARVIS-Personal-Assistant
scripts/setup_linux.sh
```

The first run downloads about 4 GB and takes 10–30 minutes depending on your connection. It:

1. Checks the prerequisites above.
2. Creates `.venv/` and installs the Python packages into it.
3. Puts Node.js 24 in `.tools/node/`. Your own Node (nvm or another install) is not used or changed.
4. Installs the npm packages for the overlay (`ui/`) and the WhatsApp bridge (`whatsapp-bridge/`).
5. Downloads the speech and voice models into `data/models/`.
6. Puts Ollama in `.tools/ollama/` and downloads the language model (`qwen2.5:3b`, 1.9 GB) into `data/models/ollama/`.
7. Creates `config.local.yaml` (settings for this machine only), makes `data/` and `secrets/` private, and runs the self-test.

Run it again whenever you like. It skips anything already done.

### What it never does

- It doesn't use `sudo`, install anything globally (no `pip install --user`, no `npm -g`), or edit `.bashrc`/`.profile`.
- It doesn't create services, autostart entries, cron jobs or systemd units. Ollama's official installer creates a system service; this script doesn't use that installer.
- It doesn't change firewall, network, sound or desktop settings, or your keyboard shortcuts.
- It doesn't read or write anything outside this folder, except your login keyring (only if you set up Gmail; see [Gmail](#gmail)).

## 3. What gets installed, and where

| What | Where | Why |
|---|---|---|
| Python packages | `.venv/` | Speech recognition (faster-whisper), voices (Piper, Kokoro), wake word (openWakeWord), audio (sounddevice), Gmail, and so on. The list is in `backend/requirements.txt`, plus `backend/requirements-linux.txt`. |
| `pulsectl`, `python-xlib` | `.venv/` | Linux replacements for the Windows-only `pycaw` and `pywin32`: volume, ducking, "is the mic in use", media keys, closing windows. |
| Node.js 24.22.0 | `.tools/node/` | Runs the WhatsApp bridge and installs Electron. Your nvm Node is too old for these. |
| Electron 33, Three.js | `ui/node_modules/` | The orb overlay. |
| Baileys, pino, qrcode | `whatsapp-bridge/node_modules/` | The WhatsApp remote. |
| Ollama 0.40.7 (CPU) | `.tools/ollama/` | Runs the local language model. |
| Language model `qwen2.5:3b` | `data/models/ollama/` | Questions, chat and email writing. |
| Whisper `base.en` and `small.en` | `data/models/whisper/` | Speech to text. |
| Piper `en_GB-alan-medium`, Kokoro | `data/models/piper/`, `data/models/kokoro/` | Jarvis's voice. |
| "Hey Jarvis" model | inside `.venv/` | Wake word. |

Other project-local folders:

| Folder | Contents |
|---|---|
| `data/logs/` | `jarvis.log` (Jarvis) and `ollama.log` |
| `data/cache/` | download caches (pip, npm, Electron, Hugging Face, yt-dlp), instead of `~/.cache` |
| `data/runtime/` | process IDs and temporary files while JARVIS runs. Emptied by `./stop_jarvis.sh`. |
| `data/electron/` | the overlay's browser profile, instead of `~/.config` |
| `data/ollama-home/` | Ollama's own key, instead of `~/.ollama` |
| `data/whatsapp-auth/` | the WhatsApp link (private, never committed) |
| `secrets/` | the Gmail OAuth client file, if you use that route (private, never committed) |

**Reusing models you already have:** if you already keep Ollama models in another folder, start JARVIS with `JARVIS_OLLAMA_MODELS=/path/to/models ./start_jarvis.sh` (and run `scripts/setup_linux.sh` the same way) so nothing is downloaded twice. If an Ollama server is already running on `127.0.0.1:11434`, JARVIS uses that server and never stops it.

## 4. Configure

- `config.yaml` holds the shared settings, the same on Windows and Ubuntu. Leave it as it is, so your changes don't overwrite your friend's.
- `config.local.yaml` is **this laptop only** and is not committed. Anything in it overrides `config.yaml`. The setup script creates it from `config.local.example.yaml` with:
  - `claude_code.exclude_paths: ["/var/www"]`, so your work projects are never read, summarised or sent anywhere. Add more folders if you like.
  - Ubuntu names for apps that config.yaml calls by their Windows names, for example "file explorer" opens Files.
- Put your own changes in `config.local.yaml`, for example:

  ```yaml
  whatsapp_remote:
    enabled: false        # turn the WhatsApp remote off on this laptop
  email:
    enabled: false
  wake_word:
    threshold: 0.3
  ```

- Restart JARVIS after any edit (`./stop_jarvis.sh`, then `./start_jarvis.sh`).

### Calibrate the wake word (optional)

```bash
./start_jarvis.sh --calibrate
```

This writes the new mic gain and wake threshold into `config.yaml`. Move those two values into `config.local.yaml` and don't commit the `config.yaml` change, because your friend's microphone is different.

### Gmail

Gmail works on Ubuntu the same way as on Windows. Credentials go in your **GNOME login keyring** (encrypted, unlocked when you log in) instead of Windows Credential Manager, as the entries `jarvis-mail` or `jarvis-gmail`. They're the only JARVIS data outside the project folder.

- **App password (simplest):** `.venv/bin/python scripts/email_setup.py`, then restart JARVIS. It tells you how to create the app password. (The script says "Windows Credential Manager"; on Ubuntu it's your keyring.)
- **Google Cloud OAuth client:** save the downloaded JSON as `secrets/credentials.json`, run `.venv/bin/python scripts/google_auth.py`, approve in the browser, then restart JARVIS.

### WhatsApp remote

It's installed, but **switched off on this laptop** (`whatsapp_remote.enabled: false` in `config.local.yaml`), so the bridge doesn't start and no QR page opens. To use it:

1. In `config.local.yaml`, set `whatsapp_remote:` → `enabled: true`.
2. Restart JARVIS. A page with a QR code opens in your browser.
3. On your phone: **WhatsApp → Settings → Linked devices → Link a device**, and scan it.

The README explains how it works and the ban risk of Baileys. Linking a personal WhatsApp to a work laptop is your call.

To unlink, or link a different number: run `./stop_jarvis.sh`, delete `data/whatsapp-auth/`, start again and scan the new QR code. To stop using it, set `enabled: false` again.

## 5. Start

```bash
./start_jarvis.sh
```

It runs **in that terminal**, and the terminal shows the log as it goes. Leave the terminal open (minimised is fine).

Use **GNOME Terminal** (Ctrl+Alt+T). It also works from VS Code's terminal: the script drops the variables VS Code adds there (`ELECTRON_RUN_AS_NODE`, and the snap's library paths that make GNOME apps crash). But programs started from snap VS Code's terminal also inherit its AppArmor label, so a plain terminal is cleaner.

It:

1. Checks that everything is installed, and tells you the exact fix if not.
2. Refuses to start a second copy.
3. Starts Ollama if it isn't already running (and remembers that it started it).
4. Starts the backend. The backend opens the overlay and the WhatsApp bridge.

After about 20 seconds Jarvis says "Systems are online" and docks as an orb in the bottom-right corner. Say **"Hey Jarvis"**, or press **Ctrl+Alt+J**.

Other modes, all run in the terminal (Ctrl+C ends them):

| Command | What it does |
|---|---|
| `./start_jarvis.sh --selftest` | checks every component and prints a fix for anything broken |
| `./start_jarvis.sh --calibrate` | tunes "Hey Jarvis" and the mic level to your voice |
| `./start_jarvis.sh --no-ui` | console mode: type commands, no overlay |

## 6. Stop completely

Any of these stops **everything** JARVIS started: overlay, backend, WhatsApp bridge, microphone, and Ollama if JARVIS started it.

- `./stop_jarvis.sh` (from any terminal)
- **Ctrl+C** in the terminal where JARVIS is running
- **Close that terminal**
- **Quit JARVIS** in the tray menu (top bar, right side)

`./stop_jarvis.sh` is safe to run any number of times. When it finishes it confirms that no JARVIS process remains and the microphone is released. GNOME's microphone indicator in the top bar also goes away.

How it knows what is JARVIS's: every process JARVIS starts carries a marker (`JARVIS_ROOT=<this folder>`) in its environment, and only processes with that marker are stopped. It never stops anything by name, like "all node" or "all python". Apps that JARVIS opened for you (Chrome, a file, Files) don't carry the marker, so they keep running.

**Ollama:** if Ollama was already running before JARVIS started, it's left running. If JARVIS started it, it's stopped, unless another program is connected to it at that moment. In that case it's left running and you're told so.

## 7. Check whether it's running

```bash
./status_jarvis.sh
```

It shows the backend, the overlay, the WhatsApp bridge, Ollama (started by JARVIS or not), whether JARVIS is holding the microphone, anything missing, leftover processes, and autostart entries. Autostart should always say "none". The script only reads; it changes nothing.

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `PortAudio missing` | `sudo apt install libportaudio2` |
| "JARVIS is already running" but you see no orb | `./status_jarvis.sh`, then `./stop_jarvis.sh` and start again |
| Orb doesn't appear | Check the terminal for Electron errors. JARVIS needs a desktop session: start it from a terminal on your desktop, not over SSH. |
| Jarvis doesn't hear "Hey Jarvis" | Settings → Sound → Input: check the microphone isn't **muted** (it was muted during setup), pick the right one and check the level. Then `./start_jarvis.sh --calibrate`. |
| No voice | Settings → Sound → Output. Check `./start_jarvis.sh --selftest`. |
| "Brain offline" / questions don't work | `./status_jarvis.sh` (Ollama and the model). See `data/logs/ollama.log`. Re-run `scripts/setup_linux.sh`. |
| Ctrl+Alt+J does nothing | Another app has taken the shortcut. Change `hotkey:` in `config.local.yaml`, e.g. `"Control+Alt+K"`. |
| No WhatsApp QR page | Delete `data/whatsapp-link.html`, or just run `./stop_jarvis.sh` (it removes it), then start again. |
| Gmail "not set up" | See [Gmail](#gmail). The keyring must be unlocked, which it is once you've logged in to the desktop. |
| Something else | `data/logs/jarvis.log`. `./start_jarvis.sh --verbose` logs more. |

## 9. Update

```bash
git pull
scripts/setup_linux.sh --update
```

`--update` reinstalls the pinned Python and npm packages and refreshes the language model. To move to newer versions of Node.js or Ollama, change `NODE_VERSION` / `OLLAMA_VERSION` at the top of `scripts/setup_linux.sh` and run it again. Python and npm versions are pinned in `backend/requirements*.txt` and the `package-lock.json` files, as on Windows.

## 10. Uninstall

```bash
./stop_jarvis.sh
rm -rf .venv .tools data ui/node_modules whatsapp-bridge/node_modules config.local.yaml secrets
```

This removes everything JARVIS installed or stored, including the models, logs, WhatsApp link and settings for this laptop. Then:

- If you set up Gmail, remove the keyring entries: open **Passwords and Keys** (Seahorse) and delete `jarvis-mail` / `jarvis-gmail`, or run:

  ```bash
  .venv/bin/python - <<'EOF'
  import keyring
  for service in ("jarvis-mail", "jarvis-gmail"):
      for key in ["address", "password", "parts"] + [f"token{i}" for i in range(20)]:
          try:
              keyring.delete_password(service, key)
          except keyring.errors.PasswordDeleteError:
              pass
  EOF
  ```

  (Do this before deleting `.venv`.)
- If you linked WhatsApp, remove "Jarvis" under **Linked devices** on your phone.
- To remove PortAudio as well: `sudo apt remove libportaudio2`.
- Then delete the project folder itself, if you want.

## 11. What works on Ubuntu

"Tested" means it was run on this laptop during setup. "Check yourself" means it needs your voice, your accounts, or a click on screen.

| Feature | Status on Ubuntu | Tested |
|---|---|---|
| Start, stop, status; no autostart; no restart after a crash | Works | Tested: Ctrl+C, closed terminal, backend crash, overlay crash, force-killed start script, double start, double stop |
| Orb overlay (Electron) | Works: bottom-right, always on top | Window and connection tested. Check the look yourself. |
| Speech recognition (Whisper) | Works | Tested (self-test) |
| Jarvis's voice (Piper) | Works | Tested ("Systems are online") |
| Wake word "Hey Jarvis" | Works | Model tested. Check your voice yourself; your mic was muted during setup. |
| Questions, chat, email writing (Ollama) | Works | Tested |
| Open apps ("Open Calculator") | Works, from the GNOME app list | Tested |
| Close apps ("Close Calculator") | Works; the app may ask to save | Tested |
| Folders and file search | Works; scans your home folder | Scan tested. Check opening a file yourself. |
| Websites, Google search, "on Chrome/Firefox" | Works | Check yourself |
| YouTube | Works | Check yourself |
| Play/pause/next | Works through GNOME's media keys | Key mapping tested. Check yourself with music playing. |
| Volume, mute | Works | Tested |
| Lowering other apps while you talk | Works | Detection tested. Check during a call yourself. |
| Holding email announcements during calls | Works (Meet, Teams, Zoom using the mic) | Tested |
| Lock the screen | Works | Check yourself (it locks the screen) |
| Time, date, maths | Works | Tested (test suite) |
| Gmail | Works; secrets in GNOME Keyring | Keyring tested. Check your account yourself. |
| WhatsApp remote | Works; **off on this laptop** for now | Bridge start and QR code tested. Linking is up to you. |
| Claude Code project briefings | Works; `/var/www` excluded | Tested |
| Tray menu, Ctrl+Alt+J, clicking the docked orb | Works | Check yourself |
| `--calibrate` | Works | Check yourself |

## 12. What's different or not available on Ubuntu

- **Spotify:** "Play X on Spotify" opens Spotify's search (or Liked Songs) and you press Play. Pressing Play automatically uses Windows UI Automation, which Ubuntu doesn't have. The Spotify app isn't installed on this laptop, so the web player opens. Play/pause/next work either way.
- **Sending WhatsApp messages to contacts from the laptop** ("WhatsApp Ali that…") isn't available. On Windows it drives the WhatsApp desktop app, which doesn't exist for Linux. Jarvis says so if asked. Commanding Jarvis *from* WhatsApp on your phone does work.
- **File search** scans your home folder for up to 4 seconds instead of using the Windows Search index, so files in very large folders can be missed. Add folders to `files.search_roots` in `config.local.yaml` if needed.
- **No built-in fallback voice:** Windows can fall back to its own voice. On Ubuntu, Piper is required (setup installs it).
- **Tray icon:** left-clicking does nothing on Ubuntu. Use its menu.
- **CPU priority:** on Windows, Jarvis raises its own priority. Ubuntu needs admin rights for that, so it runs at normal priority.
- The local AI's instructions still say "Windows laptop". They were deliberately left unchanged, and it doesn't affect what Jarvis does.

### Wayland

Ubuntu offers "Ubuntu" (Wayland) and "Ubuntu on Xorg" at the login screen (gear icon). This setup was tested on Xorg. On Wayland, apps can't position their own windows, grab global shortcuts or close other apps' windows, so the orb's position, Ctrl+Alt+J, "close X" and the media keys would work poorly or not at all. `start_jarvis.sh` warns you if you're on Wayland.

## For contributors

- Ubuntu-only code is in `backend/jarvis/linux.py`. The Windows modules call it only when not on Windows (`config.WINDOWS`), so the Windows code paths are unchanged.
- The Ubuntu scripts are `start_jarvis.sh`, `stop_jarvis.sh`, `status_jarvis.sh`, `scripts/setup_linux.sh` and `scripts/linux_common.sh`.
- Windows-only Python packages carry `; sys_platform == "win32"` in `backend/requirements.txt`, so Windows installs exactly the same set as before.
- Never commit `config.local.yaml`, `data/`, `secrets/`, `.tools/` or `.venv/`. `.gitignore` already excludes them.
