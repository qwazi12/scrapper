"""Narration via Gemini TTS (owner, 2026-10-13): gemini-3.8-flash-tts, Charon, recap-host style."""

import base64
import io
import json
import wave

import httpx
import pytest

from backend.app.config import settings
from backend.app.studio import tts


def _wav(seconds=0.5):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * int(24000 * seconds))
    return buf.getvalue()


class Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self.is_success = 200 <= status < 300
        self._p = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._p


@pytest.fixture()
def gem(monkeypatch, tmp_path):
    tts._working_api.clear()
    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini")
    monkeypatch.setattr(settings, "tts_engine", "gemini")
    monkeypatch.setattr(tts, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)
    calls = []

    def post(url, json=None, headers=None, timeout=None, **kw):
        calls.append({"url": url, "json": json, "headers": headers})
        if len(calls) == 1 and getattr(post, "first_429", False):
            return Resp(429, {"error": "rate"})
        audio = base64.b64encode(_wav()).decode()
        if url.endswith(":generateContent"):
            return Resp(200, {"candidates": [{"content": {"parts": [{"inlineData": {"data": audio}}]}}],
                              "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 300}})
        if getattr(post, "no_interactions", False):
            return Resp(400, {"error": {"message": "not supported"}})
        return Resp(200, {"steps": [{"type": "model_output", "content": [
            {"type": "audio", "data": "bm90LWF1ZGlv"}, {"type": "audio", "data": audio}]}]})
    monkeypatch.setattr(httpx, "post", post)
    return calls, post


def test_interactions_request_is_charon_with_the_style(gem, tmp_path):
    calls, _ = gem
    out = tts.synth("Number 4: Alien.", tmp_path / "a.mp3")   # also proves the LAST audio block is used
    assert out.exists() and out.stat().st_size > 0
    req = calls[0]
    assert req["url"].endswith("/v1beta/interactions")
    assert req["headers"]["x-goog-api-key"] == "test-gemini"
    body = req["json"]
    assert body["model"] == "gemini-3.8-flash-tts"
    assert body["generation_config"]["speech_config"] == [{"voice": "Charon"}]
    assert body["input"][0]["content"][0] == {"type": "text", "text": "Number 4: Alien.", "annotations": [
        {"type": "speech_metadata", "style": "energetic, fast-paced YouTube recap host"}]}
    tts.synth("Number 4: Alien.", tmp_path / "b.mp3")            # cached: no second request
    # only TTS requests (a background scheduler thread may hit the same fake with a text call)
    assert len([c for c in calls if c["url"].endswith("/interactions") or "-tts:" in c["url"]]) == 1


def test_falls_back_to_generate_content(gem, tmp_path):
    calls, post = gem
    post.no_interactions = True
    tts.synth("Number 3: The Thing.", tmp_path / "c.mp3")
    body = calls[-1]["json"]
    assert calls[-1]["url"].endswith("/models/gemini-3.8-flash-tts:generateContent")
    assert body["generationConfig"]["speechConfig"] == {"voiceConfig": {"voice": "Charon"}}
    assert body["contents"][0]["parts"][0]["speechMetadata"] == {"style": "energetic, fast-paced YouTube recap host"}
    assert tts._working_api["api"] == "generate"


def test_rate_limit_is_retried(gem, tmp_path):
    calls, post = gem
    post.first_429 = True
    tts.synth("Retry me.", tmp_path / "r.mp3")
    assert len(calls) == 2 and calls[1]["url"].endswith("/interactions")


def test_missing_key_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "tts_engine", "gemini")
    monkeypatch.setattr(tts, "cache_dir", lambda: tmp_path)
    with pytest.raises(tts.TTSError, match="GEMINI_API_KEY"):
        tts.synth("Hello.", tmp_path / "d.mp3")


def test_cost_is_recorded_from_tokens_or_estimated():
    from backend.app import costs
    est = costs.record_gemini_tts("gemini-3.8-flash-tts", {}, 60.0, 900)          # 1 min of voice, no token counts
    assert abs(est - (225 / 1e6 * 0.50 + 1500 / 1e6 * 10.0)) < 1e-9                 # ≈ $0.015 a minute
    exact = costs.record_gemini_tts("gemini-3.8-flash-tts", {"input_tokens": 100, "output_tokens": 1000}, 40.0, 400)
    assert abs(exact - (100 / 1e6 * 0.50 + 1000 / 1e6 * 10.0)) < 1e-9
