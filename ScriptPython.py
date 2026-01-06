import requests
import json
import asyncio
import time
from datetime import datetime

GOOGLE_API_KEY = "AIzaSyAhGB3YefNWEE79h2F9Xlb8H8O64eivJmw"
HEYGEN_API_KEY = "sk_V2_hgu_ktVynbBywX2_KyHK1Jw3DcZQtyGNLYNl31zltP1tY4xn"
GEMINI_MODEL = "gemini-2.5-flash"
AVATAR_ID_PROPIO = "9bb9494f46aa41c995a77de99280bda9" 

if not GOOGLE_API_KEY or not HEYGEN_API_KEY:
    raise ValueError("¡Error! Faltan las API KEYS en el archivo .env")

def obtener_datos_liturgicos():
    fecha_hoy = datetime.now().strftime("%d de %B de %Y")
    print(f"\n--- 1. Consultando Liturgia para hoy ({fecha_hoy}) ---")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GOOGLE_API_KEY}"
    prompt_query = (
        f"Hoy es {fecha_hoy}. Eres un experto en liturgia católica. "
        "Obtén el evangelio del día de hoy según la Biblia Católica, usando la traducción oficial completa y fiel. "
        "Incluye únicamente el texto íntegro del evangelio, sin interpretación ni resumen. "
        #"1. Lee el evangelio del dia completo con tono solemne y espiritual "
        "Responde ESTRICTAMENTE en este formato JSON, sin markdown extra: "
        #'{"guion": "texto hablado aquí"}'
        "Ejemplo: {\"guion\": \"Lectura del santo evangelio...\"}"
    )

    try:
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt_query}]}]})
        res.raise_for_status() # Verifica si hubo error HTTP
        data = res.json()
        raw_text = data['candidates'][0]['content']['parts'][0]['text']
        # Limpieza del JSON (quita ```json y ``` si Gemini los pone)
        clean_json_text = raw_text.replace('```json', '').replace('```', '').strip()
        parsed_json = json.loads(clean_json_text)
        print("\n --- Respuesta de Gemini  ---")
        print(f" Guion: {parsed_json.get('guion')}")
        print("-------------------------------\n")
        return parsed_json
       
    except Exception as e:
        print(f" Error: consultando a Gemini: {e}")
        return None

def generar_video_heygen(contenido_json):
    print(f"--- 2. Enviando Paquete a HeyGen (AVATAR ID: {AVATAR_ID_PROPIO}) ---")
    url = "https://api.heygen.com/v2/video/generate"
 
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "x-api-key": HEYGEN_API_KEY
    }

    # NOTA: Para cambiar el fondo dinámicamente en 'talking_photo',
    # la API generalmente requiere una URL de imagen, no una descripción de texto.
    # Por ahora, enviaremos solo el guion. Si tuvieras una URL de imagen, iría en 'background'.

    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "talking_photo",
                    "talking_photo_id": AVATAR_ID_PROPIO,
                    "scale": 1,
                    "avatar_style": "normal",
                    "talking_style": "stable"
                },
                "voice": {
                    "type": "text",
                    "input_text": contenido_json["guion"], # Usamos el guion extraído
                    "voice_id": "835561d576e04cb188580b4ada8dda5f"
                }
            }
        ],
        "dimension": {"width": 1280, "height": 720},
        "test": True
    }
  
    try:
        res = requests.post(url, json=payload, headers=headers)
        if res.status_code == 200:
            video_id = res.json()["data"]["video_id"]
            print(f"¡ÉXITO! Video solicitado. ID: {video_id}")
            return video_id
        else:
            print(f" Error en HeyGen: {res.text}")
            return None
    except Exception as e:
        print(f" Error de conexión con HeyGen: {e}")
        return None

def descargar_video_heygen(video_id):
    status_url = f"https://api.heygen.com/v1/video_status.get?video_id={video_id}"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "x-api-key": HEYGEN_API_KEY
    }

    print(f"Verificando estado del video {video_id}...")
    while True:
        # 2. Consultar el estado
        response = requests.get(status_url, headers=headers)
      
        if response.status_code != 200:
            print(f"Error en la API: {response.text}")  
            return
        data = response.json()
        status = data.get('data', {}).get('status')
      
        # 3. Evaluar el estado
        if status == 'completed':
            video_url = data['data']['video_url']
            print(f"¡Video completado! URL: {video_url}")
          
            # 4. Descargar el archivo
            print("Iniciando descarga...")
            video_content = requests.get(video_url).content
           
            nombre_archivo = f"{video_id}.mp4"
            with open(nombre_archivo, 'wb') as f:
                f.write(video_content)

            print(f"Video guardado exitosamente como: {nombre_archivo}")
            break
          
        elif status == 'failed':
            error = data['data'].get('error')
            print(f"La generación del video falló: {error}")
            break
          
        elif status in ['pending', 'processing']:
            print("El video se está procesando. Esperando 60 segundos...")
            time.sleep(60)  # Esperar antes de volver a consultar (Polling)
     
        else:
            print(f"Estado desconocido: {status}")
            break


async def main():
    datos = obtener_datos_liturgicos()
    if datos:        
        VIDEO_ID = generar_video_heygen(datos)
        if (VIDEO_ID != "None"):
            time.sleep(20)
            #VIDEO_ID = "f692954b2e2d4462bed949d00ac28cf3"
            descargar_video_heygen(VIDEO_ID)  # Reemplaza con el ID real del video generado
        else:
            print("No se pudo obtener el VIDEO_ID, abortando descarga de video.") 
    else:
        print("No se pudo obtener el guion, abortando generación de video.")    

if __name__ == "__main__":
    asyncio.run(main())  
