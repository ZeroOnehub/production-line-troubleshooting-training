"""模型网关：统一的 OpenAI 兼容调用 + 调用日志与成本 + 录制回放。

三种模式（.env 的 APP_MODE，界面右上角如实标识）：
  online   在线：调用 AIHubMix（或配置的其他 OpenAI 兼容平台）；RECORD_CALLS=true 时同时录制
  offline  离线回放：只从录制文件匹配，匹配不到抛 NoModelAvailable，由调用方走规则兜底
  degraded 降级·规则：不调用模型（没有 API Key 时 online 自动落到这里）
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..settings import Settings
from .pricing import cost_usd
from .recorder import RecordingStore

_LOG_LOCK = threading.Lock()


class NoModelAvailable(Exception):
    """模型不可用（降级模式 / 离线无录制 / 在线调用失败）。调用方据此走规则兜底。"""


@dataclass
class GatewayResult:
    content: str | None
    tool_calls: list[dict[str, Any]]
    model: str
    mode: str                      # online / replay
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float | None = 0.0
    match: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def message(self) -> dict[str, Any]:
        m: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            m["tool_calls"] = self.tool_calls
        return m

    def tool_args(self, name: str | None = None) -> list[dict[str, Any]]:
        out = []
        for tc in self.tool_calls:
            if name and tc["function"]["name"] != name:
                continue
            try:
                out.append(json.loads(tc["function"]["arguments"] or "{}"))
            except json.JSONDecodeError:
                out.append({"_invalid_json": tc["function"]["arguments"]})
        return out


class ModelGateway:
    def __init__(self, settings: Settings, client_factory: Callable[[str, str, float], Any] | None = None):
        self.settings = settings
        self.store = RecordingStore(settings.recordings_dir)
        self._client_factory = client_factory or self._default_client
        self._clients: dict[tuple[str, float], Any] = {}
        self.sinks: list[Callable[[dict[str, Any]], None]] = []
        self.record_name = "default"
        self.mode_override: str | None = None
        # 最近一次在线调用的成败（time.time()）。用于界面右上角如实显示“模型暂不可用”（断网、平台故障时）
        self.last_online_ok: float | None = None
        self.last_online_fail: float | None = None

    # ------------------------------------------------------------------
    @staticmethod
    def _default_client(base_url: str, api_key: str, timeout: float) -> Any:
        from openai import OpenAI

        return OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=1)

    def _client(self, timeout: float) -> Any:
        key = (self.settings.base_url(), timeout)
        if key not in self._clients:
            api_key = self.settings.api_key()
            if not api_key:
                raise NoModelAvailable("没有配置 API Key")
            self._clients[key] = self._client_factory(self.settings.base_url(), api_key, timeout)
        return self._clients[key]

    @property
    def mode(self) -> str:
        return self.mode_override or self.settings.effective_mode

    def online_unhealthy(self, window_s: float = 120.0) -> bool:
        """配置为在线，但最近一次在线调用失败（且在 window_s 秒内）：每次调用都在走规则兜底。"""
        f, ok = self.last_online_fail, self.last_online_ok
        return self.mode == "online" and f is not None and (ok is None or f > ok) and time.time() - f < window_s

    def model_for(self, role: str) -> str | None:
        return self.settings.role_cfg(role).get("model")

    # ------------------------------------------------------------------
    def chat(
        self,
        role: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        model: str | None = None,
        session_id: str | None = None,
        prompt_version: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        record_name: str | None = None,
        tags: dict[str, Any] | None = None,
        mode: str | None = None,
    ) -> GatewayResult:
        cfg = self.settings.role_cfg(role)
        model = model or cfg.get("model")
        mode = mode or self.mode
        base = {"role": role, "model": model, "prompt_version": prompt_version, "session_id": session_id,
                "tags": tags or {}}
        if model is None or mode == "degraded":
            self._log({**base, "mode": "fallback", "ok": False, "error": "降级模式（未调用模型）"})
            raise NoModelAvailable("degraded")
        if mode == "offline":
            t0 = time.perf_counter()
            rec, match = self.store.lookup(role, messages, tools)
            if rec is None:
                self._log({**base, "mode": "replay", "ok": False, "error": "离线回放未匹配到录制", "match": match})
                raise NoModelAvailable("no recording")
            resp = rec["response"]
            usage = rec.get("usage") or {}
            res = GatewayResult(
                content=resp.get("content"), tool_calls=resp.get("tool_calls") or [], model=rec.get("model", model),
                mode="replay", input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
                latency_ms=round((time.perf_counter() - t0) * 1000, 1), match=match,
                cost_usd=0.0,
            )
            self._log({**base, "model": res.model, "mode": "replay", "ok": True, "match": match,
                       "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                       "latency_ms": res.latency_ms, "recorded_latency_ms": rec.get("latency_ms"), "cost_usd": 0.0})
            return res
        # online
        timeout = float(cfg.get("timeout_s", 15))
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": cfg.get("temperature", 0.2) if temperature is None else temperature,
            "max_tokens": max_tokens or cfg.get("max_tokens", 600),
        }
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if cfg.get("extra_body"):
            kwargs["extra_body"] = cfg["extra_body"]
        t0 = time.perf_counter()
        try:
            client = self._client(timeout)
            resp = client.chat.completions.create(**kwargs)
        except NoModelAvailable:
            self._log({**base, "mode": "online", "ok": False, "error": "没有配置 API Key"})
            raise
        except Exception as exc:  # noqa: BLE001 - 任何调用失败都降级
            latency = round((time.perf_counter() - t0) * 1000, 1)
            self._log({**base, "mode": "online", "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}", "latency_ms": latency})
            self.last_online_fail = time.time()
            raise NoModelAvailable(str(exc)) from exc
        latency = round((time.perf_counter() - t0) * 1000, 1)
        self.last_online_ok = time.time()
        choice = resp.choices[0].message
        tool_calls = []
        for tc in (getattr(choice, "tool_calls", None) or []):
            tool_calls.append({"id": tc.id, "type": "function",
                               "function": {"name": tc.function.name, "arguments": tc.function.arguments}})
        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
        out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
        cost = cost_usd(self.settings.catalog, model, in_tok, out_tok)
        res = GatewayResult(content=choice.content, tool_calls=tool_calls, model=model, mode="online",
                            input_tokens=in_tok, output_tokens=out_tok, latency_ms=latency, cost_usd=cost)
        if self.settings.record_calls:
            self.store.append(record_name or self.record_name, role, messages, tools,
                              {"content": choice.content, "tool_calls": tool_calls}, model,
                              {"input_tokens": in_tok, "output_tokens": out_tok}, latency)
        self._log({**base, "mode": "online", "ok": True, "input_tokens": in_tok, "output_tokens": out_tok,
                   "latency_ms": latency, "cost_usd": cost})
        return res

    # ------------------------------------------------------------------
    def _log(self, rec: dict[str, Any]) -> None:
        rec = {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"), **rec}
        rec.setdefault("input_tokens", 0)
        rec.setdefault("output_tokens", 0)
        rec.setdefault("latency_ms", None)
        rec.setdefault("cost_usd", None)
        rec.setdefault("error", None)
        try:
            self.settings.logs_dir.mkdir(parents=True, exist_ok=True)
            with _LOG_LOCK:
                with (Path(self.settings.logs_dir) / "llm_calls.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass
        for sink in self.sinks:
            try:
                sink(rec)
            except Exception:  # noqa: BLE001 - 日志失败不影响主流程
                pass


_GATEWAY: ModelGateway | None = None


def get_gateway() -> ModelGateway:
    global _GATEWAY
    if _GATEWAY is None:
        from ..settings import get_settings

        _GATEWAY = ModelGateway(get_settings())
    return _GATEWAY


def set_gateway(gw: ModelGateway | None) -> None:
    global _GATEWAY
    _GATEWAY = gw
