# generate_tts.py
# Purpose:
# - Read latest (or specified) manifest-YYYY-MM-DD.json
# - Generate (or reuse) Gemini TTS audio per section with caching via audio_hash
# - Use different voices per "role" (PRIEST vs LECTOR) to keep consistency
# - Add liturgical closing phrase at the end of readings/gospel for TTS output
# - Retry on transient Gemini TTS errors (429/5xx)

from __future__ import annotations

import os
import re
import json
import time
import base64
import wave
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
from shutil import copyfile

import requests
from requests.exceptions import HTTPError, Timeout, ConnectionError
from dotenv import load_dotenv
import os

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")

VOICE_PRIEST = os.getenv("GEMINI_TTS_VOICE_PRIEST", "Charon")
VOICE_LECTOR = os.getenv("GEMINI_TTS_VOICE_LECTOR", "Kore")

# Global cache directory for audio assets
ASSETS_DIR = Path(os.getenv("TTS_ASSETS_DIR", "data/assets/audio"))

# Gemini TTS (per docs/examples): PCM 24kHz mono, 16-bit
PCM_SAMPLE_RATE = 24000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH_BYTES = 2  # 16-bit

# Stable profile id (affects audio_hash). Change only if you change style substantially.
STYLE_PROFILE_ID = os.getenv("TTS_STYLE_PROFILE_ID", "priest_es_co_v1")


# =========================
# ROLE + CLOSING PHRASES
# =========================
# Assign roles by section id
VOICE_BY_SECTION = {
    # PRIEST voice
    "welcome": "PRIEST",
    "homily": "PRIEST",
    "final_reflection": "PRIEST",
    "closing": "PRIEST",
    "gospel": "PRIEST",
    "confiteor": "PRIEST",
    "creed": "PRIEST",
    "lords_prayer": "PRIEST",

    # LECTOR voice
    "first_reading": "LECTOR",
    "psalm": "LECTOR",
    "second_reading": "LECTOR",
}

VOICE_PROFILES = {
    "PRIEST": {"voice_name": VOICE_PRIEST},
    "LECTOR": {"voice_name": VOICE_LECTOR},
}

# Liturgical closing phrases appended ONLY for TTS (not modifying manifest text unless you choose to)
# If you want gospel to end with "Palabra de Dios." change it here.
CLOSING_PHRASE_BY_SECTION = {
    "first_reading": "Palabra de Dios.",
    "second_reading": "Palabra de Dios.",
    "psalm": "Palabra de Dios.",
    "gospel": "Palabra del Señor.",
}


# =========================
# HELPERS
# =========================
def utc_now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def normalize_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def safe_filename(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9_\-\.]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-") or "section"

def write_wav(path: Path, pcm_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(PCM_CHANNELS)
        wf.setsampwidth(PCM_SAMPLE_WIDTH_BYTES)
        wf.setframerate(PCM_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)

def add_closing_phrase(sec_id: str, text: str) -> str:
    phrase = CLOSING_PHRASE_BY_SECTION.get(sec_id)
    if not phrase:
        return text

    t = (text or "").rstrip()
    # avoid duplicates
    t_low = t.lower().rstrip(".")
    p_low = phrase.lower().rstrip(".")
    if t_low.endswith(p_low):
        return text

    return f"{t}\n\n{phrase}"

def compute_audio_hash(text_hash: str, *, model: str, voice_name: str, style_profile_id: str) -> str:
    # Anything that changes the audio MUST be part of this hash.
    return sha256_hex(f"{text_hash}|{model}|{voice_name}|{style_profile_id}")

def date_str_from_manifest_path(manifest_path: Path) -> str:
    # expects manifest-YYYY-MM-DD.json
    stem = manifest_path.stem
    if stem.startswith("manifest-"):
        return stem.replace("manifest-", "")
    # fallback
    return datetime.utcnow().strftime("%Y-%m-%d")

def link_audio_to_date(cache_path: Path, date_dir: Path, sec_id: str) -> Path:
    date_dir.mkdir(parents=True, exist_ok=True)
    out = date_dir / f"{sec_id}.wav"
    if not out.exists():
        copyfile(cache_path, out)   # o symlink si prefieres
    return out


# =========================
# GEMINI TTS CALL
# =========================
def gemini_tts_request(text: str, *, model: str, voice_name: str, style_prompt: str) -> bytes:
    """
    Returns raw PCM bytes (s16le, 24kHz, mono) from Gemini TTS.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("Missing GEMINI_API_KEY (or GOOGLE_API_KEY) in environment variables.")

    # "Director notes" + transcript
    prompt = f"""{style_prompt}

TRANSCRIPCIÓN (lee exactamente este texto):
{text}
""".strip()

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice_name}
                }
            }
        }
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }

    r = requests.post(url, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()

    # Inline base64 audio
    try:
        b64 = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    except KeyError as e:
        raise RuntimeError(f"Unexpected Gemini TTS response shape: missing {e}. Response keys: {list(data.keys())}")

    return base64.b64decode(b64)

def gemini_tts_request_with_retry(
    text: str,
    *,
    model: str,
    voice_name: str,
    style_prompt: str,
    max_retries: int = 6,
) -> bytes:
    """
    Retries on transient errors (429, 5xx, timeouts).
    """
    delay = 2
    last_err: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            return gemini_tts_request(text, model=model, voice_name=voice_name, style_prompt=style_prompt)
        except HTTPError as e:
            last_err = e
            status = e.response.status_code if e.response is not None else None
            # retry on transient
            if status in (429, 500, 502, 503, 504):
                print(f"[TTS] HTTP {status} (attempt {attempt}/{max_retries}) -> retry in {delay}s")
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise
        except (Timeout, ConnectionError) as e:
            last_err = e
            print(f"[TTS] Network error (attempt {attempt}/{max_retries}) -> retry in {delay}s: {e}")
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue

    raise RuntimeError(f"TTS failed after {max_retries} retries. Last error: {last_err}")

# =========================
# MAIN PIPELINE
# =========================
def generate_tts_for_manifest(
    manifest_path: Path,
    *,
    assets_dir: Path = ASSETS_DIR,
    model: str = GEMINI_TTS_MODEL,
    style_profile_id: str = STYLE_PROFILE_ID,
    write_back_manifest: bool = True,
) -> None:
    manifest: Dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    sections = manifest.get("sections", [])
    if not isinstance(sections, list) or not sections:
        raise ValueError("Manifest has no sections[]")

    # One stable style prompt (keep consistent to preserve audio cache)
    style_prompt = (
        "PERFIL DE AUDIO: Voz serena, cálida y pastoral, como un sacerdote en una iglesia. "
        "Acento español colombiano (es-CO), dicción clara, ritmo moderado, pausas naturales.\n"
        "NOTAS DEL DIRECTOR: Evita dramatización excesiva. Mantén reverencia. "
        "En lecturas bíblicas, tono proclamativo; en oraciones, tono devocional; "
        "en homilía, tono cercano y esperanzador."
    )

    date_str = date_str_from_manifest_path(manifest_path)
    by_date_dir = assets_dir / "by-date" / date_str

    updated_any = False
    created_count = 0
    reused_count = 0
    linked_count = 0

    for sec in sections:
        sec_id = sec.get("id") or "section"
        text = normalize_text(sec.get("text", "") or "")
        text_hash = sec.get("text_hash")

        if not text or not text_hash:
            continue

        # Choose role/voice
        role = VOICE_BY_SECTION.get(sec_id, "PRIEST")
        voice_name = VOICE_PROFILES.get(role, VOICE_PROFILES["PRIEST"])["voice_name"]

        # Add closing phrases ONLY for audio rendering
        tts_text = add_closing_phrase(sec_id, text)

        # audio hash is based on original text_hash (stable), but includes voice/model/style profile
        audio_hash = compute_audio_hash(text_hash, model=model, voice_name=voice_name, style_profile_id=style_profile_id)

        #out_dir = assets_dir / style_profile_id / voice_name
        #out_file = f"{safe_filename(sec_id)}-{audio_hash}.wav"
        #out_path = out_dir / out_file

        cache_dir = assets_dir / "cache" / style_profile_id / voice_name
        cache_path = cache_dir / f"{audio_hash}.wav"

        if cache_path.exists():
            reused_count += 1
        else:
            pcm = gemini_tts_request_with_retry(
                tts_text,
                model=model,
                voice_name=voice_name,
                style_prompt=style_prompt,
            )
            write_wav(cache_path, pcm)
            created_count += 1

        # Daily path (human friendly)
        daily_path = link_audio_to_date(cache_path, by_date_dir, sec_id)
        linked_count += 1

        # Update manifest audio block
        sec["audio"] = {
            "provider": "gemini-tts",
            "model": model,
            "voice_name": voice_name,
            "role": role,
            "style_profile_id": style_profile_id,
            "audio_hash": audio_hash,     # cache key
            "mime_type": "audio/wav",
            "sample_rate_hz": PCM_SAMPLE_RATE,
            "channels": PCM_CHANNELS,
            "path": str(daily_path).replace("\\", "/"),
            "cache_path": str(cache_path).replace("\\", "/"),
            "generated_at": utc_now_iso(),
        }

        updated_any = True

    # Write back manifest
    if write_back_manifest and updated_any:
        manifest.setdefault("audio_generation", {})
        manifest["audio_generation"].update({
            "last_run_at": utc_now_iso(),
            "provider": "gemini-tts",
            "model": model,
            "style_profile_id": style_profile_id,
            "voices": {
                "PRIEST": VOICE_PRIEST,
                "LECTOR": VOICE_LECTOR,
            },
            "created": created_count,
            "reused": reused_count,
        })
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OK: TTS done for {manifest_path.name} | created={created_count} reused={reused_count}")


def find_latest_manifest(manifests_dir: Path = Path("data/manifests")) -> Path:
    files = sorted(manifests_dir.glob("manifest-*.json"), reverse=True)
    if not files:
        raise FileNotFoundError("No manifests found in data/manifests.")
    return files[0]


if __name__ == "__main__":
    # Use latest manifest by default; optionally pass a path:
    #   python3 app/generate_tts.py data/manifests/manifest-2026-01-07.json
    import sys
    if len(sys.argv) >= 2:
        mp = Path(sys.argv[1])
    else:
        mp = find_latest_manifest()

    generate_tts_for_manifest(mp)