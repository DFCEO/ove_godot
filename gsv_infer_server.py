"""
GPT-SoVITS v3 LoRA 独立推理服务器
直接复用 WebUI 的模型加载和推理逻辑，暴露简单 HTTP API
Usage: python gsv_infer_server.py
"""

import os
import sys
import json
import time
import http.server
import tempfile

# 切换到整合包目录
GSV_ROOT = r"C:\Users\zj199\.openclaw\workspace\voice\GPT-SoVITS-v3lora-20250228"
os.chdir(GSV_ROOT)

# 设置路径优先级，确保用正确的 GPT_SoVITS
sys.path.insert(0, GSV_ROOT)
sys.path.insert(0, os.path.join(GSV_ROOT, "GPT_SoVITS"))

# 重新导入 torch，确保 CUDA 可用
import torch

# 先加载 process_ckpt（全局依赖）
import process_ckpt
from process_ckpt import get_sovits_version_from_path_fast, load_sovits_new

# 然后加载 inference_webui（这会触发模型加载）
import inference_webui

# inference_webui 启动时自动调用了 change_sovits_weights + change_gpt_weights
# 用的是 weight.json 中保存的最新路径

REF_AUDIO = r"C:\Users\zj199\.openclaw\workspace\ove-godot\01.wav"
REF_TEXT = "今儿他来，明儿我再来，这样间错开来着"

print("[GSV Server] Models loaded. Ready for inference.", flush=True)


def synthesize(text: str) -> bytes:
    """调用 WebUI 的 get_tts_wav，返回 WAV 字节"""
    import librosa
    import soundfile as sf
    import numpy as np
    from io import BytesIO
    
    # 用 gradio 的 Warning，但推理时不会 popup UI，所以静默即可
    # 直接调 get_tts_wav
    sampling_rate, audio_data = inference_webui.get_tts_wav(
        ref_wav_path=REF_AUDIO,
        prompt_text=REF_TEXT,
        prompt_language="中文",
        text=text,
        text_language="中文",
        how_to_cut="按标点符号切",
        top_k=5,
        top_p=1.0,
        temperature=1.0,
        ref_free=False,
        speed=1.0,
        if_freeze=False,
        inp_refs=None,
        sample_steps=8,
        if_sr=False,
        pause_second=0.3,
    )
    
    # audio_data 是 (sampling_rate, numpy_array) 元组
    buf = BytesIO()
    sf.write(buf, audio_data, sampling_rate, format="WAV")
    return buf.getvalue()


class GSVHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            data = json.loads(raw)
            text = data.get("text", "")
            
            if not text:
                self._json_resp(400, {"error": "missing text"})
                return
            
            t0 = time.time()
            print(f"[GSV] Synthesizing: {text[:50]}...", flush=True)
            wav_bytes = synthesize(text)
            dt = time.time() - t0
            print(f"[GSV] Done in {dt:.1f}s, {len(wav_bytes)} bytes", flush=True)
            
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav_bytes)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(wav_bytes)
        except Exception as e:
            print(f"[GSV] Error: {e}", flush=True)
            import traceback
            traceback.print_exc()
            self._json_resp(500, {"error": str(e)})
    
    def _json_resp(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    
    def log_message(self, *args):
        pass


def main():
    port = 9881
    server = http.server.HTTPServer(("127.0.0.1", port), GSVHandler)
    print(f"[GSV Server] Listening on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
