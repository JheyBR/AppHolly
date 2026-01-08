import os
import json
import re
import hashlib
import requests
from pathlib import Path

# -----------------------------
# Utilidades
# -----------------------------
def normalize_text(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def save_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

# -----------------------------
# Gemini
# -----------------------------
def gemini_generate_liturgy_parts(readings: dict) -> list[dict]:
    api_key = os.getenv("GOOGLE_API_KEY", "AIzaSyBQP-L6kpJUQOoKSOeIJFozEnUUJGMBX38")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if not api_key:
        raise RuntimeError("Falta GOOGLE_API_KEY en variables de entorno")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    # Contexto (no demasiado largo para no gastar tokens)
    gospel = readings.get("gospel", "")
    first = readings.get("first_reading", "")
    psalm = readings.get("psalm", "")
    second = readings.get("second_reading", "")

    prompt = f"""
    Devuelve ÚNICAMENTE un JSON válido (sin markdown, sin texto adicional).

    Objetivo:
    Generar las partes faltantes de una MISA VIRTUAL en español (es-CO), basadas en las lecturas del día.
    IMPORTANTE:
    - NO reescribas ni modifiques las lecturas originales. Solo úsalo como contexto.
    - No inventes referencias bíblicas (capítulos/versículos).
    - Tono pastoral, respetuoso y cálido.
    - Textos listos para narración (puntuación y pausas naturales).

    Lecturas (contexto):
    EVANGELIO:
    {gospel}

    PRIMERA LECTURA (opcional):
    {first}

    SALMO (opcional):
    {psalm}

    SEGUNDA LECTURA (si existe):
    {second}

    Salida:
    Un JSON con este esquema exacto:
    {{
      "language": "es-CO",
      "sections": [
        {{
          "id": "welcome",
          "type": "speech",
          "title": "Bienvenida",
          "text": "..."
        }},
        {{
          "id": "homily",
          "type": "homily",
          "title": "Homilía",
          "text": "..."
        }},
        {{
          "id": "final_reflection",
          "type": "speech",
          "title": "Reflexión final",
          "text": "..."
        }},
        {{
          "id": "closing",
          "type": "speech",
          "title": "Cierre",
          "text": "..."
        }}
      ]
    }}

    Longitudes sugeridas (aprox):
    - Bienvenida: 15–30 segundos
    - Homilía: 3–5 minutos
    - Reflexión final: 45–90 segundos
    - Cierre: 30–60 segundos
    """.strip()

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.5,
            "maxOutputTokens": 8192
        }
    }

    r = requests.post(url, json=payload, timeout=90)
    r.raise_for_status()
    data = r.json()

    raw = data["candidates"][0]["content"]["parts"][0]["text"]
    clean = raw.replace("```json", "").replace("```", "").strip()
    finish_reason = data["candidates"][0].get("finishReason")
    print("Gemini finishReason:", finish_reason)

    try:
        parsed = json.loads(clean)
    except Exception as e:
        raise ValueError(f"Gemini no devolvió JSON válido: {e}\nRespuesta cruda:\n{raw[:1200]}")

    sections = parsed.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("Respuesta de Gemini inválida: falta sections[]")

    # Validación mínima de campos
    for s in sections:
        for k in ("id", "type", "title", "text"):
            if k not in s or not str(s[k]).strip():
                raise ValueError(f"Sección inválida en respuesta Gemini. Falta/campo vacío: {k}. Sección: {s.get('id')}")

        s["text"] = normalize_text(s["text"])

    return sections

# -----------------------------
# Ensamble en manifest
# -----------------------------
ORDER = [
    "welcome",
    "first_reading",
    "psalm",
    "second_reading",
    "gospel",
    "homily",
    "confiteor",
    "creed",
    "lords_prayer",
    "final_reflection",
    "closing",
]

def section_to_manifest_shape(section: dict, source_url: str) -> dict:
    text = normalize_text(section["text"])
    text_hash = sha256_hex(f"{section['id']}|{section['type']}|{text}")
    return {
        "id": section["id"],
        "type": section["type"],
        "title": section["title"],
        "source_url": source_url,
        "text": text,
        "text_hash": text_hash,
        "audio": None,
        "premium": {"video": None, "video_hash": None},
    }

def enrich_manifest(manifest: dict, generated_sections: list[dict]) -> dict:
    # Index actual sections by id
    existing = {s["id"]: s for s in manifest.get("sections", [])}

    # Upsert generated sections
    for gs in generated_sections:
        existing[gs["id"]] = section_to_manifest_shape(gs, source_url="generated_by_gemini")

    # Rebuild ordered list (solo incluye second_reading si existe y tiene texto)
    new_sections = []
    for sec_id in ORDER:
        s = existing.get(sec_id)
        if not s:
            continue
        if sec_id == "second_reading":
            if not s.get("text", "").strip():
                continue
        new_sections.append(s)

    manifest["sections"] = new_sections
    manifest.setdefault("language", "es-CO")
    manifest["generated_parts"] = manifest.get("generated_parts", {})
    manifest["generated_parts"]["gemini"] = {
        "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        "included_ids": [s["id"] for s in generated_sections],
    }
    return manifest

def gemini_generate_main():
    # Puedes pasar una fecha fija si quieres. Por ahora: toma el manifest más reciente.
    manifests_dir = Path("data/manifests")
    files = sorted(manifests_dir.glob("manifest-*.json"), reverse=True)
    if not files:
        raise FileNotFoundError("No hay manifests en data/manifests. Genera primero el manifest desde Dominicos.")

    path = files[0]
    manifest = load_manifest(path)

    # Extrae lecturas del manifest (Dominicos)
    readings = {"first_reading": "", "psalm": "", "second_reading": "", "gospel": ""}
    for s in manifest.get("sections", []):
        if s.get("id") in readings:
            readings[s["id"]] = s.get("text", "")

    if not readings["gospel"].strip():
        raise ValueError("El manifest no tiene gospel con texto. No se puede generar homilía y partes litúrgicas.")

    generated = gemini_generate_liturgy_parts(readings)
    manifest = enrich_manifest(manifest, generated)

    save_manifest(path, manifest)
    print(f"OK: manifest enriquecido con secciones Gemini -> {path.name}")

if __name__ == "__main__":
    gemini_generate_main()