"""2026-10-19: every Gemini voice line left its $0.02 budget hold stuck in "unknown" and was recorded at ~$0."""
import base64

from backend.app import budget_reservations as br, costs
from backend.app.db import SessionLocal
from backend.app.models import BudgetReservation
from backend.app.studio import tts


def test_voice_line_with_measured_audio_settles_its_hold(session):
    audio = base64.b64encode(b"\0" * 48000 * 6).decode()              # 6 s of 24 kHz 16-bit mono
    data = {"steps": [{"type": "model_output", "content": [{"type": "audio", "data": audio}]}]}
    with br.attempt("gemini-tts", 0.02) as rid:
        cost = costs.record_gemini_tts("gemini-3.8-flash-tts", tts.tts_usage(data),
                                       len(audio) * 3 / 4 / 48000, len("Number four: Alien."))
    with SessionLocal() as s:
        assert s.get(BudgetReservation, rid).state == "settled"
    assert 0.001 < cost < 0.003                                         # ~150 audio tokens at $10/M


def test_token_counts_under_either_name_are_read():
    assert tts.tts_usage({"usage": {"total_input_tokens": 12, "total_output_tokens": 300}}) == {"input_tokens": 12, "output_tokens": 300}
    assert tts.tts_usage({"usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 200}}) == {"input_tokens": 9, "output_tokens": 200}
    assert tts.tts_usage({}) == {}


def test_spoken_text_is_found_in_either_request_shape():
    assert tts._spoken_text(tts.gemini_payload("Hello there.")) == "Hello there."
    assert tts._spoken_text(tts.generate_payload("Hello there.")) == "Hello there."
