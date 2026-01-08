import json
import re
import hashlib
from pathlib import Path
from datetime import datetime, date

import requests
import pdfplumber


# ---------------------------
# 1) URL del PDF (Dominicos)
# ---------------------------
def dominicos_pdf_url(iso_date: str) -> str:
    """
    iso_date: 'YYYY-MM-DD'
    Dominicos usa: /predicacion/pdf-evangelio-del-dia/D-M-YYYY.pdf (sin ceros)
    Ej: 2026-01-07 -> 7-1-2026
    """
    dt = datetime.strptime(iso_date, "%Y-%m-%d").date()
    dmy = f"{dt.day}-{dt.month}-{dt.year}"
    return f"https://www.dominicos.org/predicacion/pdf-evangelio-del-dia/{dmy}.pdf"


# ---------------------------
# 2) Descargar PDF
# ---------------------------
def download_pdf(url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "MisaVirtualBot/1.0 (+contact@your-domain.com)"}
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    out_path.write_bytes(r.content)


# ---------------------------
# 3) Extraer texto del PDF
# ---------------------------
def extract_text_from_pdf(pdf_path: Path) -> str:
    chunks = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                chunks.append(t)
    return "\n".join(chunks)


# ---------------------------
# 4) Normalización y hashing
# ---------------------------
def normalize_text(s: str) -> str:
    s = s.replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------
# 5) Parse por secciones
# ---------------------------
def _find_all(text: str, pattern: str):
    return [m.start() for m in re.finditer(pattern, text, flags=re.I)]

def _slice(text: str, start: int, end: int | None):
    if start is None:
        return ""
    if end is None:
        return text[start:].strip()
    return text[start:end].strip()

def extract_sections(text: str) -> dict:
    t = normalize_text(text)

    # Marcadores base (tal cual los vemos en el PDF extraído)
    i_first = t.lower().find("primera lectura")
    i_psalm = t.lower().find("salmo de hoy")
    i_second = t.lower().find("segunda lectura")

    # "Evangelio del día" aparece al menos 1 vez (cabecera),
    # y normalmente vuelve a aparecer para la sección del evangelio.
    ev_day_positions = _find_all(t, r"evangelio del día")
    if len(ev_day_positions) >= 2:
        i_ev_section = ev_day_positions[1]  # el segundo
    elif len(ev_day_positions) == 1:
        # fallback (si solo aparece una vez, úsalo como sección)
        i_ev_section = ev_day_positions[0]
    else:
        i_ev_section = -1

    # Inicio real del texto del evangelio
    i_ev_reading = t.lower().find("lectura del santo evangelio", max(i_ev_section, 0))

    # Fin del evangelio: antes de “Evangelio de hoy en vídeo” o “Reflexión…”
    end_markers = []
    for mk in ["evangelio de hoy en vídeo", "reflexión del evangelio de hoy"]:
        pos = t.lower().find(mk, max(i_ev_reading, 0))
        if pos != -1:
            end_markers.append(pos)
    i_ev_end = min(end_markers) if end_markers else None

    # Determinar límites por orden litúrgico
    out = {"first_reading": "", "psalm": "", "second_reading": "", "gospel": ""}

    # Primera lectura: desde "Primera lectura" hasta "Salmo de hoy"
    if i_first != -1 and i_psalm != -1 and i_psalm > i_first:
        out["first_reading"] = _slice(t, i_first, i_psalm)

    # Salmo: desde "Salmo de hoy" hasta "Segunda lectura" o hasta la sección del Evangelio
    if i_psalm != -1:
        next_cut = None
        if i_second != -1 and i_second > i_psalm:
            next_cut = i_second
        elif i_ev_section != -1 and i_ev_section > i_psalm:
            next_cut = i_ev_section
        out["psalm"] = _slice(t, i_psalm, next_cut)

    # Segunda lectura (si existe): desde "Segunda lectura" hasta sección del Evangelio
    if i_second != -1 and i_ev_section != -1 and i_ev_section > i_second:
        out["second_reading"] = _slice(t, i_second, i_ev_section)

    # Evangelio: desde "Lectura del santo evangelio…" hasta fin marker
    if i_ev_reading != -1:
        out["gospel"] = _slice(t, i_ev_reading, i_ev_end)
    else:
        # fallback: si no encuentra el inicio estándar, toma desde el segundo "Evangelio del día"
        if i_ev_section != -1:
            out["gospel"] = _slice(t, i_ev_section, i_ev_end)

    # Limpieza extra: remover encabezados sobrantes
    out["first_reading"] = re.sub(r"^primera lectura\s*", "", out["first_reading"], flags=re.I).strip()
    out["psalm"] = re.sub(r"^salmo de hoy\s*", "", out["psalm"], flags=re.I).strip()

    # Validación mínima: evangelio debe existir
    if not out["gospel"]:
        raise ValueError("No pude extraer la sección de Evangelio. Revisa marcadores del PDF.")

    return out


# ---------------------------
# 6) Construcción de manifest
# ---------------------------
def build_manifest(iso_date: str, source_url: str, sections: dict) -> dict:
    def section_obj(sec_id: str, sec_type: str, title: str, text: str) -> dict:
        text_n = normalize_text(text)
        text_hash = sha256_hex(f"{sec_id}|{sec_type}|{text_n}")
        return {
            "id": sec_id,
            "type": sec_type,
            "title": title,
            "source_url": source_url,
            "text": text_n,
            "text_hash": text_hash,
            # Audio/video se completarán en etapas posteriores
            "audio": None,
            "premium": {"video": None, "video_hash": None}
        }

    manifest = {
        "schema_version": "1.0",
        "date": iso_date,
        "language": "es-CO",   # Dominicos España; puedes usar es-CO en UI si quieres
        "title": "Misa Virtual",
        "source": {
            "provider": "dominicos.org",
            "pdf_url": source_url
        },
        "sections": []
    }

    if sections.get("first_reading"):
        manifest["sections"].append(section_obj("first_reading", "reading", "Primera lectura", sections["first_reading"]))
    if sections.get("psalm"):
        manifest["sections"].append(section_obj("psalm", "psalm", "Salmo responsorial", sections["psalm"]))
    if sections.get("second_reading"):
        manifest["sections"].append(section_obj("second_reading", "reading", "Segunda lectura", sections["second_reading"]))

    manifest["sections"].append(section_obj("gospel", "gospel", "Evangelio", sections["gospel"]))

    return manifest


def save_manifest(manifest: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    iso_date = manifest["date"]
    out_path = out_dir / f"manifest-{iso_date}.json"
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


# ---------------------------
# 7) Orquestador (una fecha)
# ---------------------------
def generate_manifest_from_dominicos(iso_date: str, workdir: Path) -> Path:
    pdf_url = dominicos_pdf_url(iso_date)
    pdf_path = workdir / "raw" / f"dominicos-{iso_date}.pdf"

    download_pdf(pdf_url, pdf_path)
    full_text = extract_text_from_pdf(pdf_path)
    secs = extract_sections(full_text)

    manifest = build_manifest(iso_date, pdf_url, secs)
    out_path = save_manifest(manifest, workdir / "manifests")
    return out_path

if __name__ == "__main__":
    iso = date.today().strftime("%Y-%m-%d")
    base = Path("data")
    out = generate_manifest_from_dominicos(iso, base)
    print(f"OK: {out}")