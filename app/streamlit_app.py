import json
import base64
import google.auth
import streamlit as st
import streamlit.components.v1 as components
import os
from pathlib import Path
from datetime import datetime, timedelta, date
from google.auth import impersonated_credentials
from dotenv import load_dotenv
from google.cloud import storage
from typing import Optional

load_dotenv()

# -----------------------------
# Paths (local assets)
# -----------------------------
MANIFESTS_DIR = Path("data/manifests")

BASE_DIR = Path(__file__).resolve().parent  # .../app/app

def resolve_ui_dir() -> Path:
    # Tu estructura real:
    candidates = [
        BASE_DIR / "data" / "ui",                 # /app/app/data/ui  ✅
        Path("app/data/ui"),                      # si CWD es repo root
        Path("data/ui"),                          # si CWD es /app/app
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]

UI_DIR = resolve_ui_dir()

LOGO_PATH = UI_DIR / "logo.png"
FAVICON_PATH = UI_DIR / "favicon.png"
BG_PRIEST_PATH = UI_DIR / "bg_priest.jpg"
BG_LECTOR_PATH = UI_DIR / "bg_lector.jpg"

# -----------------------------
# Env / Modes
# -----------------------------
STORAGE_MODE = os.getenv("STORAGE_MODE", "local").strip().lower()  # local | gcs

GCS_BUCKET = os.getenv("GCS_BUCKET", "").strip()
GCS_PREFIX = os.getenv("GCS_PREFIX", "").strip().strip("/")  # ej: "missa"
SIGNED_URL_MINUTES = int(os.getenv("SIGNED_URL_MINUTES", "1440"))  # 24h default
SIGNER_SA_EMAIL = os.getenv("SIGNER_SA_EMAIL", "").strip()

# -----------------------------
# Helpers
# -----------------------------
def normalize_bg(p: Path) -> Path:
    if p.exists():
        return p
    if p.suffix.lower() in (".jpg", ".jpeg"):
        alt = p.with_suffix(".png")
        if alt.exists():
            return alt
    if p.suffix.lower() == ".png":
        alt = p.with_suffix(".jpg")
        if alt.exists():
            return alt
    return p

def read_file_b64(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif ext == ".png":
        mime = "image/png"
    elif ext == ".wav":
        mime = "audio/wav"
    else:
        mime = "application/octet-stream"
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    return mime, b64

def load_manifest_local(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def list_manifest_dates_local(manifests_dir: Path) -> list[tuple[str, Path]]:
    items = []
    for p in manifests_dir.glob("manifest-*.json"):
        stem = p.stem
        if not stem.startswith("manifest-"):
            continue
        date_str = stem.replace("manifest-", "")
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            items.append((date_str, p))
        except ValueError:
            continue
    items.sort(key=lambda x: x[0], reverse=True)
    return items

def role_for_section(sec: dict) -> str:
    audio = sec.get("audio") or {}
    role = (audio.get("role") or "").strip().upper()
    return role if role in ("PRIEST", "LECTOR") else "PRIEST"

def section_label(sec: dict) -> str:
    return (sec.get("title") or sec.get("id") or "Sección").strip()

def is_playable_local(sec: dict) -> bool:
    if not (sec.get("text") or "").strip():
        return False
    audio = sec.get("audio") or {}
    return bool((audio.get("path") or "").strip())

def is_playable_gcs(sec: dict) -> bool:
    if not (sec.get("text") or "").strip():
        return False
    # En GCS no dependemos de "path" local. Usamos convención <sec_id>.wav
    sec_id = (sec.get("id") or "").strip()
    return bool(sec_id)

# -----------------------------
# GCS helpers (list/read/sign)
# -----------------------------
def gcs_client() -> storage.Client:
    return storage.Client()

def gcs_manifest_blob(date_str: str) -> str:
    # gs://bucket/<prefix>/manifests/<date>/manifest-<date>.json
    return f"{GCS_PREFIX}/manifests/{date_str}/manifest-{date_str}.json"

def gcs_audio_blob(date_str: str, sec_id: str) -> str:
    # gs://bucket/<prefix>/audio/<date>/<sec_id>.wav
    return f"{GCS_PREFIX}/audio/{date_str}/{sec_id}.wav"

def list_manifest_dates_gcs(bucket: str) -> list[str]:
    """
    Lista fechas disponibles buscando manifests en:
      <prefix>/manifests/<YYYY-MM-DD>/manifest-YYYY-MM-DD.json
    """
    client = gcs_client()
    b = client.bucket(bucket)
    prefix = f"{GCS_PREFIX}/manifests/"
    dates = set()

    # Listado por prefijo; luego filtramos por patrón.
    for blob in client.list_blobs(b, prefix=prefix):
        name = blob.name  # missa/manifests/2026-01-08/manifest-2026-01-08.json
        parts = name.split("/")
        if len(parts) < 4:
            continue
        # parts: [prefix, 'manifests', 'YYYY-MM-DD', 'manifest-YYYY-MM-DD.json']
        date_str = parts[-2]
        file_name = parts[-1]
        if file_name == f"manifest-{date_str}.json":
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
                dates.add(date_str)
            except ValueError:
                pass

    return sorted(dates, reverse=True)

def load_manifest_gcs(bucket: str, blob_name: str) -> dict:
    client = gcs_client()
    b = client.bucket(bucket)
    blob = b.blob(blob_name)
    raw = blob.download_as_text(encoding="utf-8")
    return json.loads(raw)

def get_signing_credentials(target_sa_email: str):
    source_creds, _ = google.auth.default()
    return impersonated_credentials.Credentials(
        source_credentials=source_creds,
        target_principal=target_sa_email,
        target_scopes=["https://www.googleapis.com/auth/devstorage.read_only"],
        lifetime=3600,
    )

def _get_impersonated_storage_creds(target_sa_email: str, lifetime_seconds: int = 3600):
    """
    Usa ADC (Cloud Run) + IAMCredentials para firmar sin key file.
    Requiere roles/iam.serviceAccountTokenCreator sobre el SA.
    """
    source_creds, _ = google.auth.default()
    return impersonated_credentials.Credentials(
        source_credentials=source_creds,
        target_principal=target_sa_email,
        target_scopes=["https://www.googleapis.com/auth/devstorage.read_only"],
        lifetime=lifetime_seconds,
    )

def signed_gcs_url(bucket: str, blob: str, minutes: Optional[int] = None) -> str:
    """
    Genera Signed URL V4 para un objeto en GCS.
    Toma el Service Account firmante desde SIGNER_SA_EMAIL.
    """
    if not SIGNER_SA_EMAIL:
        raise RuntimeError(
            "Falta la variable de entorno SIGNER_SA_EMAIL (service account para firmar URLs)."
        )

    exp_minutes = minutes if minutes is not None else SIGNED_URL_MINUTES

    creds = _get_impersonated_storage_creds(SIGNER_SA_EMAIL, lifetime_seconds=min(3600, exp_minutes * 60))
    client = storage.Client(credentials=creds)
    bucket_obj = client.bucket(bucket)
    blob_obj = bucket_obj.blob(blob)

    return blob_obj.generate_signed_url(
        version="v4",
        expiration=timedelta(minutes=exp_minutes),
        method="GET",
        credentials=creds,
    )

# -----------------------------
# Page config (favicon)
# -----------------------------
if FAVICON_PATH.exists():
    page_icon = str(FAVICON_PATH)
else:
    page_icon = "⛪"

st.set_page_config(
    page_title="Missa - Tu Misa Virtual",
    page_icon=page_icon,
    layout="wide"
)

# -----------------------------
# Sidebar (logo + title + date selector)
# -----------------------------
if LOGO_PATH.exists():
    try:
      st.sidebar.image(str(LOGO_PATH), use_container_width=True)
    except TypeError:
      st.sidebar.image(str(LOGO_PATH), use_column_width=True)

st.sidebar.title("Missa")

# -----------------------------
# Load manifests (LOCAL or GCS)
# -----------------------------
if STORAGE_MODE == "gcs":
    if not GCS_BUCKET or not GCS_PREFIX:
        st.error("Faltan variables para GCS. Revisa: GCS_BUCKET y GCS_PREFIX.")
        st.stop()

    dates = list_manifest_dates_gcs(GCS_BUCKET)
    if not dates:
        st.error("No hay manifests en GCS. Aún no se han subido manifests al bucket.")
        st.stop()

    selected_date = st.sidebar.selectbox("Fecha", dates, index=0)
    manifest_blob = gcs_manifest_blob(selected_date)
    manifest = load_manifest_gcs(GCS_BUCKET, manifest_blob)

    sections = manifest.get("sections", [])
    playable = [s for s in sections if is_playable_gcs(s)]
else:
    manifests = list_manifest_dates_local(MANIFESTS_DIR)
    if not manifests:
        st.error("No hay manifests en data/manifests. Genera uno primero.")
        st.stop()

    dates = [d for d, _ in manifests]
    selected_date = st.sidebar.selectbox("Fecha", dates, index=0)
    manifest_path = dict(manifests)[selected_date]

    manifest = load_manifest_local(manifest_path)
    sections = manifest.get("sections", [])
    playable = [s for s in sections if is_playable_local(s)]

if not playable:
    st.error("No hay secciones reproducibles para esta fecha.")
    st.stop()

# -----------------------------
# Backgrounds (local images)
# -----------------------------
bg_priest = normalize_bg(BG_PRIEST_PATH)
bg_lector = normalize_bg(BG_LECTOR_PATH)
bg_priest_mime, bg_priest_b64 = read_file_b64(bg_priest) if bg_priest.exists() else ("image/jpeg", "")
bg_lector_mime, bg_lector_b64 = read_file_b64(bg_lector) if bg_lector.exists() else ("image/jpeg", "")

# -----------------------------
# Build playlist (LOCAL embeds, GCS signed URLs)
# -----------------------------
playlist = []
missing = []

if STORAGE_MODE == "gcs":
    for s in playable:
        sec_id = (s.get("id") or "section").strip()
        blob_name = gcs_audio_blob(selected_date, sec_id)

        try:
            url = signed_gcs_url(GCS_BUCKET, blob_name)
        except Exception as e:
            missing.append(f"{blob_name} ({e})")
            continue

        playlist.append({
            "id": sec_id,
            "title": section_label(s),
            "role": role_for_section(s),
            "audio_url": url,
            "text": s.get("text", ""),
        })
else:
    for s in playable:
        audio_path = Path((s.get("audio") or {}).get("path"))
        if not audio_path.exists():
            missing.append(str(audio_path))
            continue

        mime, b64 = read_file_b64(audio_path)
        playlist.append({
            "id": s.get("id") or "section",
            "title": section_label(s),
            "role": role_for_section(s),
            "audio_mime": mime,
            "audio_b64": b64,
            "text": s.get("text", ""),
        })

if missing:
    st.warning("Algunos audios no se encontraron / no pudieron firmarse. Se omiten:")
    st.code("\n".join(missing))

if not playlist:
    st.error("No hay audios reproducibles (playlist vacía).")
    st.stop()

# -----------------------------
# Render UI
# -----------------------------
from datetime import datetime

try:
    date_obj = datetime.strptime(selected_date, "%Y-%m-%d")
    date_label = date_obj.strftime("%d de %B de %Y")
except Exception:
    date_label = selected_date

st.markdown(f"## Misa Virtual – {date_label}")

payload = {
    "date": selected_date,
    "playlist": playlist,
    "bg": {
        "PRIEST": {"mime": bg_priest_mime, "b64": bg_priest_b64},
        "LECTOR": {"mime": bg_lector_mime, "b64": bg_lector_b64},
    },
    "storage_mode": STORAGE_MODE,
}

payload_json = json.dumps(payload).replace("</", "<\\/")

html = f"""
<div id="appholly" style="position: relative; min-height: 70vh; border-radius: 14px; overflow: hidden;">
  <div id="bg" style="
      position:absolute; inset:0;
      background-size: cover;
      background-position: center;
      transform: scale(1.02);
      transition: opacity 250ms ease;
      opacity: 1;
    "></div>

  <div style="position:absolute; inset:0; background: rgba(0,0,0,.55);"></div>

  <div style="position:relative; z-index:2; padding: 28px;">
    <div style="display:flex; align-items: baseline; gap: 14px; flex-wrap: wrap;">
      <div id="title" style="font-size: 44px; font-weight: 800; color: #fff; line-height:1.05;">Sección</div>
      <div id="counter" style="font-size: 18px; opacity:.9; color:#ddd;">Sección</div>
    </div>

    <div style="margin-top: 10px; display:flex; gap: 10px; align-items:center;">
      <button id="prev" style="padding: 10px 14px; border-radius: 10px; border: 1px solid rgba(255,255,255,.25); background: rgba(0,0,0,.25); color:#fff; cursor:pointer;">← Anterior</button>
      <button id="next" style="padding: 10px 14px; border-radius: 10px; border: 1px solid rgba(255,255,255,.25); background: rgba(0,0,0,.25); color:#fff; cursor:pointer;">Siguiente →</button>
    </div>

    <div style="margin-top: 14px;">
      <audio id="player" controls style="width: 100%; border-radius: 999px;"></audio>
    </div>

    <div style="margin-top: 14px; padding: 14px; border-radius: 14px; border: 1px solid rgba(255,255,255,.14); background: rgba(0,0,0,.35); color:#fff;">
      <details>
        <summary style="cursor:pointer; font-weight:700;">Ver texto</summary>
        <div id="text" style="margin-top: 10px; line-height: 1.6; max-height: 38vh; overflow:auto; padding-right: 6px;"></div>
      </details>
    </div>
  </div>
</div>

<script>
const payload = {payload_json};
const playlist = payload.playlist || [];
const bgData = payload.bg || {{}};
const mode = payload.storage_mode || "local";

const elBg = document.getElementById("bg");
const title = document.getElementById("title");
const counter = document.getElementById("counter");
const text = document.getElementById("text");
const player = document.getElementById("player");
const btnPrev = document.getElementById("prev");
const btnNext = document.getElementById("next");

let idx = 0;

function bgForRole(role) {{
  const r = (role || "PRIEST").toUpperCase();
  const obj = bgData[r] || bgData["PRIEST"] || null;
  if (!obj || !obj.b64) return null;
  return `url("data:${{obj.mime}};base64,${{obj.b64}}")`;
}}

function setAudioSource(item) {{
  // GCS: URL firmada
  if (item.audio_url) {{
    player.src = item.audio_url;
    return;
  }}
  // Local: data URI
  if (item.audio_mime && item.audio_b64) {{
    player.src = `data:${{item.audio_mime}};base64,${{item.audio_b64}}`;
    return;
  }}
  player.removeAttribute("src");
}}

function render(i, autoplay=true) {{
  if (!playlist.length) return;

  idx = Math.max(0, Math.min(i, playlist.length - 1));
  const item = playlist[idx];

  title.textContent = item.title || "Sección";
  counter.textContent = `Sección ${{idx+1}} / ${{playlist.length}}`;
  text.textContent = item.text || "";

  const bgUrl = bgForRole(item.role || "PRIEST");
  if (bgUrl) elBg.style.backgroundImage = bgUrl;

  setAudioSource(item);

  // Forzar recarga del audio y autoplay cuando cambia sección
  player.load();
  if (autoplay) {{
    const p = player.play();
    if (p && p.catch) p.catch(() => {{
      // Autoplay puede ser bloqueado por el navegador hasta interacción del usuario
    }});
  }}
}}

function next(autoplay=true) {{
  if (idx < playlist.length - 1) {{
    render(idx + 1, autoplay);
  }}
}}

function prev(autoplay=true) {{
  if (idx > 0) {{
    render(idx - 1, autoplay);
  }}
}}

btnNext.addEventListener("click", () => next(true));
btnPrev.addEventListener("click", () => prev(true));

// Auto-advance al terminar
player.addEventListener("ended", () => {{
  if (idx < playlist.length - 1) {{
    next(true);
  }}
}});

// Init
render(0, true);
</script>
"""

components.html(html, height=760, scrolling=False)