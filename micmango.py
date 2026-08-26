#!/usr/bin/env python3
"""Local macOS push-to-talk dictation powered by MLX Whisper."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly

from history_store import HistoryStore


APP_DIR = Path(__file__).resolve().parent
MODEL = "mlx-community/whisper-large-v3-turbo"
INSTANCE_LOCK_FILE = Path("/tmp/micmango.lock")
DEFAULT_CONFIG_FILE = APP_DIR / "local-only" / "config.json"
RIGHT_OPTION_KEYCODE = 61
V_KEYCODE = 9
VALID_LANGUAGES = {"auto", "zh", "en"}
DEFAULT_PROMPT = (
    "这是一段简体中文与英文混合的语音输入。今天先整理需求，然后修改代码，"
    "最后运行测试。Claude Code, Python API, git push, GitHub, yt-dlp, demo."
)
ENGLISH_PROMPT = (
    "This is clear English dictation with natural punctuation. Claude Code, "
    "Python API, git push, GitHub, yt-dlp, demo."
)
_T2S_CONVERTER: Any | None = None


@dataclass
class Config:
    model: str = MODEL
    default_language: str = "auto"
    audio_device: int | str | None = None
    sample_rate: int = 16_000
    minimum_recording_seconds: float = 0.3
    maximum_recording_seconds: float = 120.0
    minimum_rms_dbfs: float = -55.0
    minimum_peak_dbfs: float = -35.0
    restore_clipboard: bool = True
    clipboard_restore_delay_seconds: float = 0.35
    simplify_chinese: bool = True
    print_transcript_in_log: bool = False
    record_history: bool = True
    pause_punctuation_seconds: float = 1.0
    prompts: dict[str, str] = field(
        default_factory=lambda: {
            "auto": DEFAULT_PROMPT,
            "zh": DEFAULT_PROMPT,
            "en": ENGLISH_PROMPT,
        }
    )
    common_replacements: dict[str, str] = field(
        default_factory=lambda: {
            "Cloud Code": "Claude Code",
            "Github": "GitHub",
            "YTDLP": "yt-dlp",
            "YT-DLP": "yt-dlp",
        }
    )
    chinese_replacements: dict[str, str] = field(
        default_factory=lambda: {
            "克劳德 Code": "Claude Code",
            "克劳德代码": "Claude Code",
            "吉特哈布": "GitHub",
        }
    )

    @classmethod
    def load(cls, path: Path) -> "Config":
        config = cls()
        if not path.exists():
            return config
        raw = json.loads(path.read_text(encoding="utf-8"))
        for key, value in raw.items():
            if not hasattr(config, key):
                raise ValueError(f"Unknown config key: {key}")
            setattr(config, key, value)
        config.validate()
        return config

    def validate(self) -> None:
        if self.default_language not in VALID_LANGUAGES:
            raise ValueError(
                f"default_language must be one of {sorted(VALID_LANGUAGES)}"
            )
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.minimum_recording_seconds < 0:
            raise ValueError("minimum_recording_seconds cannot be negative")
        if self.maximum_recording_seconds <= self.minimum_recording_seconds:
            raise ValueError(
                "maximum_recording_seconds must exceed minimum_recording_seconds"
            )
        if self.pause_punctuation_seconds < 0:
            raise ValueError("pause_punctuation_seconds cannot be negative")


def replace_term(text: str, source: str, target: str) -> str:
    if not source:
        return text
    if source[0].isascii() and source[0].isalnum() and source[-1].isalnum():
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(source)}(?![A-Za-z0-9_])"
        return re.sub(pattern, target, text, flags=re.IGNORECASE)
    return text.replace(source, target)


def clean_transcript(text: str, config: Config, language: str) -> str:
    cleaned = " ".join(text.strip().split())
    if config.simplify_chinese:
        global _T2S_CONVERTER
        if _T2S_CONVERTER is None:
            from opencc import OpenCC

            _T2S_CONVERTER = OpenCC("t2s")
        cleaned = _T2S_CONVERTER.convert(cleaned)
    replacements = dict(config.common_replacements)
    if language == "zh":
        replacements.update(config.chinese_replacements)
    for source in sorted(replacements, key=len, reverse=True):
        cleaned = replace_term(cleaned, source, replacements[source])

    # Add readable spacing only at Chinese/ASCII word boundaries.
    cleaned = re.sub(r"([\u3400-\u9fff])([A-Za-z0-9])", r"\1 \2", cleaned)
    cleaned = re.sub(r"([A-Za-z0-9])([\u3400-\u9fff])", r"\1 \2", cleaned)
    cleaned = re.sub(r"\s+([,，。.!！？?;；:：])", r"\1", cleaned)
    cleaned = re.sub(r"([,;:])(?=[^\s])", r"\1 ", cleaned)
    return cleaned.strip()


def resample_audio(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    divisor = math.gcd(source_rate, target_rate)
    converted = resample_poly(
        audio, target_rate // divisor, source_rate // divisor
    )
    return np.asarray(converted, dtype=np.float32)


def detect_pause_positions(
    audio: np.ndarray,
    sample_rate: int,
    minimum_pause_seconds: float,
) -> list[float]:
    """Locate meaningful mid-utterance silences as fractions of total duration."""
    if minimum_pause_seconds <= 0 or len(audio) < sample_rate:
        return []
    frame_size = max(1, round(sample_rate * 0.02))
    hop_size = max(1, round(sample_rate * 0.01))
    if len(audio) <= frame_size:
        return []

    squared = np.square(audio.astype(np.float64, copy=False))
    cumulative = np.concatenate(([0.0], np.cumsum(squared)))
    frame_power = (cumulative[frame_size:] - cumulative[:-frame_size])[::hop_size]
    frame_rms = np.sqrt(frame_power / frame_size)
    speech_reference = float(np.percentile(frame_rms, 90))
    if speech_reference < 1e-4:
        return []
    silence_threshold = max(10 ** (-48 / 20), speech_reference * 0.18)
    silent = frame_rms < silence_threshold
    minimum_frames = max(1, round(minimum_pause_seconds * sample_rate / hop_size))

    positions: list[float] = []
    run_start: int | None = None
    for index, is_silent in enumerate(np.append(silent, False)):
        if is_silent and run_start is None:
            run_start = index
        elif not is_silent and run_start is not None:
            run_end = index
            if (
                run_end - run_start >= minimum_frames
                and run_start * hop_size > sample_rate * 0.2
                and run_end * hop_size < len(audio) - sample_rate * 0.2
            ):
                midpoint = ((run_start + run_end) / 2) * hop_size
                positions.append(float(midpoint / len(audio)))
            run_start = None
    return positions


def add_pause_punctuation(text: str, pause_positions: list[float]) -> str:
    """Insert commas near acoustic pauses without splitting ASCII terms."""
    if not text or not pause_positions:
        return text
    punctuation = set("，。！？；：,.!?;:")
    original_length = len(text)
    insertions: list[int] = []
    for position in pause_positions:
        target = round(original_length * position)
        radius = max(4, round(original_length * 0.12))
        candidates: list[int] = []
        for index in range(max(2, target - radius), min(original_length - 1, target + radius) + 1):
            left, right = text[index - 1], text[index]
            if left in punctuation or right in punctuation:
                continue
            if any(char in punctuation for char in text[max(0, index - 3): index + 3]):
                continue
            if left.isascii() and right.isascii() and (
                left.isalnum() or left in "-_"
            ) and (right.isalnum() or right in "-_"):
                continue
            candidates.append(index)
        if candidates:
            chosen = min(candidates, key=lambda index: abs(index - target))
            if all(abs(chosen - existing) > 3 for existing in insertions):
                insertions.append(chosen)

    contains_chinese = bool(re.search(r"[\u3400-\u9fff]", text))
    for index in sorted(insertions, reverse=True):
        left = text[:index].rstrip()
        right = text[index:].lstrip()
        separator = "，" if contains_chinese else ", "
        text = left + separator + right
    return text


def ensure_microphone_permission() -> None:
    import AVFoundation as AV

    status = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeAudio)
    if status == AV.AVAuthorizationStatusAuthorized:
        return
    if status in (AV.AVAuthorizationStatusDenied, AV.AVAuthorizationStatusRestricted):
        raise PermissionError(
            "macOS 未授权麦克风。请在系统设置 → 隐私与安全性 → 麦克风中"
            "允许当前终端应用，然后完全退出并重新打开它。"
        )

    completed = threading.Event()
    granted = False

    def permission_result(allowed: bool) -> None:
        nonlocal granted
        granted = bool(allowed)
        completed.set()

    AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        AV.AVMediaTypeAudio, permission_result
    )
    if not completed.wait(timeout=30) or not granted:
        raise PermissionError("未获得 macOS 麦克风权限。")


def macbook_lid_is_closed() -> bool:
    """Return the current clamshell state when macOS exposes it."""
    try:
        result = subprocess.run(
            ["/usr/sbin/ioreg", "-r", "-k", "AppleClamshellState", "-d", "4"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(
        re.search(r'"AppleClamshellState"\s*=\s*(?:Yes|true)', result.stdout)
    )


def is_builtin_macbook_microphone(device_name: str) -> bool:
    normalized = device_name.casefold()
    return "macbook" in normalized and "microphone" in normalized


def acquire_instance_lock() -> Any:
    """Prevent two launchers from registering the same global hotkey."""
    lock_file = INSTANCE_LOCK_FILE.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        raise RuntimeError(
            "MicMango 已经在运行。请先在原启动窗口中停止它。"
        ) from None
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


class AudioRecorder:
    def __init__(self, config: Config):
        self.config = config
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self.capture_sample_rate = config.sample_rate

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        timing: Any,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            print(f"[audio] {status}", file=sys.stderr, flush=True)
        with self._lock:
            self._chunks.append(indata[:, 0].copy())

    def start(self) -> None:
        with self._lock:
            self._chunks = []
        device_info = sd.query_devices(self.config.audio_device, "input")
        self.capture_sample_rate = int(round(device_info["default_samplerate"]))
        self._stream = sd.InputStream(
            samplerate=self.capture_sample_rate,
            channels=1,
            dtype="float32",
            device=self.config.audio_device,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.empty(0, dtype=np.float32)
        audio = np.concatenate(chunks).astype(np.float32, copy=False)
        return resample_audio(
            audio, self.capture_sample_rate, self.config.sample_rate
        )

    def abort(self) -> None:
        try:
            self.stop()
        except Exception:
            pass


class ClipboardPaster:
    def __init__(self, restore: bool, restore_delay: float):
        self.restore = restore
        self.restore_delay = restore_delay

    @staticmethod
    def _read_text() -> str | None:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, check=False
        )
        return result.stdout if result.returncode == 0 else None

    @staticmethod
    def _write_text(text: str) -> None:
        subprocess.run(["pbcopy"], input=text, text=True, check=True)

    @staticmethod
    def _send_paste() -> None:
        import Quartz

        down = Quartz.CGEventCreateKeyboardEvent(None, V_KEYCODE, True)
        up = Quartz.CGEventCreateKeyboardEvent(None, V_KEYCODE, False)
        Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

    def paste(self, text: str) -> None:
        previous = self._read_text() if self.restore else None
        self._write_text(text)
        self._send_paste()
        if self.restore and previous is not None:
            time.sleep(self.restore_delay)
            self._write_text(previous)


class Transcriber:
    def __init__(self, config: Config):
        self.config = config

    def load(self) -> None:
        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder

        # Model loading and inference stay on this worker thread so MLX can safely
        # keep compiled Metal kernels and reuse them between recordings.
        ModelHolder.get_model(self.config.model, mx.float16)

    def transcribe(
        self, audio: str | Path | np.ndarray, language: str
    ) -> tuple[str, str]:
        import mlx_whisper

        language_argument = None if language == "auto" else language
        result = mlx_whisper.transcribe(
            str(audio) if isinstance(audio, Path) else audio,
            path_or_hf_repo=self.config.model,
            language=language_argument,
            initial_prompt=self.config.prompts.get(language),
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            verbose=None,
        )
        detected = str(result.get("language") or language)
        return str(result.get("text", "")).strip(), detected


class VoiceInputApp:
    def __init__(self, config: Config):
        self.config = config
        self.recorder = AudioRecorder(config)
        self.transcriber = Transcriber(config)
        self.paster = ClipboardPaster(
            config.restore_clipboard, config.clipboard_restore_delay_seconds
        )
        self.history_store: HistoryStore | None = None
        self._state = "loading"
        self._state_lock = threading.Lock()
        self._active_language = config.default_language
        self._record_started_at = 0.0
        self._maximum_timer: threading.Timer | None = None
        self._transcription_jobs: queue.Queue[tuple[np.ndarray, str] | None] = (
            queue.Queue()
        )
        self._worker_ready = threading.Event()
        self._worker_error: Exception | None = None
        self._worker = threading.Thread(
            target=self._transcription_worker,
            name="micmango-model-worker",
            daemon=True,
        )

    def prepare(self) -> None:
        print(f"正在加载模型：{self.config.model}", flush=True)
        # MLX Metal streams are thread-local. The persistent worker must both load
        # the model and run every inference request on the same thread.
        self._worker.start()
        self._worker_ready.wait()
        if self._worker_error is not None:
            raise self._worker_error
        with self._state_lock:
            self._state = "idle"
        print("模型已就绪。按住右 Option 说话，松开后自动粘贴。", flush=True)
        print("按住 Shift + 右 Option 可临时使用 English 模式。", flush=True)

    @staticmethod
    def _beep() -> None:
        subprocess.Popen(
            ["/usr/bin/afplay", "/System/Library/Sounds/Tink.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def start_recording(self, language: str) -> None:
        with self._state_lock:
            if self._state != "idle":
                return
            self._state = "starting"
            self._active_language = language
        try:
            self._beep()
            self.recorder.start()
            self._record_started_at = time.monotonic()
            with self._state_lock:
                self._state = "recording"
            self._maximum_timer = threading.Timer(
                self.config.maximum_recording_seconds, self.stop_recording
            )
            self._maximum_timer.daemon = True
            self._maximum_timer.start()
            print(f"● 录音中 [{language}]", flush=True)
        except Exception as exc:
            with self._state_lock:
                self._state = "idle"
            print(f"无法开始录音：{exc}", file=sys.stderr, flush=True)

    def stop_recording(self) -> None:
        with self._state_lock:
            if self._state != "recording":
                return
            self._state = "processing"
        if self._maximum_timer is not None:
            self._maximum_timer.cancel()
            self._maximum_timer = None
        audio = self.recorder.stop()
        duration = len(audio) / self.config.sample_rate
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
        peak_db = 20 * np.log10(max(peak, 1e-12))
        rms_db = 20 * np.log10(max(rms, 1e-12))
        self._beep()
        print(f"■ 录音结束（{duration:.2f}s）", flush=True)
        print(
            f"  音量 rms={rms_db:.1f} dBFS, peak={peak_db:.1f} dBFS",
            flush=True,
        )
        if duration < self.config.minimum_recording_seconds:
            print("已忽略过短录音。", flush=True)
            with self._state_lock:
                self._state = "idle"
            return
        if (
            rms_db < self.config.minimum_rms_dbfs
            or peak_db < self.config.minimum_peak_dbfs
        ):
            if audio.size and not np.any(audio):
                print(
                    "录音数据全部为 0，已跳过转写。若选择的是 MacBook 内置麦克风，"
                    "请打开 MacBook 屏幕；Apple Silicon Mac 合盖时会在硬件层断开"
                    "内置麦克风。合盖使用时请改选外接麦克风。",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                print(
                    "录音音量过低，已跳过转写以避免静音幻觉。请检查麦克风、"
                    "输入音量和权限。",
                    file=sys.stderr,
                    flush=True,
                )
            with self._state_lock:
                self._state = "idle"
            return
        self._transcription_jobs.put((audio, self._active_language))

    def _transcription_worker(self) -> None:
        try:
            self.transcriber.load()
        except Exception as exc:
            self._worker_error = exc
            self._worker_ready.set()
            return
        self._worker_ready.set()
        while True:
            job = self._transcription_jobs.get()
            if job is None:
                return
            audio, language = job
            self._finish_transcription(audio, language)

    def _finish_transcription(self, audio: np.ndarray, language: str) -> None:
        started = time.perf_counter()
        try:
            raw, detected = self.transcriber.transcribe(audio, language)
            # Auto mode intentionally avoids Chinese-only phonetic replacements.
            text = clean_transcript(raw, self.config, language)
            pauses = detect_pause_positions(
                audio,
                self.config.sample_rate,
                self.config.pause_punctuation_seconds,
            )
            text = add_pause_punctuation(text, pauses)
            elapsed = time.perf_counter() - started
            if not text:
                print(f"未识别到文字（{elapsed:.2f}s）。", flush=True)
                return
            self.paster.paste(text)
            if self.config.record_history:
                if self.history_store is None:
                    self.history_store = HistoryStore()
                self.history_store.add(text, detected, elapsed)
            if self.config.print_transcript_in_log:
                print(f"✓ [{detected}, {elapsed:.2f}s] {text}", flush=True)
            else:
                print(
                    f"✓ [{detected}, {elapsed:.2f}s] 已粘贴 {len(text)} 个字符",
                    flush=True,
                )
        except Exception as exc:
            print(f"转写失败：{exc}", file=sys.stderr, flush=True)
        finally:
            with self._state_lock:
                self._state = "idle"

    def shutdown(self) -> None:
        if self._maximum_timer is not None:
            self._maximum_timer.cancel()
        self.recorder.abort()
        if self._worker.is_alive():
            self._transcription_jobs.put(None)
            self._worker.join(timeout=2.0)


class RightOptionListener:
    def __init__(self, app: VoiceInputApp, debug_events: bool = False):
        self.app = app
        self.debug_events = debug_events
        self._pressed = False
        self._tap = None
        self._source = None

    def _callback(self, proxy: Any, event_type: int, event: Any, refcon: Any) -> Any:
        import Quartz

        if event_type in (
            Quartz.kCGEventTapDisabledByTimeout,
            Quartz.kCGEventTapDisabledByUserInput,
        ):
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
            return event

        keycode = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode
        )
        if self.debug_events:
            print(
                f"[key event] type={event_type} keycode={keycode} "
                f"flags=0x{int(Quartz.CGEventGetFlags(event)):x}",
                flush=True,
            )
        if keycode != RIGHT_OPTION_KEYCODE:
            return event

        flags = Quartz.CGEventGetFlags(event)
        is_down = bool(flags & Quartz.kCGEventFlagMaskAlternate)
        if is_down and not self._pressed:
            self._pressed = True
            language = (
                "en"
                if flags & Quartz.kCGEventFlagMaskShift
                else self.app.config.default_language
            )
            self.app.start_recording(language)
        elif not is_down and self._pressed:
            self._pressed = False
            self.app.stop_recording()
        return event

    def run(self) -> None:
        import Quartz

        mask = 1 << Quartz.kCGEventFlagsChanged
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._callback,
            None,
        )
        if self._tap is None:
            raise RuntimeError(
                "无法监听全局快捷键。请在系统设置 → 隐私与安全性中为 Terminal "
                "开启辅助功能和输入监控，然后重新运行。"
            )
        self._source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(
            run_loop, self._source, Quartz.kCFRunLoopCommonModes
        )

        stop_requested = threading.Event()

        def stop(_signum: int, _frame: Any) -> None:
            stop_requested.set()

        signal.signal(signal.SIGTERM, stop)
        Quartz.CGEventTapEnable(self._tap, True)
        try:
            while not stop_requested.is_set():
                Quartz.CFRunLoopRunInMode(
                    Quartz.kCFRunLoopDefaultMode, 0.25, False
                )
        except KeyboardInterrupt:
            pass
        finally:
            self.app.shutdown()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_FILE, help="JSON config path"
    )
    parser.add_argument(
        "--language",
        choices=sorted(VALID_LANGUAGES),
        help="Override default language mode",
    )
    parser.add_argument("--device", help="Override audio device index or name")
    parser.add_argument(
        "--list-devices", action="store_true", help="Print audio devices and exit"
    )
    parser.add_argument(
        "--check", action="store_true", help="Validate config and dependencies, then exit"
    )
    parser.add_argument(
        "--transcribe",
        type=Path,
        metavar="AUDIO",
        help="Transcribe one audio file through the MicMango pipeline and exit",
    )
    parser.add_argument(
        "--debug-events",
        action="store_true",
        help="Print modifier-key events for global hotkey diagnostics",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list_devices:
        print(sd.query_devices())
        print(f"default: {sd.default.device}")
        return 0

    try:
        config = Config.load(args.config)
        if args.language:
            config.default_language = args.language
        if args.device is not None:
            config.audio_device = int(args.device) if args.device.isdigit() else args.device
        config.validate()
        if not args.transcribe:
            ensure_microphone_permission()
            input_info = sd.query_devices(config.audio_device, "input")
            input_name = str(input_info["name"])
            if (
                is_builtin_macbook_microphone(input_name)
                and macbook_lid_is_closed()
            ):
                raise RuntimeError(
                    f"当前 MacBook 已合盖，内置麦克风 {input_name!r} 会被硬件断开。"
                    "请使用 --list-devices 查看设备，并用 --device 选择外接麦克风。"
                )
            capture_rate = int(round(input_info["default_samplerate"]))
            sd.check_input_settings(
                device=config.audio_device,
                channels=1,
                dtype="float32",
                samplerate=capture_rate,
            )
            import Quartz

            listen_allowed = Quartz.CGPreflightListenEventAccess()
            post_allowed = Quartz.CGPreflightPostEventAccess()
            if not listen_allowed:
                print(
                    "提示：macOS 输入监控预检查未通过，将继续尝试创建快捷键监听器。"
                    "若右 Option 无响应，请为启动 MicMango 的终端应用开启输入监控。",
                    file=sys.stderr,
                )
            if not post_allowed:
                print(
                    "警告：macOS 尚未允许模拟键盘操作，转写可以运行，"
                    "但自动粘贴可能失败。请开启辅助功能权限。",
                    file=sys.stderr,
                )
    except Exception as exc:
        print(f"启动检查失败：{exc}", file=sys.stderr)
        if sd.default.device[0] == -1:
            print(
                "当前进程没有可用的默认麦克风。请运行 --list-devices，"
                "然后用 --device 指定输入设备。",
                file=sys.stderr,
            )
        return 2

    if args.check:
        print("配置、音频设备和 macOS 依赖检查通过。")
        return 0

    if args.transcribe:
        if not args.transcribe.is_file():
            print(f"音频文件不存在：{args.transcribe}", file=sys.stderr)
            return 2
        transcriber = Transcriber(config)
        print(f"正在加载模型：{config.model}", flush=True)
        transcriber.load()
        started = time.perf_counter()
        raw, detected = transcriber.transcribe(args.transcribe, config.default_language)
        text = clean_transcript(raw, config, config.default_language)
        print(f"[{detected}, {time.perf_counter() - started:.2f}s] {text}")
        return 0

    try:
        instance_lock = acquire_instance_lock()
    except RuntimeError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1

    app = VoiceInputApp(config)
    try:
        app.prepare()
        RightOptionListener(app, debug_events=args.debug_events).run()
    except Exception as exc:
        app.shutdown()
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1
    finally:
        instance_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
