"""Generate customer-side speech for the voice e2e test (Chromium plays it as a fake microphone).

    poetry run python -m scripts.make_test_audio <out_dir>

Writes <out_dir>/payment.wav and <out_dir>/escalate.wav for the first policy in the renewal queue
(needs GOOGLE_API_KEY in .env and a seeded DB). Long silences let the bot speak between lines.
"""

import sys
import wave
from datetime import timedelta

from google import genai
from google.genai import types
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal, utcnow
from app.core.formatting import IST
from app.models import Customer, Policy
from app.models.enums import PolicyStatus

RATE = 24_000  # Gemini TTS: 24 kHz mono s16le
DIGITS = "zero one two three four five six seven eight nine".split()


def spoken_policy(policy_no: str) -> str:
    prefix, year, num = policy_no.split("-")
    return f"{' '.join(prefix)}, {year}, {' '.join(DIGITS[int(d)] for d in num)}"


def tts(client: genai.Client, text: str) -> bytes:
    for model in ("gemini-3.8-flash-tts", "gemini-3.1-flash-tts-preview"):
        try:
            r = client.models.generate_content(model=model, contents=text, config=types.GenerateContentConfig(
                response_modalities=["AUDIO"], speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")))))
            return r.candidates[0].content.parts[0].inline_data.data
        except Exception as e:  # quota/overload: try the next TTS model
            print(f"{model}: {e}", file=sys.stderr)
    raise SystemExit("No TTS model available")


def write(path: str, client: genai.Client, lines: list[tuple[int, str]]) -> None:
    pcm = bytearray()
    for pause, text in lines:
        pcm += b"\x00\x00" * (RATE * pause) + tts(client, text)
    pcm += b"\x00\x00" * (RATE * 120)  # trailing silence so the file doesn't loop mid-call
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(pcm))
    print(f"wrote {path}")


def main(out_dir: str) -> None:
    today = utcnow().astimezone(IST).date()
    with SessionLocal() as db:  # same ordering as GET /voice/queue
        p, c = db.execute(select(Policy, Customer).join(Customer, Customer.id == Policy.customer_id)
                          .where(Policy.status.in_([PolicyStatus.due, PolicyStatus.active]),
                                 Policy.renewal_date.between(today, today + timedelta(days=30)))
                          .order_by(Policy.renewal_date, Policy.id)).first()
    first = c.full_name.split()[0]
    identity = f"My policy number is {spoken_policy(p.policy_no)}, and my date of birth is {c.dob:%-d %B %Y}."
    client = genai.Client(api_key=get_settings().google_api_key)
    write(f"{out_dir}/payment.wav", client, [(7, f"Yes, this is {first} speaking."), (10, identity),
          (16, "Yes please. Send me the payment link on WhatsApp."), (14, "No, that's all. Thank you.")])
    write(f"{out_dir}/escalate.wav", client, [(7, "Yes, speaking."), (10, identity),
          (16, "Last year I was charged a late fee even though I paid on time. I want to speak to a person about this.")])
    print(f"customer={c.full_name} policy={p.policy_no}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
