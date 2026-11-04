"""语音识别与合成：AIHubMix（OpenAI 兼容 /audio 接口）与阿里云百炼两种实现，封装成同一个可替换接口。

- 识别失败一律抛 VoiceUnavailable，前端回到文字输入。
- 在线合成时可录制音频；离线模式只播放录制音频，没有录制就返回 None（前端只显示文字）。
- 每次调用写入调用日志（角色 voice_asr / voice_tts），按公开价格折算成本。
"""
from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path
from typing import Any

from ..gateway.gateway import ModelGateway
from ..gateway.pricing import audio_cost_usd


class VoiceUnavailable(Exception):
    pass


class VoiceService:
    def __init__(self, gateway: ModelGateway):
        self.gw = gateway
        self.settings = gateway.settings
        self.cfg = self.settings.models.get("voice", {})
        self.audio_dir = Path(self.settings.recordings_dir) / "audio"

    # ------------------------------------------------------------------
    def _client(self) -> Any:
        from openai import OpenAI

        key = self.settings.api_key()
        if not key:
            raise VoiceUnavailable("没有配置 API Key")
        return OpenAI(base_url=self.settings.base_url(), api_key=key, timeout=float(self.cfg.get("asr", {}).get("timeout_s", 15)))

    def transcribe(self, audio: bytes, filename: str = "speech.webm", session_id: str | None = None) -> dict[str, Any]:
        mode = self.gw.mode
        if mode != "online":
            raise VoiceUnavailable("离线回放 / 降级模式下语音输入关闭，请用文字输入")
        asr = self.cfg.get("asr", {})
        provider = asr.get("provider", "aihubmix")
        t0 = time.perf_counter()
        try:
            if provider == "bailian":
                text = self._bailian_asr(audio)
            else:
                f = io.BytesIO(audio)
                f.name = filename
                resp = self._client().audio.transcriptions.create(model=asr.get("model", "whisper-large-v3"), file=f,
                                                                  language=asr.get("language", "zh"))
                text = getattr(resp, "text", "") or ""
        except VoiceUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            self.gw._log({"role": "voice_asr", "model": asr.get("model"), "mode": "online", "ok": False,
                          "error": f"{type(exc).__name__}: {str(exc)[:200]}", "session_id": session_id})
            raise VoiceUnavailable(f"语音识别失败：{exc}") from exc
        latency = round((time.perf_counter() - t0) * 1000, 1)
        seconds = max(1.0, len(audio) / 16000.0)  # 粗略估计：webm/opus 约 16KB/s
        cost = audio_cost_usd(self.settings.catalog, asr.get("model", ""), seconds=seconds)
        self.gw._log({"role": "voice_asr", "model": asr.get("model"), "mode": "online", "ok": bool(text), "latency_ms": latency,
                      "cost_usd": cost, "session_id": session_id, "audio_seconds_est": seconds})
        if not text.strip():
            raise VoiceUnavailable("没有识别出内容，请再说一遍或改用文字输入")
        return {"text": text.strip(), "latency_ms": latency}

    def _bailian_asr(self, audio: bytes) -> str:  # pragma: no cover - 需要百炼 Key 才能验证
        import os

        import httpx

        b = self.cfg.get("bailian", {})
        key = os.environ.get(b.get("api_key_env", "DASHSCOPE_API_KEY"))
        if not key:
            raise VoiceUnavailable("没有配置百炼 Key")
        # 百炼录音文件识别需先上传文件再提交任务；目前以 OpenAI 兼容接口为主，这里保留同一接口的接入位置。
        raise VoiceUnavailable("百炼语音识别尚未接入，请改用 aihubmix 或文字输入")

    # ------------------------------------------------------------------
    def _audio_key(self, text: str, voice: str) -> str:
        return hashlib.sha256(f"{voice}|{text}".encode("utf-8")).hexdigest()[:24]

    def speak(self, text: str, speaker: str = "coach", session_id: str | None = None) -> tuple[bytes, str] | None:
        tts = self.cfg.get("tts", {})
        voice = (tts.get("voices") or {}).get(speaker, "alloy")
        key = self._audio_key(text, voice)
        path = self.audio_dir / f"{key}.mp3"
        if path.exists():
            return path.read_bytes(), "replay"
        if self.gw.mode != "online":
            return None
        t0 = time.perf_counter()
        last_exc: Exception | None = None
        for model in [tts.get("model"), tts.get("fallback_model")]:
            if not model:
                continue
            try:
                resp = self._client().audio.speech.create(model=model, voice=voice, input=text)
                data = resp.read() if hasattr(resp, "read") else resp.content
                latency = round((time.perf_counter() - t0) * 1000, 1)
                self.gw._log({"role": "voice_tts", "model": model, "mode": "online", "ok": True, "latency_ms": latency,
                              "cost_usd": audio_cost_usd(self.settings.catalog, model, characters=len(text)),
                              "session_id": session_id})
                if self.settings.record_calls:
                    self.audio_dir.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                return data, "online"
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
        self.gw._log({"role": "voice_tts", "model": tts.get("model"), "mode": "online", "ok": False,
                      "error": f"{type(last_exc).__name__}: {str(last_exc)[:200]}" if last_exc else "未配置", "session_id": session_id})
        return None
