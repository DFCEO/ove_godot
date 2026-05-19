"""
stt_service.py - Ove 的"耳朵"
==============================
持续监听麦克风 → Silero VAD 检测说话 → Faster-Whisper 转文字 → 推送至 bridge

依赖:
- pyaudio: 麦克风采集
- torch + silero-vad: 语音活动检测
- faster-whisper: 语音转文字 (GPU/CPU)
"""

import sys
import os
import time
import json
import threading
import queue
import struct
import wave
import tempfile
from collections import deque

import numpy as np

# ---- 配置 ----
SAMPLE_RATE = 16000          # Whisper 需要 16kHz
CHUNK_MS = 30                # 每帧 30ms
CHUNK_SAMPLES = 480           # 每帧采样数 (VAD 用缓冲区累积到 512)
SILENCE_DURATION = 1.0       # 静音 1.0s 认为说话结束（防中间停顿拆分）
SILENCE_FRAMES = int(SILENCE_DURATION * 1000 / CHUNK_MS)  # 18 frames @ 32ms
MIN_SPEECH_DURATION = 0.8    # 最短有效语音（防噪音误触发）
MIN_SPEECH_FRAMES = int(MIN_SPEECH_DURATION * 1000 / CHUNK_MS)  # 约 26 frames @ 30ms per VAD chunk
WHISPER_MODEL = "small"      # tiny/base/small/medium
BRIDGE_URL = "http://127.0.0.1:18779/voice"

# ---- 日志 ----
def log(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[STT {timestamp}] {msg}", flush=True)


# ═══════════════════════════ Silero VAD ═══════════════════════════

def load_vad():
    """加载 Silero VAD 模型(通过 torch hub)"""
    import torch
    model, utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        force_reload=False,
        trust_repo=True,
    )
    (get_speech_timestamps, _, read_audio, _, _) = utils
    log("Silero VAD loaded")
    return model


def vad_speech_prob(model, audio_chunk: np.ndarray) -> float:
    """返回单个音频块的语音概率 (0.0~1.0)

    audio_chunk: float32 numpy array, shape=(N,), 16kHz
    """
    import torch
    if audio_chunk.dtype != np.float32:
        audio_chunk = audio_chunk.astype(np.float32)
    tensor = torch.from_numpy(audio_chunk).unsqueeze(0)  # (1, N)
    with torch.no_grad():
        prob = model(tensor, SAMPLE_RATE).item()
    return prob


# ═══════════════════════════ PyAudio 采集 ═══════════════════════════

class Microphone:
    """麦克风音频采集线程"""

    def __init__(self, chunk_samples: int, target_rate: int, device_index: int = None):
        self.chunk = chunk_samples
        self.target_rate = target_rate
        self.device_index = device_index
        self._q: queue.Queue = queue.Queue()
        self._running = False
        self._thread: threading.Thread | None = None
        self._stream = None
        self._native_rate = target_rate
        try:
            import pyaudio
            self._pyaudio = pyaudio
        except ImportError:
            log("ERROR: pip install pyaudio")
            sys.exit(1)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log("Microphone started")

    def _run(self):
        p = self._pyaudio.PyAudio()

        # 确定设备和采样率
        dev_idx = self.device_index
        if dev_idx is None:
            dev_idx = p.get_default_input_device_info()["index"]
        dev_info = p.get_device_info_by_index(dev_idx)
        self._native_rate = int(dev_info["defaultSampleRate"])
        log(f"Using device [{dev_idx}]: {dev_info['name']} @ {self._native_rate}Hz")

        try:
            self._stream = p.open(
                format=self._pyaudio.paInt16,
                channels=1,
                rate=self._native_rate,
                input=True,
                input_device_index=dev_idx,
                frames_per_buffer=int(self.chunk * self._native_rate / self.target_rate),
            )
        except Exception as e:
            log(f"Microphone error: {e}")
            log("Available devices:")
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info["maxInputChannels"] > 0:
                    log(f"  [{i}] {info['name']}")
            p.terminate()
            return

        native_chunk = int(self.chunk * self._native_rate / self.target_rate)
        log(f"Capturing at {self._native_rate}Hz, chunk={native_chunk} → {self.target_rate}Hz")

        resample_ratio = self.target_rate / self._native_rate

        while self._running:
            try:
                data = self._stream.read(native_chunk, exception_on_overflow=False)
                samples = struct.unpack(f"{native_chunk}h", data)
                float_data = np.array(samples, dtype=np.float32) / 32768.0

                # 重采样到目标采样率(线性插值)
                if abs(resample_ratio - 1.0) > 0.001:
                    new_len = max(1, int(len(float_data) * resample_ratio))
                    float_data = np.interp(
                        np.linspace(0, len(float_data) - 1, new_len),
                        np.arange(len(float_data)),
                        float_data
                    ).astype(np.float32)

                self._q.put(float_data)
            except Exception as e:
                log(f"Read error: {e}")
                break

        self._stream.stop_stream()
        self._stream.close()
        p.terminate()

    def get(self, timeout: float = 0.1) -> np.ndarray | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self._running = False


# ═══════════════════════════ VAD + 录音 ═══════════════════════════

class SpeechDetector:
    """语音活动检测 + 录音缓冲"""

    def __init__(self, vad_model):
        self.vad = vad_model
        self._vad_buffer: list[float] = []      # 累积到 512 送给 VAD
        self._speech_buffer: list[np.ndarray] = []  # 完整语音录音
        self._is_speaking = False
        self._silence_count = 0
        self._speech_frames = 0  # VAD 判定为语音的帧数
        self._on_speech_end = None  # callback(text: str)
        self._debug_counter = 0

    def on_speech_end(self, callback):
        """设置回调:检测到完整语音后调用 callback(text)"""
        self._on_speech_end = callback

    def feed(self, chunk: np.ndarray):
        """喂入一个音频块 (float32, 16kHz)"""
        # 累积到 VAD 缓冲区
        self._vad_buffer.extend(chunk.tolist())

        # VAD 要求精确 512 采样
        while len(self._vad_buffer) >= 512:
            vad_chunk = np.array(self._vad_buffer[:512], dtype=np.float32)
            self._vad_buffer = self._vad_buffer[512:]
            self._process_vad_chunk(vad_chunk)

    def _process_vad_chunk(self, chunk: np.ndarray):
        """处理一个 512 采样的 VAD 块"""
        prob = vad_speech_prob(self.vad, chunk)

        # 调试:每秒打印一次
        self._debug_counter += 1
        if self._debug_counter >= 33:  # ~1s
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            log(f"Audio: rms={rms:.4f}  vad={prob:.3f}  speaking={self._is_speaking}")
            self._debug_counter = 0

        if prob > 0.85:
            # 语音
            if not self._is_speaking:
                self._is_speaking = True
                self._speech_buffer = []
                self._speech_frames = 1
                self._silence_count = 0
            else:
                self._speech_frames += 1
                self._silence_count = 0
            self._speech_buffer.append(chunk)
        else:
            # 静音
            if self._is_speaking:
                self._speech_buffer.append(chunk)
                self._silence_count += 1
                if self._silence_count >= SILENCE_FRAMES:
                    self._is_speaking = False
                    if self._speech_frames >= MIN_SPEECH_FRAMES:
                        self._finalize()
                    else:
                        self._speech_buffer = []
            else:
                # 不在说话中,丢掉静音(但保留一点上下文)
                pass


    def _finalize(self):
        """拼接录音 → 转文字 → 回调"""
        if not self._speech_buffer:
            return
        audio = np.concatenate(self._speech_buffer)
        duration = len(audio) / SAMPLE_RATE
        log(f"Speech detected: {duration:.1f}s ({self._speech_frames} frames)")

        # 在另一个线程中转写,不要阻塞音频采集
        t = threading.Thread(target=self._transcribe, args=(audio,), daemon=True)
        t.start()

    def _transcribe(self, audio: np.ndarray):
        text = transcribe(audio)
        if text and text.strip():
            log(f"→ \"{text}\"")
            if self._on_speech_end:
                self._on_speech_end(text.strip())


# ═══════════════════════════ Whisper 转写 ═══════════════════════════

_whisper_model = None
_whisper_lock = threading.Lock()

def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        log(f"Loading Whisper '{WHISPER_MODEL}'...")
        _whisper_model = whisper.load_model(WHISPER_MODEL)
        log("Whisper ready")
    return _whisper_model


def transcribe(audio: np.ndarray) -> str:
    """转写 float32 numpy array (16kHz) → 文本"""
    with _whisper_lock:
        model = get_whisper()
        result = model.transcribe(
            audio,
            language="zh",
            fp16=False,
        )
    text = result.get("text", "").strip()
    return text


# ═══════════════════════════ Bridge 推送 ═══════════════════════════

def send_to_bridge(text: str):
    """将转写文本推送到 ove_bridge"""
    import urllib.request
    try:
        body = json.dumps({"text": text, "source": "voice"}).encode("utf-8")
        req = urllib.request.Request(
            BRIDGE_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            pass
    except Exception as e:
        log(f"Bridge push error: {e}")


# ═══════════════════════════ Main ═══════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ove STT Service")
    parser.add_argument("--device", "-d", type=int, default=None, help="Input device index")
    parser.add_argument("--list", "-l", action="store_true", help="List input devices and exit")
    args = parser.parse_args()
    
    if args.list:
        import pyaudio
        p = pyaudio.PyAudio()
        print("Input devices:")
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0:
                print(f"  [{i}] {info['name']}")
        p.terminate()
        return
    
    log("=== Ove STT Service — 耳朵 ===")

    # 加载 VAD
    vad = load_vad()

    # 创建检测器
    detector = SpeechDetector(vad)
    detector.on_speech_end(send_to_bridge)

    # 启动麦克风（使用默认或指定设备）
    mic = Microphone(CHUNK_SAMPLES, SAMPLE_RATE, device_index=args.device)
    mic.start()

    log("Listening... (Ctrl+C to stop)")

    try:
        while True:
            chunk = mic.get(timeout=0.1)
            if chunk is not None:
                detector.feed(chunk)
            else:
                time.sleep(0.01)
    except KeyboardInterrupt:
        log("Stopping...")
    finally:
        mic.stop()


if __name__ == "__main__":
    main()
