import asyncio
import os
import requests
import json
from datetime import datetime

# --- CONFIGURACIÓN ---
GOOGLE_API_KEY = "AIzaSyCcsgGy-ayxyd4AwmXhBqWZOHnid_LwZ5I"
HEYGEN_API_KEY = "sk_V2_hgu_kpWHE2H5QyD_rrnQIxMmX10JaCwkVl5hknothgLliNnc"
GEMINI_MODEL = "gemini-3-flash-preview"
AVATAR_ID_PROPIO = "393a8cf553814c3ca2cfcd633a97f1dd" 

def obtener_guion_liturgico():
    fecha_hoy = datetime.now().strftime("%d de %B de %Y")
    print(f"--- 1. Consultando Liturgia para hoy ({fecha_hoy}) ---")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GOOGLE_API_KEY}" 
    
    # Prompt optimizado para brevedad y ahorro de recursos
    prompt_query = (
        f"Hoy es {fecha_hoy}. Actúa como un experto en liturgia católica. "
        "Resume el evangelio de hoy en un guion de máximo 40 palabras (unas 3 frases). " # Reducido a 40 palabras
        "El tono debe ser solemne y pastoral. "
        "Responde estrictamente en formato JSON: "
        '{"guion": "texto del guion"}'
    )
    # ... resto del código [cite: 2]
    
    res = requests.post(url, json={"contents": [{"parts": [{"text": prompt_query}]}]}) 
    try:
        data = res.json()
        raw_text = data['candidates'][0]['content']['parts'][0]['text'] 
        clean_json = raw_text.replace('```json', '').replace('```', '').strip()
        return json.loads(clean_json)
    except:
        return None

def generar_video_heygen(contenido_json):
    print(f"--- 2. Enviando Paquete a HeyGen (ID: {AVATAR_ID_PROPIO}) ---")
    url = "https://api.heygen.com/v2/video/generate" 
    
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "x-api-key": HEYGEN_API_KEY 
    }
    
    # Esta es la estructura que la API solicita según tu mensaje de error
    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "talking_photo",
                    "talking_photo_id": AVATAR_ID_PROPIO # El error pedía específicamente este campo
                },
                "voice": {
                    "type": "text",
                    "input_text": contenido_json["guion"],
                    "voice_id": "4f0b1c9da53e47d7a045c238b87303c2"
                }
            }
        ],
        "dimension": {"width": 720, "height": 1280},
        "test": True
    }
    
    res = requests.post(url, json=payload, headers=headers) 
    
    if res.status_code == 200:
        video_id = res.json()["data"]["video_id"] 
        print(f"✅ ¡ÉXITO! Video solicitado. ID: {video_id}")
        return video_id
    else:
        print(f"❌ Error en HeyGen: {res.text}")
        return None

async def main():
    liturgia = obtener_guion_liturgico()
    if liturgia:
        print(f"✨ Guion: {liturgia['guion']}")
        generar_video_heygen(liturgia)

if __name__ == "__main__":
    asyncio.run(main())
