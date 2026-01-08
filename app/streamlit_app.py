import json
import base64
from pathlib import Path
from datetime import datetime
import streamlit as st
import streamlit.components.v1 as components


MANIFESTS_DIR = Path("data/manifests")

BG_PRIEST = Path("data/ui/bg_priest.jpg")   # o .png
BG_LECTOR = Path("data/ui/bg_lector.jpg")   # o .png


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_manifest_dates(manifests_dir: Path) -> list[tuple[str, Path]]:
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


def is_playable(sec: dict) -> bool:
    if not (sec.get("text") or "").strip():
        return False
    audio = sec.get("audio") or {}
    return bool((audio.get("path") or "").strip())


def section_label(sec: dict) -> str:
    return (sec.get("title") or sec.get("id") or "Sección").strip()


def role_for_section(sec: dict) -> str:
    audio = sec.get("audio") or {}
    return (audio.get("role") or "PRIEST").upper()


def read_file_b64(path: Path) -> tuple[str, str]:
    """
    Returns (mime, base64_str)
    """
    if not path.exists():
        return ("", "")
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


st.set_page_config(page_title="AppHolly - Misa Virtual", layout="wide")

# Sidebar: Fecha
st.sidebar.title("AppHolly")
manifests = list_manifest_dates(MANIFESTS_DIR)
if not manifests:
    st.error("No hay manifests en data/manifests. Genera uno primero.")
    st.stop()

dates = [d for d, _ in manifests]
selected_date = st.sidebar.selectbox("Fecha", dates, index=0)
manifest_path = dict(manifests)[selected_date]

manifest = load_manifest(manifest_path)
sections = manifest.get("sections", [])
playable = [s for s in sections if is_playable(s)]

if not playable:
    st.warning("No hay secciones con audio para esta fecha. Ejecuta TTS y vuelve.")
    st.stop()

# Cargar backgrounds
bg_priest_path = normalize_bg(BG_PRIEST)
bg_lector_path = normalize_bg(BG_LECTOR)

bg_priest_mime, bg_priest_b64 = read_file_b64(bg_priest_path)
bg_lector_mime, bg_lector_b64 = read_file_b64(bg_lector_path)

if not bg_priest_b64 or not bg_lector_b64:
    st.warning("Faltan imágenes de fondo. Crea data/ui/bg_priest.jpg y data/ui/bg_lector.jpg para ver el efecto completo.")

# Construir playlist (cargamos audios en base64)
playlist = []
missing = []
for s in playable:
    audio_path = Path((s.get("audio") or {}).get("path"))
    if not audio_path.exists():
        missing.append(str(audio_path))
        continue

    mime, b64 = read_file_b64(audio_path)
    playlist.append({
        "id": s.get("id") or "section",
        "title": section_label(s),
        "role": role_for_section(s),  # PRIEST / LECTOR
        "audio_mime": mime,
        "audio_b64": b64,
        "text": s.get("text", ""),
    })

if missing:
    st.error("Hay audios referenciados en el manifest que no existen en disco:\n" + "\n".join(missing))
    st.stop()

# UI header (sin info técnica)
st.title("Misa Virtual")

# HTML player (single audio element + playlist + background swap)
# Nota: incluimos un botón "Iniciar" por restricciones de autoplay de navegadores.
# Después de iniciar una vez, el encadenamiento es automático.
payload = {
    "date": selected_date,
    "playlist": playlist,
    "bg": {
        "PRIEST": {"mime": bg_priest_mime, "b64": bg_priest_b64},
        "LECTOR": {"mime": bg_lector_mime, "b64": bg_lector_b64},
    }
}
payload_json = json.dumps(payload).replace("</", "<\\/")

html = """
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

  <div style="position:relative; z-index:2; padding: 28px; color: white; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;">
    <div id="title" style="font-size: 34px; font-weight: 800; line-height: 1.1; margin-bottom: 14px;">Cargando…</div>

    <div style="display:flex; gap: 10px; align-items:center; margin-bottom: 14px; flex-wrap: wrap;">
      <button id="startBtn" style="
          padding: 10px 16px; border-radius: 10px; border: 1px solid rgba(255,255,255,.25);
          background: rgba(255,255,255,.12); color: white; cursor: pointer; font-weight: 700;
        ">Iniciar</button>

      <button id="prevBtn" style="
          padding: 10px 14px; border-radius: 10px; border: 1px solid rgba(255,255,255,.20);
          background: rgba(255,255,255,.08); color: white; cursor: pointer; font-weight: 700;
        ">⟵ Anterior</button>

      <button id="nextBtn" style="
          padding: 10px 14px; border-radius: 10px; border: 1px solid rgba(255,255,255,.20);
          background: rgba(255,255,255,.08); color: white; cursor: pointer; font-weight: 700;
        ">Siguiente ⟶</button>

      <div id="counter" style="opacity:.85; font-weight: 600; margin-left: 6px;"></div>
    </div>

    <audio id="player" controls style="width: 100%;"></audio>

    <details style="margin-top: 14px;">
      <summary style="cursor:pointer; opacity:.9; font-weight:700;">Ver texto</summary>
      <div id="text" style="margin-top: 10px; line-height: 1.5; opacity:.95;"></div>
    </details>
  </div>
</div>

<script>
  const DATA = __PAYLOAD__;
  const playlist = DATA.playlist || [];
  let idx = 0;
  let started = false;

  const bg = document.getElementById("bg");
  const title = document.getElementById("title");
  const text = document.getElementById("text");
  const counter = document.getElementById("counter");
  const player = document.getElementById("player");
  const startBtn = document.getElementById("startBtn");
  const prevBtn = document.getElementById("prevBtn");
  const nextBtn = document.getElementById("nextBtn");

  function bgForRole(role) {
    const entry = (DATA.bg && DATA.bg[role]) || null;
    if (!entry || !entry.b64 || !entry.mime) return "";
    return `url("data:${entry.mime};base64,${entry.b64}")`;
  }

  function setBackground(role) {
    const bgUrl = bgForRole(role || "PRIEST");
    if (!bgUrl) return;
    // fade quick
    bg.style.opacity = 0.0;
    setTimeout(() => {
      bg.style.backgroundImage = bgUrl;
      bg.style.opacity = 1.0;
    }, 140);
  }

  function setTrack(i) {
    if (!playlist.length) return;

    idx = Math.max(0, Math.min(i, playlist.length - 1));
    const item = playlist[idx];

    title.textContent = item.title || "Sección";
    text.textContent = item.text || "";
    counter.textContent = `Sección ${idx+1} / ${playlist.length}`;

    setBackground(item.role || "PRIEST");

    const src = `data:${item.audio_mime};base64,${item.audio_b64}`;
    if (player.src !== src) {
      player.src = src;
      player.load();
    }

    // Enable/disable nav buttons at edges
    prevBtn.disabled = (idx === 0);
    nextBtn.disabled = (idx === playlist.length - 1);

    // Styling for disabled buttons
    prevBtn.style.opacity = prevBtn.disabled ? 0.5 : 1;
    nextBtn.style.opacity = nextBtn.disabled ? 0.5 : 1;
    prevBtn.style.cursor = prevBtn.disabled ? "not-allowed" : "pointer";
    nextBtn.style.cursor = nextBtn.disabled ? "not-allowed" : "pointer";
  }

  function tryPlay() {
    const p = player.play();
    if (p && p.catch) p.catch(() => {});
  }

  function goPrev() {
    if (idx <= 0) return;
    setTrack(idx - 1);
    if (started) tryPlay();
  }

  function goNext() {
    if (idx >= playlist.length - 1) return;
    setTrack(idx + 1);
    if (started) tryPlay();
  }

  // Init
  if (playlist.length > 0) {
    setTrack(0);
  } else {
    title.textContent = "No hay audios";
    counter.textContent = "";
    prevBtn.disabled = true;
    nextBtn.disabled = true;
  }

  startBtn.addEventListener("click", () => {
    started = true;
    tryPlay();
    startBtn.style.display = "none";
  });

  prevBtn.addEventListener("click", () => {
    goPrev();
  });

  nextBtn.addEventListener("click", () => {
    goNext();
  });

  // Auto-advance
  player.addEventListener("ended", () => {
    if (!started) return;
    if (idx < playlist.length - 1) {
      setTrack(idx + 1);
      tryPlay();
    } else {
      counter.textContent = `Finalizado (${playlist.length}/${playlist.length})`;
      // startBtn stays hidden; finished state
    }
  });
</script>
"""

html = html.replace("__PAYLOAD__", payload_json)
components.html(html, height=560)