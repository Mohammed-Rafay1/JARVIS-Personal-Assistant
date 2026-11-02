# Jarvis QA review (2 Nov 2026)

This is a senior engineer and QA pass over the current build. Every finding below is backed by `data/logs/jarvis.log`: 368 recognitions, 103 re-checks and 61 "interrupted while thinking" events over several days of real use. All fixes are on the git branch **`ux-hardening`**, one commit per fix, so any fix can be undone on its own.

## What you reported, and why it happened

### "Sometimes it keeps thinking"

| Cause | Evidence in the log | Fix |
|---|---|---|
| The local AI had no time limit. One request waited 57 s and then failed with a timeout. | `'Yeah, this.'` at 18:57:15, then `httpx.ReadTimeout` at 18:58:12 | The answer must start within 20 s (45 s while the model loads), or Jarvis apologises and listens again. "Just a moment, sir." plays after 4 s of silence. |
| Mumbled fragments were sent to the AI. | `'Yeah, this.'`, `'This guy.'` | If the request is 3 words or fewer, the recognizer is unsure, and it isn't a command, Jarvis says "Sorry, sir, I didn't catch that." |
| Speech recognition queued behind abandoned work. When you interrupted, the old recognition kept running and the new one waited for it. | `STT 13.00s (base.en): 'Jarvis,'` (normally about 1.3 s); STT times of 15–20 s | One recognition runs at a time. Abandoned requests skip the slow second pass. The idle "Hey Jarvis" check gives way to a real request. |
| The slow second-opinion recognizer ran on everything uncertain, including long clips (8–20 s). | 128 of 368 recognitions took over 4 s | Clear commands and clips over 6 s skip it. It stays on for song names, messages, emails and yes/no confirmations, because the log shows it improved the text in 82 of 103 cases. |
| "Davis, play…" (Whisper's spelling of *Jarvis* for your accent) didn't match any command, so it went to the slow AI. | `'davis play dilruba song on youtube'` | Misheard wake names are dropped when a command follows. "Davis is late" is still left alone. |

### "Sometimes it didn't identify my voice"

| Cause | Evidence | Fix |
|---|---|---|
| A 600 ms pause ended your sentence. "Jarvis, … please send a text" became two turns. | `'Jarvis, Please Send Text.'` then `'On WhatsApp.'` | While under 1 s has been said, Jarvis waits 1 s of silence before deciding you're done. |
| Speaking again while Jarvis was still recognising threw away the first half of the sentence. | the same sequence | New speech during recognition now continues the same sentence, so the whole thing is heard together. |
| Background voices and videos interrupted Jarvis. 61 interruptions, many of them other people. | burst at 13:13–13:16 (floor 433, ten interruptions) | An interruption now needs about 0.4 s of louder, close speech (`call.rethink_min_rms`). |
| Everyday WhatsApp phrasings weren't understood. | `find mustafa and send him a text hi` started a **file search** | A general texting parser covers send, write, text and message, with or without "on WhatsApp". |
| You got no sign you weren't understood. Jarvis just went back to listening. | 71 empty recognitions | The call screen says "Sorry, I didn't catch that." |

### Other defects found

- The microphone dropped the newest audio frame when its queue was full.
- The wake-word model kept stale audio from the previous call.
- `--selftest` reported Gmail as FAILED even though it works, because it only checked the Google Cloud route.
- During this work, `--selftest` caught a crash I had introduced in speech recognition (a shadowed import). It is fixed, and a test now covers it.

Results: all 11 self-test checks pass, and the test suite went from 120 to 152 tests, all passing.

## Commits (newest last) and how to revert

| Commit | Change | Setting to switch it off without code |
|---|---|---|
| `c91d067` | Drop misheard wake names before a command | — |
| `48adf29` | Longer pause allowed after a short start | `listening.short_silence_ms: 600` |
| `9286344` | Faster STT, sentence continuation, stricter interrupt | `stt.retry_max_seconds: 999`, `call.rethink_min_rms: 1200` |
| `5ef3518` | Time limits on the AI, spoken filler, "didn't catch that" | `llm.first_reply_seconds: 60`, `llm.filler_after_seconds: 999` |
| `e661c34` | Everyday WhatsApp phrasings | — |
| `7336a2c` | Mic, wake-word and screen-hint robustness | — |
| `7013c52` | Fix STT start-up crash; fix the Gmail self-check | — (needed by `9286344`) |

To undo one change: `git revert <commit>`. If you revert `9286344`, revert `7013c52` with it. To drop everything: `git checkout main`. Nothing has been pushed or merged.

## Remaining weak points (not changed: these need your input or more time)

1. **The wake word barely fires for your voice.** Scores are 0.15–0.22 against a model built for about 0.5. That's why you often use the hotkey (61 hotkey wakes vs 53 voice wakes), and why the Whisper backup does a lot of the work. **Best fix:** openWakeWord's *custom verifier*, a small model trained on about 10 recordings of you saying "Hey Jarvis" plus some of your normal speech. It makes the detector sensitive to your voice and deaf to others. This needs a 3-minute recording session added to `calibrate_wake.bat`.
2. **Background speech still reaches the recognizer.** Urdu conversation, videos and Teams calls produce nonsense transcripts. Options, cheapest first: a headset or directional mic; raise `vad_aggressiveness` to 3; speaker verification (accept only your voice, about 50 MB model, about 0.2 s per turn).
3. **Accent accuracy.** The accurate model wins 80% of uncertain turns. It's worth benchmarking `distil-small.en` as the *only* model on your own clips. It could give small.en accuracy at nearly base.en speed.
4. **The local AI is slow on this CPU.** It takes 14–27 s to load and 3–6 s per answer. Options: `keep_alive: 2h` if RAM allows; `qwen2.5:1.5b` for small talk (about 2× faster, weaker answers).
5. **The conversation state machine has no end-to-end tests.** `app.py` is about 1,250 lines with 15 follow-up states (who?, what?, send it?…), and each is tested only by talking to it. Next step: a fake mic/LLM harness that plays scripted turns through `handle_text` and asserts the replies.
6. **Per-turn latency isn't logged as one line.** A `turn: stt 1.2s, intent 0.0s, tool 2.1s, speech 0.8s` line would make "it feels slow" reports provable at a glance.

## Can I control it from my phone? (research only, nothing built)

**Yes, and three routes are realistic.** In every route the laptop must be on, awake and logged in. The Spotify and WhatsApp automations click on screen, so **they fail while Windows is locked**. Email, project status, volume, maths and opening sites work anyway.

| Route | How it works | Effort | Security |
|---|---|---|---|
| **A. Telegram bot** (recommended first) | You message a private bot by text or voice note. Jarvis on the laptop polls Telegram *outbound*, runs the command through the same intent engine, and replies with text or a voice note. Confirm and Cancel buttons replace "send it". | about half a day | No open ports; long polling works behind any router. The bot must accept **only your chat ID**, and sending email or WhatsApp keeps its confirmation step. Anyone holding the bot token can talk to it, so the token is stored in Windows Credential Manager like the mail password. |
| **B. Private web page over Tailscale** | Jarvis serves a small phone page (push-to-talk mic, comms log, the same cards) on your private Tailscale network only. Tailscale's free Personal plan now covers six users with unlimited devices. | about 1 day | Reachable only from your own devices; the existing token check is kept. Nothing is exposed to the internet. |
| **C. Claude Code Remote Control** (exists today) | For coding work only: run `claude remote-control` (or `/rc` in a session) on the laptop and continue that Claude Code session from the Claude phone app. It controls Claude Code, not Jarvis. | 0 (built in) | Outbound HTTPS only; it currently needs a Max plan, with Pro planned. |

**Not recommended:**
- Port-forwarding the Jarvis WebSocket to the internet. It would be the laptop's control surface, exposed to anyone.
- Driving Jarvis through your own WhatsApp chat. It's fragile UI automation and against WhatsApp's terms.

Sources:
- [Tailscale free tier change (2026)](https://thisisanitsupportgroup.beehiiv.com/p/tailscale-s-free-tier-just-doubled-if-you-ve-been-putting-it-off-today-s-the-morning)
- [Tailscale free plan limits](https://www.ssdnodes.com/learn/is-tailscale-free-plan-limits)
- [Telegram long polling (aiogram docs)](https://docs.aiogram.dev/en/latest/dispatcher/long_polling.html)
- [Claude Code Remote Control](https://www.helpnetsecurity.com/?p=358918)
- [Remote Control details](https://www.dsebastien.net/claude-code-remote-control/)
