import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("AKBO_DATA_DIR", str(ROOT / ".data"))).resolve()
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "200")) * 1024 * 1024
MAX_AUDIO_SECONDS = int(os.getenv("MAX_AUDIO_SECONDS", "600"))
SAMPLE_RATE = 48_000
INSTRUMENTS = ("vocal", "bass", "drums", "synthesizer", "guitar", "piano")
LABELS = {"vocal": "보컬", "bass": "베이스", "drums": "드럼", "synthesizer": "신디사이저", "guitar": "기타", "piano": "피아노"}
PROMPTS = {
    "vocal": "The singing human voice, lead vocals and backing vocals.",
    "bass": "The bass guitar and low pitched bass instrument.",
    "drums": "The drum kit, percussion, kick, snare and cymbals.",
    "synthesizer": "The electronic synthesizer, synth pads and synth leads.",
    "guitar": "The acoustic and electric guitar, strumming and plucked guitar strings.",
    "piano": "The acoustic piano and electric piano keys.",
}
