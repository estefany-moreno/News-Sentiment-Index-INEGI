import os
import re
import unicodedata
import requests
import pandas as pd
from datetime import datetime
from uuid import uuid4
from urllib.parse import urlparse

# Configuración para API de GOOGLE DRIVE
# GOOGLE DRIVE - OAUTH
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Credenciales vía variables de entorno (GitHub Actions: Repository secrets).
# No incluir client_id, client_secret, refresh_token ni token de DataBursatil en el código ni en el repo.
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID_DATABURSATIL")
DATABURSATIL_TOKEN = os.getenv("DATABURSATIL_TOKEN")

# Valores que indican carpeta de Drive no configurada (evitan subir por error)
_PLACEHOLDERS_DRIVE_FOLDER = frozenset({"", "TU_FOLDER_ID_AQUI", "DRIVE_FOLDER_ID_DATABURSATIL"})
# Cadenas que no son un token real (evitan ejecutar con placeholder típico)
_TOKEN_PLACEHOLDERS = frozenset({"DATABURSATIL_TOKEN", "TOKEN_DE_DATA_BURSATIL"})

_missing = [k for k, v in {
    "GOOGLE_CLIENT_ID": CLIENT_ID,
    "GOOGLE_CLIENT_SECRET": CLIENT_SECRET,
    "GOOGLE_REFRESH_TOKEN": REFRESH_TOKEN,
    "DRIVE_FOLDER_ID_DATABURSATIL": DRIVE_FOLDER_ID,
    "DATABURSATIL_TOKEN": DATABURSATIL_TOKEN,
}.items() if not (v or "").strip()]
_tok = (DATABURSATIL_TOKEN or "").strip()
if _tok in _TOKEN_PLACEHOLDERS:
    _missing.append("DATABURSATIL_TOKEN")
if _missing or (DRIVE_FOLDER_ID or "").strip() in _PLACEHOLDERS_DRIVE_FOLDER:
    raise SystemExit(
        f"\n Faltan credenciales o DRIVE_FOLDER_ID inválido: {', '.join(dict.fromkeys(_missing)) or 'DRIVE_FOLDER_ID_DATABURSATIL'}"
    )


def get_drive_service():
    creds = Credentials(
        None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return build("drive", "v3", credentials=creds)


def upload_to_drive(service, local_file_path: str, drive_folder_id: str):
    file_metadata = {
        "name": os.path.basename(local_file_path),
        "parents": [drive_folder_id],
    }
    media = MediaFileUpload(local_file_path, mimetype="text/csv", resumable=True)

    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id",
    ).execute()

    print(f"Subido a Google Drive - ID: {file.get('id')}")
# =============================================================================


# Código para la recolección de noticias (DataBursatil - API)
API_BASE = "https://api.databursatil.com/v2/noticias"
COLUMNS = ["fecha", "fuente", "titulo", "descripcion", "contenido", "texto_input", "url", "fecha_recoleccion", "id_unico"]


def normalizar_texto(s: str) -> str:
    """Minúsculas y sin acentos (para búsqueda insensible a tildes y mayúsculas)."""
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def cargar_diccionario_regiones() -> set[str]:
    """Carga los términos del diccionario de regiones (normalizados: minúsculas y sin acentos)."""
    ruta = os.path.join(os.path.dirname(__file__), "Diccionarios", "diccionario_regiones_mexico.txt")
    with open(ruta, encoding="utf-8") as f:
        return {normalizar_texto(t.strip()) for t in f.read().split(",") if t.strip()}


def cargar_diccionario_entidades_indicadores() -> set[str]:
    """Carga los términos del diccionario de entidades e indicadores mexicanos (normalizados)."""
    ruta = os.path.join(os.path.dirname(__file__), "Diccionarios", "diccionario_entidades_indicadores_mexicanos.txt")
    with open(ruta, encoding="utf-8") as f:
        return {normalizar_texto(t.strip()) for t in f.read().split(",") if t.strip()}


def _palabras_normalizadas(texto: str) -> list[str]:
    """Extrae las palabras del texto normalizado (solo letras y dígitos, sin acentos)."""
    busqueda = normalizar_texto(texto)
    return re.findall(r"[a-z0-9]+", busqueda)


def noticia_coincide_regiones(titulo: str, texto: str, terminos: set[str]) -> bool:
    """True si al menos un término del diccionario aparece como palabra(s) completa(s) en titulo o texto.
    Un término de una palabra debe coincidir con una palabra entera; uno de varias con una secuencia consecutiva."""
    words_list = _palabras_normalizadas(f"{titulo} {texto}")
    words_set = set(words_list)
    for term in terminos:
        term_words = re.findall(r"[a-z0-9]+", term)
        if not term_words:
            continue
        if len(term_words) == 1:
            if term in words_set:
                return True
        else:
            n = len(term_words)
            if any(words_list[i : i + n] == term_words for i in range(len(words_list) - n + 1)):
                return True
    return False


def obtener_noticias(token: str) -> list[dict]:
    """Consulta el endpoint de noticias de DataBursatil y devuelve la lista de noticias."""
    r = requests.get(API_BASE, params={"token": token}, timeout=30)
    r.raise_for_status()
    return r.json()


def fuente_desde_url(url: str) -> str:
    """Extrae el dominio como nombre de fuente a partir de la URL."""
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url or ""


def noticias_a_dataframe(
    noticias: list[dict],
    terminos_regiones: set[str],
    terminos_entidades_indicadores: set[str],
) -> pd.DataFrame:
    """Convierte la respuesta de la API a DataFrame con las columnas requeridas.
    Solo se incluyen noticias donde aparezca al menos un término del diccionario de regiones
    o del diccionario de entidades e indicadores mexicanos.
    Descripcion siempre se llena: si el texto tiene < 60 palabras va todo ahí y contenido vacío;
    si tiene >= 60 palabras, descripcion = primeras 60 palabras y contenido = texto completo."""
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    filas = []
    terminos = terminos_regiones | terminos_entidades_indicadores
    for item in noticias:
        titulo = (item.get("n") or "").strip()
        texto = (item.get("c") or "").strip()
        if not noticia_coincide_regiones(titulo, texto, terminos):
            continue
        url = (item.get("f") or "").strip()
        palabras = texto.split()
        n_palabras = len(palabras)
        if n_palabras < 60:
            descripcion, contenido = texto, ""
        else:
            descripcion = " ".join(palabras[:60])
            contenido = texto
        filas.append({
            "fecha": ahora,
            "fuente": fuente_desde_url(url),
            "titulo": titulo,
            "descripcion": descripcion,
            "contenido": contenido,
            "texto_input": f"{titulo} {descripcion}{contenido}".strip(),
            "url": url,
            "fecha_recoleccion": ahora,
            "id_unico": str(uuid4()),
        })
    return pd.DataFrame(filas, columns=COLUMNS)


def main():
    # 1. Token DataBursatil ya validado al importar (DATABURSATIL_TOKEN)
    terminos_regiones = cargar_diccionario_regiones()
    terminos_entidades = cargar_diccionario_entidades_indicadores()
    noticias = obtener_noticias(DATABURSATIL_TOKEN)
    df = noticias_a_dataframe(noticias, terminos_regiones, terminos_entidades)

    # 2. Guardar localmente
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta = os.path.join(os.path.dirname(__file__), "Noticias_APIs")
    os.makedirs(carpeta, exist_ok=True)
    local_path = os.path.join(carpeta, f"noticias_api_{timestamp}.csv")
    df.to_csv(local_path, index=False, encoding="utf-8")
    print(f" Guardadas {len(df)} noticias en {local_path}")

    # 3. Subir a Google Drive
    folder_ok = DRIVE_FOLDER_ID.strip() not in _PLACEHOLDERS_DRIVE_FOLDER
    if folder_ok and len(df) > 0:
        try:
            service = get_drive_service()
            upload_to_drive(service, local_path, DRIVE_FOLDER_ID)
        except Exception as e:
            print(f"\n Error al subir a Drive: {e}")
    else:
        if not folder_ok:
            print("\n No se subió a Drive: DRIVE_FOLDER_ID_DATABURSATIL vacío o placeholder (revisa variables de entorno)")
        else:
            print("\n No se subió a Drive (DataFrame sin filas)")


if __name__ == "__main__":
    main()
