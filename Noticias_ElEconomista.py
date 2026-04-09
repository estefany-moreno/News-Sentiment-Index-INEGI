import os
import re
import time
import unicodedata
import requests
import feedparser
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

# Credenciales: archivo local ../Secrets/oauth_secrets.txt (no subir a git).
# En GitHub Actions puedes inyectar las mismas claves vía os.getenv (ejemplo comentado abajo).
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID_EL_ECONOMISTA")

# Valores que indican carpeta de Drive no configurada (evitan subir por error)
_PLACEHOLDERS_DRIVE_FOLDER = frozenset({"", "TU_FOLDER_ID_AQUI", "DRIVE_FOLDER_ID_EL_ECONOMISTA"})

_missing = [k for k, v in {
    "GOOGLE_CLIENT_ID": CLIENT_ID,
    "GOOGLE_CLIENT_SECRET": CLIENT_SECRET,
    "GOOGLE_REFRESH_TOKEN": REFRESH_TOKEN,
    "DRIVE_FOLDER_ID_EL_ECONOMISTA": DRIVE_FOLDER_ID,
}.items() if not (v or "").strip()]
if _missing or (DRIVE_FOLDER_ID or "").strip() in _PLACEHOLDERS_DRIVE_FOLDER:
    raise SystemExit(f"\n Faltan credenciales o DRIVE_FOLDER_ID inválido: {', '.join(_missing) or 'DRIVE_FOLDER_ID_EL_ECONOMISTA'}")


def get_drive_service():
    creds = Credentials(
        None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=['https://www.googleapis.com/auth/drive']
    )
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return build('drive', 'v3', credentials=creds)

def upload_to_drive(service, local_file_path: str, drive_folder_id: str):
    file_metadata = {
        'name': os.path.basename(local_file_path),
        'parents': [drive_folder_id]
    }
    media = MediaFileUpload(local_file_path, mimetype='text/csv', resumable=True)
    
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()
    
    print(f"Subido a Google Drive - ID: {file.get('id')}")
# =============================================================================


# Codigo original para la recolección de noticias
RSS_URL = "https://www.eleconomista.com.mx/rss/ultimas-noticias"
COLUMNS = ["fecha", "fuente", "titulo", "descripcion", "contenido", "texto_input", "url", "fecha_recoleccion", "id_unico"]
# Patrón de "palabra": letras/dígitos y, opcionalmente, segmentos unidos por - o & (ej. T-MEC, S&P como una sola palabra).
PATRON_PALABRA = re.compile(r"[a-z0-9]+(?:[-&][a-z0-9]+)*")


def normalizar_texto(s: str) -> str:
    """Minúsculas y sin acentos (para búsqueda insensible a tildes y mayúsculas)."""
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def cargar_diccionario_regiones() -> set[str]:
    """Carga los términos del diccionario de regiones (normalizados)."""
    ruta = os.path.join(os.path.dirname(__file__), "Diccionarios", "diccionario_regiones_mexico.txt")
    with open(ruta, encoding="utf-8") as f:
        return {normalizar_texto(t.strip()) for t in f.read().split(",") if t.strip()}


def cargar_diccionario_entidades_indicadores() -> set[str]:
    """Carga los términos del diccionario de entidades e indicadores mexicanos (normalizados)."""
    ruta = os.path.join(os.path.dirname(__file__), "Diccionarios", "diccionario_entidades_indicadores_mexicanos.txt")
    with open(ruta, encoding="utf-8") as f:
        return {normalizar_texto(t.strip()) for t in f.read().split(",") if t.strip()}


def cargar_diccionario_economia() -> set[str]:
    """Carga los términos del diccionario de economía actualizado (normalizados)."""
    ruta = os.path.join(os.path.dirname(__file__), "Diccionarios", "diccionario_economia_actualizado.txt")
    with open(ruta, encoding="utf-8") as f:
        return {normalizar_texto(t.strip()) for t in f.read().split(",") if t.strip()}


def _palabras_normalizadas(texto: str) -> list[str]:
    """Extrae las palabras del texto normalizado; permite - y & dentro de una palabra (ej. T-MEC, S&P)."""
    busqueda = normalizar_texto(texto)
    return PATRON_PALABRA.findall(busqueda)


def noticia_coincide_terminos(titulo: str, texto: str, terminos: set[str]) -> bool:
    """True si al menos un término aparece como palabra(s) completa(s) en titulo o texto."""
    words_list = _palabras_normalizadas(f"{titulo} {texto}")
    words_set = set(words_list)
    for term in terminos:
        term_words = PATRON_PALABRA.findall(term)
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


def _quitar_html(texto: str) -> str:
    """Quita etiquetas HTML y normaliza espacios."""
    if not texto:
        return ""
    sin_tags = re.sub(r"<[^>]+>", " ", texto)
    return " ".join(sin_tags.split())


def _truncar_te_puede_interesar(texto: str) -> str:
    """Quita desde 'te puede interesar' hasta después del último triplete (El Economista + ### + ##); conserva el resto."""
    if not texto:
        return ""
    m = re.search(r"\bte puede interesar\b", texto, re.IGNORECASE)
    if not m:
        return texto
    antes = re.sub(r"[\s#]+$", "", texto[: m.start()].rstrip())
    despues = texto[m.end() :]
    triple = r"El Economista\s+###\s+[^#]+##\s+(?:(?!El Economista).)*"
    m2 = re.match(r"\s*(?:" + triple + r"\s*){1,2}(.*)", despues, re.DOTALL | re.IGNORECASE)
    resto = m2.group(1).strip() if m2 else ""
    return antes + (" " + resto if resto else "")


def _fecha_rss(entry) -> str:
    """Obtiene la fecha de publicación de la entrada en formato estándar."""
    if getattr(entry, "published_parsed", None):
        return time.strftime("%Y-%m-%d %H:%M:%S", entry.published_parsed)
    if getattr(entry, "updated_parsed", None):
        return time.strftime("%Y-%m-%d %H:%M:%S", entry.updated_parsed)
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fuente_desde_url(url: str) -> str:
    """Extrae el dominio como nombre de fuente a partir de la URL."""
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url or ""


def obtener_entradas_rss(url: str) -> list:
    """Descarga y parsea el RSS; devuelve la lista de entradas (entries)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:91.0) Gecko/20100101 Firefox/91.0",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    r = requests.get(url, timeout=30, headers=headers)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    return getattr(feed, "entries", [])


def _extraer_contenido_entry(entry) -> str:
    """Extrae el texto de <content:encoded> de la entrada RSS."""
    content = entry.get("content") or []
    if content and isinstance(content, list) and len(content) > 0:
        val = content[0].get("value") if isinstance(content[0], dict) else ""
        return (val or "").strip()
    # Algunos feeds exponen content:encoded como content_encoded
    return (entry.get("content_encoded") or "").strip()


def entradas_a_dataframe(
    entries: list,
    terminos_regiones: set[str],
    terminos_entidades_indicadores: set[str],
    terminos_economia: set[str],
    nombre_fuente: str = "www.eleconomista.com.mx",
) -> pd.DataFrame:
    """Convierte las entradas RSS a DataFrame con las columnas requeridas.
    Filtros: al menos un término de regiones/entidades y uno de economía, buscando en título, descripción
    o (si ahí no se cumple) en título + descripción + primeras 80 palabras del contenido.
    Descripcion: texto de <description> del RSS sin HTML; contenido: texto de <content:encoded> sin HTML."""
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    filas = []
    terminos_reg_ent = terminos_regiones | terminos_entidades_indicadores
    for entry in entries:
        titulo = _quitar_html((entry.get("title") or "").strip())
        raw_desc = entry.get("summary") or entry.get("description") or ""
        raw_content = _extraer_contenido_entry(entry)
        desc_limpia = _truncar_te_puede_interesar(_quitar_html(raw_desc).strip())
        cont_limpio = _truncar_te_puede_interesar(_quitar_html(raw_content).strip())
        if not (titulo or desc_limpia or cont_limpio):
            continue
        ok_reg = noticia_coincide_terminos(titulo, desc_limpia, terminos_reg_ent)
        ok_eco = noticia_coincide_terminos(titulo, desc_limpia, terminos_economia)
        if not (ok_reg and ok_eco):
            primeras_80 = " ".join(cont_limpio.split()[:80])
            texto_lead = f"{desc_limpia} {primeras_80}".strip()
            if not ok_reg:
                ok_reg = noticia_coincide_terminos(titulo, texto_lead, terminos_reg_ent)
            if not ok_eco:
                ok_eco = noticia_coincide_terminos(titulo, texto_lead, terminos_economia)
        if not (ok_reg and ok_eco):
            continue
        url = (entry.get("link") or "").strip()
        descripcion = desc_limpia  # texto de <description> del RSS sin HTML
        contenido = cont_limpio  # texto de <content:encoded> sin HTML
        fecha_pub = _fecha_rss(entry)
        filas.append({
            "fecha": fecha_pub,
            "fuente": nombre_fuente,
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
    # 1. Cargar diccionarios y obtener noticias
    terminos_regiones = cargar_diccionario_regiones()
    terminos_entidades = cargar_diccionario_entidades_indicadores()
    terminos_economia = cargar_diccionario_economia()
    entries = obtener_entradas_rss(RSS_URL)
    
    df = entradas_a_dataframe(
        entries,
        terminos_regiones,
        terminos_entidades,
        terminos_economia,
        nombre_fuente="www.eleconomista.com.mx",
    )
    
    # 2. Guardar localmente 
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta = os.path.join(os.path.dirname(__file__), "Noticias_RSS")
    os.makedirs(carpeta, exist_ok=True)
    local_path = os.path.join(carpeta, f"noticias_eleconomista_rss_{timestamp}.csv")
    df.to_csv(local_path, index=False, encoding="utf-8")
    print(f" Guardadas {len(df)} noticias en {local_path}")
    
    # 3. SUBIR A GOOGLE DRIVE (¡la parte nueva!)
    folder_ok = DRIVE_FOLDER_ID.strip() not in _PLACEHOLDERS_DRIVE_FOLDER
    if folder_ok and len(df) > 0:
        try:
            service = get_drive_service()
            upload_to_drive(service, local_path, DRIVE_FOLDER_ID)
        except Exception as e:
            print(f"\n Error al subir a Drive: {e}")
    else:
        if not folder_ok:
            print("\n No se subió a Drive: DRIVE_FOLDER_ID_EL_ECONOMISTA vacío o placeholder en oauth_secrets.txt")
        else:
            print("\n No se subió a Drive (DataFrame sin filas)")

if __name__ == "__main__":
    main()