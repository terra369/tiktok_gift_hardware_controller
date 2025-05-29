# audio_tts.py
import os, requests, tempfile
from playsound3 import playsound
from config import OPENAI_API_KEY

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {OPENAI_API_KEY}",
}
BASE_PAYLOAD = {"model": "tts-1", "voice": "alloy", "response_format": "mp3"}


def speak(text: str) -> None:
    """text を日本語音声に変換して PC スピーカーで再生"""
    data = {**BASE_PAYLOAD, "input": text}
    r = requests.post(
        "https://api.openai.com/v1/audio/speech", headers=HEADERS, json=data, timeout=60
    )
    r.raise_for_status()

    # 一時ファイルに保存して再生
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
        fp.write(r.content)
        temp_path = fp.name
    # block=True で完再生待ち
    playsound(temp_path, block=True)
