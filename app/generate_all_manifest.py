import json
import asyncio
from pathlib import Path
from datetime import date, datetime
from DownloadText import generate_manifest_from_dominicos
from enrich_manifest_with_gemini import gemini_generate_main
from add_prayers_to_manifest import upsert_prayers_into_manifest
from generate_tts import generate_tts_for_manifest

MANIFESTS_DIR = Path("data/manifests")
TEMPLATES_PRAYERS = Path("data/templates/prayers_es.json")
VARIABLE_GEMINI_IDS = {"welcome", "homily", "final_reflection", "closing"}
PRAYERS_IDS = {"confiteor", "creed", "lords_prayer"}


def manifest_path_for_today() -> Path:
    iso = date.today().strftime("%Y-%m-%d")
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    return MANIFESTS_DIR / f"manifest-{iso}.json"

def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def save_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

def has_sections(manifest: dict, required_ids: set[str]) -> bool:
    ids = {s.get("id") for s in manifest.get("sections", [])}
    return required_ids.issubset(ids)

def all_sections_have_audio(manifest: dict) -> bool:
    for s in manifest.get("sections", []):
        if not (s.get("text") or "").strip():
            continue

        audio = s.get("audio") or {}
        path = (audio.get("path") or "").strip()
        if not path:
            return False

        if not Path(path).exists():
            return False

    return True

def ensure_dominicos_manifest(manifest_path: Path):
    """
    Si el manifest del día NO existe -> lo crea desde Dominicos.
    Si existe -> no hace nada.
    """
    if manifest_path.exists():
        print(f"[SKIP] Dominicos: ya existe {manifest_path.name}")
        return

    print("[RUN] Dominicos: generando manifest base...")
    # Debes implementar/llamar tu función que genera el manifest base en esa ruta.
    # Ideal: que tu función reciba la fecha o el path.
    obtener_datos_liturgicos_dominicos()  # <-- tu función actual

def ensure_prayers(manifest_path: Path):
    """
    Inserta prayers si no están.
    """
    manifest = load_manifest(manifest_path)

    if has_sections(manifest, PRAYERS_IDS):
        print("[SKIP] Prayers: ya están incluidos en el manifest")
        return

    print("[RUN] Prayers: insertando oraciones fijas...")
    # Si tu función modifica el manifest en disco internamente, perfecto.
    add_prayers_to_manifest()  # <-- tu función actual (o upsert_prayers_into_manifest)

def ensure_gemini_sections(manifest_path: Path):
    """
    Ejecuta Gemini solo si faltan secciones variables.
    """
    manifest = load_manifest(manifest_path)

    if has_sections(manifest, VARIABLE_GEMINI_IDS):
        print("[SKIP] Gemini: secciones variables ya existen")
        return

    print("[RUN] Gemini: generando bienvenida/homilía/reflexión/cierre...")
    gemini_generate_main()  # <-- tu función actual

def clear_missing_audio_entries(manifest: dict) -> dict:
    from pathlib import Path
    changed = False

    for s in manifest.get("sections", []):
        audio = s.get("audio") or None
        if not audio:
            continue

        p = (audio.get("path") or "").strip()
        if p and not Path(p).exists():
            s["audio"] = None
            changed = True

    if changed:
        print("[INFO] Se limpiaron referencias de audio inexistentes en el manifest.")
    return manifest

def ensure_tts(manifest_path: Path):
    """
    Genera TTS solo si falta audio en alguna sección con texto.
    """
    manifest = load_manifest(manifest_path)
    manifest = clear_missing_audio_entries(manifest)
    save_manifest(manifest_path, manifest)

    if all_sections_have_audio(manifest):
        print("[SKIP] TTS: todas las secciones ya tienen audio")
        return

    print("[RUN] TTS: generando/reutilizando audios...")
    # Si tu generate_tts() ya busca el manifest más reciente, puedes llamarlo tal cual.
    # Mejor: pasarle manifest_path para hacerlo explícito e idempotente.
    generate_tts_for_manifest(manifest_path)  # usa tu generate_tts.py

def obtener_datos_liturgicos_dominicos():
    iso_date = date.today().strftime("%Y-%m-%d")
    out = generate_manifest_from_dominicos(iso_date, Path("data"))
    print(f"Manifest generado: {out}")

def add_prayers_to_manifest():
    date_iso = datetime.now().strftime("%Y-%m-%d")
    manifest_path = Path(f"data/manifests/manifest-{date_iso}.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = upsert_prayers_into_manifest(
        manifest,
        prayers_templates_path=Path("data/templates/prayers_es.json")
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("OK: prayers añadidos al manifest")

def generate_tts():
    manifests_dir = Path("data/manifests")
    date_iso = datetime.now().strftime("%Y-%m-%d")
    files = sorted(manifests_dir.glob(f"manifest-{date_iso}.json"), reverse=True)
    if not files:
        raise FileNotFoundError("No encontré manifests en data/manifests.")

    generate_tts_for_manifest(files[0])
async def main():
    mp = manifest_path_for_today()
    ensure_dominicos_manifest(mp)
    if not mp.exists():
        raise FileNotFoundError(f"No se generó el manifest esperado: {mp}")
    ensure_prayers(mp)
    ensure_gemini_sections(mp)
    ensure_tts(mp)
   
if __name__ == "__main__":
    asyncio.run(main())  
