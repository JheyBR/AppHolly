from __future__ import annotations
import json
import re
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional

BASE_DIR = Path(__file__).resolve().parent  # /app/app
DEFAULT_TEMPLATES_PATH = BASE_DIR / "data" / "templates" / "prayers_es.json"

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

def normalize_text(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def load_prayers_templates(path: Path) -> Dict[str, Dict[str, Any]]:
    """
    Retorna dict por id: confiteor/creed/lords_prayer -> {id,type,title,text}
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    templates = data.get("templates", [])
    return {t["id"]: t for t in templates}

def template_to_manifest_section(tpl: Dict[str, Any]) -> Dict[str, Any]:
    text = normalize_text(tpl["text"])
    text_hash = sha256_hex(f"{tpl['id']}|{tpl['type']}|{text}")
    return {
        "id": tpl["id"],
        "type": tpl["type"],
        "title": tpl["title"],
        "source_url": f"template:catholic_prayer:{tpl['id']}",
        "text": text,
        "text_hash": text_hash,
        "audio": None,
        "premium": {"video": None, "video_hash": None},
    }

def upsert_prayers_into_manifest(
    manifest: Dict[str, Any],
    prayers_templates_path: Optional[Path] = None,
) -> Dict[str, Any]:
    
    if prayers_templates_path is None:
        prayers_templates_path = DEFAULT_TEMPLATES_PATH
    else:
        prayers_templates_path = Path(prayers_templates_path)
        if not prayers_templates_path.is_absolute():
            prayers_templates_path = (BASE_DIR / prayers_templates_path).resolve()

    templates = load_prayers_templates(prayers_templates_path)

    required_ids = ["confiteor", "creed", "lords_prayer"]
    for rid in required_ids:
        if rid not in templates:
            raise KeyError(f"Falta plantilla requerida en prayers_es.json: {rid}")

    # Index secciones existentes
    existing = {s["id"]: s for s in manifest.get("sections", [])}

    # Upsert oraciones fijas
    for rid in required_ids:
        existing[rid] = template_to_manifest_section(templates[rid])

    # Reordenar según ORDER
    new_sections: List[Dict[str, Any]] = []
    for sec_id in ORDER:
        s = existing.get(sec_id)
        if not s:
            continue
        if sec_id == "second_reading" and not s.get("text", "").strip():
            continue
        new_sections.append(s)

    # Mantén cualquier sección extra no contemplada en ORDER al final (opcional)
    extras = [s for sid, s in existing.items() if sid not in set(ORDER)]
    manifest["sections"] = new_sections + extras

    # Metadata opcional
    manifest.setdefault("templates", {})
    manifest["templates"]["prayers"] = {
        "source": str(prayers_templates_path).replace("\\", "/"),
        "included_ids": required_ids,
    }

    return manifest