"""End-of-speech detection: WebRTC VAD gated by an adaptive noise floor."""
from __future__ import annotations

import numpy as np
import webrtcvad

from .mic import FRAME, SAMPLE_RATE

SUB = 320  # 20 ms sub-frames for webrtcvad
FRAME_MS = FRAME * 1000 // SAMPLE_RATE


class SileroGate:
    """Silero VAD v6 (via sherpa-onnx): is this 80 ms frame speech? Measured 19 Nov on the user's recordings with
    room noise mixed in: 75% of their speech caught at every noise level (WebRTC VAD: 46-48%), cut off mid-sentence
    in 2 of 25 clips (WebRTC: 11-12), and it always noticed the end of the sentence (WebRTC missed it 10-13 times
    in noise). ~5-10 ms of CPU per frame, so it runs only while Jarvis is listening to you."""

    def __init__(self, model: str, threshold: float = 0.5) -> None:
        import sherpa_onnx

        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model, cfg.silero_vad.threshold = str(model), threshold
        cfg.silero_vad.min_silence_duration, cfg.silero_vad.min_speech_duration = 0.08, 0.05
        cfg.silero_vad.window_size = 512
        cfg.sample_rate, cfg.num_threads = SAMPLE_RATE, 1
        self.v = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)

    def __call__(self, frame: np.ndarray) -> bool:
        self.v.accept_waveform(frame.astype(np.float32) / 32768.0)
        speech = bool(self.v.is_speech_detected())
        while not self.v.empty():   # the finished segments aren't needed: only the live yes/no
            self.v.pop()
        return speech

    def reset(self) -> None:
        self.v.reset()


class Endpointer:
    """Feed 80 ms frames; reports when an utterance has started and ended."""

    def __init__(self, aggressiveness: int = 2, silence_ms: int = 700, max_seconds: float = 12,
                 no_speech_timeout: float = 5, short_silence_ms: int = 0, neural: SileroGate | None = None) -> None:
        self.neural = neural   # Silero decides speech when set; WebRTC VAD + a loudness gate otherwise
        self.vad = webrtcvad.Vad(aggressiveness)
        self.silence_frames = max(1, silence_ms // FRAME_MS)
        # People pause after "Jarvis," or "Send a text to..." before the rest. While only a word or two
        # has been said, wait longer before deciding the sentence is over.
        self.short_silence_frames = max(self.silence_frames, short_silence_ms // FRAME_MS)
        self.short_speech_frames = 1000 // FRAME_MS
        self.max_frames = int(max_seconds * 1000 / FRAME_MS)
        self.no_speech_frames = int(no_speech_timeout * 1000 / FRAME_MS)
        self.noise_floor = 300.0  # int16 RMS; updated while idle
        self.require_me = False   # with a voiceprint: only your voice starts a sentence (see feed)
        self.reset()

    def reset(self, no_speech_timeout: float | None = None, carry: list[np.ndarray] | None = None) -> None:
        if no_speech_timeout is not None:
            self.no_speech_frames = int(no_speech_timeout * 1000 / FRAME_MS)
        self.carry = list(carry or [])  # the start of a sentence heard before a pause; kept in front of it
        self.frames: list[np.ndarray] = []
        self.started = False
        self.speech_frames = 0
        self.silent_run = 0
        self.waited = 0           # frames heard before the sentence started (for the no-speech timeout)
        self.last_speech = False  # whether the last frame sounded like speech (before the voice check)
        self.last_voice = 0       # how many frames in, your last word ended
        self.others = 0           # frames of someone else talking (voiceprint said not you)
        if getattr(self, "neural", None) is not None:
            self.neural.reset()

    def observe_idle(self, frame: np.ndarray) -> None:
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        self.noise_floor = 0.98 * self.noise_floor + 0.02 * min(rms, 3000)

    def vad_votes(self, frame: np.ndarray) -> int:
        """How many of the frame's four 20 ms slices WebRTC VAD calls speech (0-4)."""
        pcm = frame.tobytes()
        return sum(self.vad.is_speech(pcm[i * SUB * 2:(i + 1) * SUB * 2], SAMPLE_RATE) for i in range(FRAME // SUB))

    def _is_speech(self, frame: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        if self.neural is not None:
            said = self.neural(frame)   # fed every frame, so it keeps its sense of what came before
            return said and rms >= 100.0   # never digital silence
        if rms < max(150.0, self.noise_floor * 1.8):
            return False
        pcm = frame.tobytes()
        votes = sum(self.vad.is_speech(pcm[i * SUB * 2:(i + 1) * SUB * 2], SAMPLE_RATE)
                    for i in range(FRAME // SUB))
        return votes >= 2

    def feed(self, frame: np.ndarray, me: bool | None = None) -> str:
        """Returns 'waiting', 'speaking', 'done' (utterance ready) or 'timeout' (nothing said).

        me: the voiceprint's latest verdict (True you, False someone else, None not known yet). Someone else
        talking counts as silence: their voice neither starts your sentence nor keeps it going, so with people
        chatting nearby Jarvis waits for you, and stops listening when you stop. With require_me, a voice not
        yet recognised is held (not thrown away) until the check says it's you."""
        self.frames.append(frame)
        speech = self.last_speech = self._is_speech(frame)
        if speech and me is False:
            speech = False
            self.others += 1
        if speech:
            self.speech_frames += 1
            self.silent_run = 0
            self.last_voice = len(self.frames)   # your last word so far (others' voices never count)
            # ~2 s of a voice the check couldn't place (it never answered): listen rather than ignore you
            if self.speech_frames >= 2 and (self.started or not self.require_me or me is True
                                            or (me is None and self.speech_frames >= 25)):
                self.started = True
        else:
            self.silent_run += 1
            if not self.started and len(self.frames) > 2 and me is not False:
                self.observe_idle(frame)  # the room's noise, between turns of a call
        if not self.started:
            self.waited += 1
            if self.waited >= self.no_speech_frames:
                return "timeout"
            if self.speech_frames and self.silent_run > 8:   # that voice stopped without being you: start over
                self.speech_frames = 0
            # a short pre-roll; while an unrecognised voice is speaking, up to ~3 s of it
            keep = 8 if self.speech_frames == 0 else 40
            if len(self.frames) > keep:
                self.last_voice = max(0, self.last_voice - (len(self.frames) - keep))
                self.frames = self.frames[-keep:]
            return "waiting"
        need = self.short_silence_frames if self.speech_frames < self.short_speech_frames else self.silence_frames
        if self.silent_run >= need or len(self.frames) >= self.max_frames:
            return "done"
        return "speaking"

    def audio(self) -> np.ndarray:
        """The captured utterance as float32 in [-1, 1]."""
        mine = self.frames
        if self.others and self.last_voice:
            # people kept talking after you stopped: leave their words out, or they'd be transcribed as yours
            mine = self.frames[:self.last_voice + 3]
        frames = self.carry + mine
        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames).astype(np.float32) / 32768.0
