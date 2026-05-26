# Recolección unificada de noticias: DataBursatil API, RSS El Economista y RSS Reforma.

import os
import re
import time
import unicodedata
import requests
import feedparser
import gspread
import pandas as pd
from datetime import datetime
from uuid import uuid4
from urllib.parse import urlparse


# Configuración general: diccionarios locales + Google Sheets de salida.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json"))
SHEET_KEY = "1KcqVvr-NxAhzYxfIBv3_TXMnQiWCXaq9NFm3z-Ff_Hc"
HOJA_RAW = "noticias_raw"
COLUMNS = ["fecha", "fuente", "titulo", "descripcion", "contenido", "texto_input", "url", "fecha_recoleccion", "id_unico"]

# URLs de recolección.
API_BASE = "https://api.databursatil.com/v2/noticias"
RSS_ELECONOMISTA = "https://www.eleconomista.com.mx/rss/ultimas-noticias"
RSS_REFORMA = "https://www.reforma.com/rss/negocios.xml"
PATRON_PALABRA = re.compile(r"[a-z0-9]+(?:[-&][a-z0-9]+)*")


def normalizar_texto(s: str) -> str:
    """Minúsculas y sin acentos (para búsqueda insensible a tildes y mayúsculas)."""
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _cargar_diccionario(nombre: str, carpeta: str = "") -> set[str]:
    """Carga un diccionario separado por comas y normaliza sus términos."""
    ruta = os.path.join(BASE_DIR, carpeta, nombre)
    with open(ruta, encoding="utf-8") as f:
        return {normalizar_texto(t.strip()) for t in f.read().split(",") if t.strip()}


def cargar_diccionario_regiones() -> set[str]:
    """Carga los términos del diccionario de regiones (normalizados)."""
    return _cargar_diccionario("diccionario_regiones_mexico.txt", "Diccionarios")


def cargar_diccionario_entidades_indicadores() -> set[str]:
    """Carga los términos del diccionario de entidades e indicadores mexicanos (normalizados)."""
    return _cargar_diccionario("diccionario_entidades_indicadores_mexicanos.txt", "Diccionarios")


def cargar_diccionario_economia() -> set[str]:
    """Carga los términos del diccionario de economía actualizado (normalizados)."""
    return _cargar_diccionario("diccionario_economia_actualizado.txt", "Diccionarios")


def _palabras_normalizadas(texto: str, patron=PATRON_PALABRA) -> list[str]:
    """Extrae las palabras del texto normalizado."""
    return patron.findall(normalizar_texto(texto))


def noticia_coincide_terminos(titulo: str, texto: str, terminos: set[str], patron=PATRON_PALABRA) -> bool:
    """True si al menos un término aparece como palabra(s) completa(s) en titulo o texto."""
    words_list = _palabras_normalizadas(f"{titulo} {texto}", patron)
    words_set = set(words_list)
    for term in terminos:
        term_words = patron.findall(term)
        if not term_words:
            continue
        if (len(term_words) == 1 and term in words_set) or (
            len(term_words) > 1 and any(words_list[i : i + len(term_words)] == term_words for i in range(len(words_list) - len(term_words) + 1))
        ):
            return True
    return False


def fuente_desde_url(url: str) -> str:
    """Extrae el dominio como nombre de fuente a partir de la URL."""
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url or ""


def obtener_noticias_databursatil(token: str) -> list[dict]:
    """Consulta el endpoint de noticias de DataBursatil y devuelve la lista de noticias."""
    r = requests.get(API_BASE, params={"token": token}, timeout=30)
    r.raise_for_status()
    return r.json()


def noticias_api_a_dataframe(noticias: list[dict], terminos_regiones: set[str], terminos_entidades: set[str]) -> pd.DataFrame:
    """Convierte la respuesta de DataBursatil a DataFrame con filtros de regiones/entidades."""
    ahora, filas = datetime.now().strftime("%Y-%m-%d %H:%M:%S"), []
    terminos = terminos_regiones | terminos_entidades
    patron_simple = re.compile(r"[a-z0-9]+")
    for item in noticias:
        titulo, texto = (item.get("n") or "").strip(), (item.get("c") or "").strip()
        if not noticia_coincide_terminos(titulo, texto, terminos, patron_simple):
            continue
        url, palabras = (item.get("f") or "").strip(), texto.split()
        descripcion, contenido = (texto, "") if len(palabras) < 60 else (" ".join(palabras[:60]), texto)
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


def _quitar_html(texto: str) -> str:
    """Quita etiquetas HTML y normaliza espacios."""
    return " ".join(re.sub(r"<[^>]+>", " ", texto or "").split())


def _truncar_te_puede_interesar(texto: str) -> str:
    """Quita desde 'te puede interesar' hasta después del último bloque promocional."""
    if not texto:
        return ""
    m = re.search(r"\bte puede interesar\b", texto, re.IGNORECASE)
    if not m:
        return texto
    antes = re.sub(r"[\s#]+$", "", texto[: m.start()].rstrip())
    m2 = re.match(r"\s*(?:El Economista\s+###\s+[^#]+##\s+(?:(?!El Economista).)*\s*){1,2}(.*)", texto[m.end() :], re.DOTALL | re.IGNORECASE)
    resto = m2.group(1).strip() if m2 else ""
    return antes + (" " + resto if resto else "")


def _fecha_rss(entry) -> str:
    """Obtiene la fecha de publicación de la entrada en formato estándar."""
    if getattr(entry, "published_parsed", None):
        return time.strftime("%Y-%m-%d %H:%M:%S", entry.published_parsed)
    if getattr(entry, "updated_parsed", None):
        return time.strftime("%Y-%m-%d %H:%M:%S", entry.updated_parsed)
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def obtener_entradas_rss(url: str) -> list:
    """Descarga y parsea el RSS; devuelve la lista de entradas."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:91.0) Gecko/20100101 Firefox/91.0", "Accept": "application/rss+xml, application/xml, text/xml, */*"}
    r = requests.get(url, timeout=30, headers=headers)
    r.raise_for_status()
    return getattr(feedparser.parse(r.content), "entries", [])


def _extraer_contenido_entry(entry) -> str:
    """Extrae el texto de <content:encoded> de la entrada RSS."""
    content = entry.get("content") or []
    if content and isinstance(content, list):
        return ((content[0].get("value") if isinstance(content[0], dict) else "") or "").strip()
    return (entry.get("content_encoded") or "").strip()


def entradas_eleconomista_a_dataframe(entries: list, terminos_regiones: set[str], terminos_entidades: set[str], terminos_economia: set[str]) -> pd.DataFrame:
    """Convierte RSS de El Economista a DataFrame con filtros región/entidad + economía."""
    ahora, filas = datetime.now().strftime("%Y-%m-%d %H:%M:%S"), []
    terminos_reg_ent = terminos_regiones | terminos_entidades
    for entry in entries:
        titulo = _quitar_html((entry.get("title") or "").strip())
        desc = _truncar_te_puede_interesar(_quitar_html(entry.get("summary") or entry.get("description") or "").strip())
        cont = _truncar_te_puede_interesar(_quitar_html(_extraer_contenido_entry(entry)).strip())
        if not (titulo or desc or cont):
            continue
        ok_reg = noticia_coincide_terminos(titulo, desc, terminos_reg_ent)
        ok_eco = noticia_coincide_terminos(titulo, desc, terminos_economia)
        if not (ok_reg and ok_eco):
            lead = f"{desc} {' '.join(cont.split()[:80])}".strip()
            ok_reg = ok_reg or noticia_coincide_terminos(titulo, lead, terminos_reg_ent)
            ok_eco = ok_eco or noticia_coincide_terminos(titulo, lead, terminos_economia)
        if ok_reg and ok_eco:
            filas.append({
                "fecha": _fecha_rss(entry),
                "fuente": "www.eleconomista.com.mx",
                "titulo": titulo,
                "descripcion": desc,
                "contenido": cont,
                "texto_input": f"{titulo} {desc}{cont}".strip(),
                "url": (entry.get("link") or "").strip(),
                "fecha_recoleccion": ahora,
                "id_unico": str(uuid4()),
            })
    return pd.DataFrame(filas, columns=COLUMNS)


def entradas_reforma_a_dataframe(entries: list, terminos_regiones: set[str], terminos_entidades: set[str]) -> pd.DataFrame:
    """Convierte RSS de Reforma a DataFrame con filtros de regiones/entidades."""
    ahora, filas = datetime.now().strftime("%Y-%m-%d %H:%M:%S"), []
    terminos = terminos_regiones | terminos_entidades
    for entry in entries:
        titulo = _quitar_html((entry.get("title") or "").strip())
        texto = _quitar_html(entry.get("summary") or entry.get("description") or "").strip()
        if not noticia_coincide_terminos(titulo, texto, terminos):
            continue
        palabras = texto.split()
        descripcion, contenido = (texto, "") if len(palabras) < 100 else (" ".join(palabras[:100]), texto)
        filas.append({
            "fecha": _fecha_rss(entry),
            "fuente": "www.reforma.com",
            "titulo": titulo,
            "descripcion": descripcion,
            "contenido": contenido,
            "texto_input": f"{titulo} {descripcion}{contenido}".strip(),
            "url": (entry.get("link") or "").strip(),
            "fecha_recoleccion": ahora,
            "id_unico": str(uuid4()),
        })
    return pd.DataFrame(filas, columns=COLUMNS)


def guardar_en_google_sheets(df: pd.DataFrame):
    """Agrega las noticias recolectadas, ordenadas por fecha, después de la última fila escrita."""
    gc = gspread.service_account(filename=CREDENTIALS)
    sheet = gc.open_by_key(SHEET_KEY)
    try:
        ws = sheet.worksheet(HOJA_RAW)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=HOJA_RAW, rows=1, cols=len(COLUMNS))
    if not ws.get_all_values():
        ws.update(values=[COLUMNS], range_name="A1")
    valores = df.fillna("").astype(str).values.tolist()
    ultima_fila = len(ws.get_all_values())
    if ultima_fila + len(valores) > ws.row_count:
        ws.add_rows(ultima_fila + len(valores) - ws.row_count)
    if len(COLUMNS) > ws.col_count:
        ws.add_cols(len(COLUMNS) - ws.col_count)
    if valores:
        ws.update(values=valores, range_name=f"A{ultima_fila + 1}")


def main():
    # 1. Cargar diccionarios y recolectar las tres fuentes.
    terminos_regiones = cargar_diccionario_regiones()
    terminos_entidades = cargar_diccionario_entidades_indicadores()
    terminos_economia = cargar_diccionario_economia()
    token = os.getenv("DATABURSATIL_TOKEN")
    if not token:
        raise ValueError("Define DATABURSATIL_TOKEN en GitHub Actions Secrets.")

    df_api = noticias_api_a_dataframe(obtener_noticias_databursatil(token), terminos_regiones, terminos_entidades)
    df_ele = entradas_eleconomista_a_dataframe(obtener_entradas_rss(RSS_ELECONOMISTA), terminos_regiones, terminos_entidades, terminos_economia)
    df_ref = entradas_reforma_a_dataframe(obtener_entradas_rss(RSS_REFORMA), terminos_regiones, terminos_entidades)

    # 2. Unir resultados y ordenar de la noticia más antigua a la más reciente.
    df = pd.concat([df_api, df_ele, df_ref], ignore_index=True)
    df["_fecha_orden"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.sort_values("_fecha_orden", na_position="last").drop(columns="_fecha_orden")

    # 3. Agregar al final de la hoja noticias_raw.
    guardar_en_google_sheets(df)
    print(f"Agregadas {len(df)} noticias a {HOJA_RAW} (API={len(df_api)}, ElEconomista={len(df_ele)}, Reforma={len(df_ref)}).")


if __name__ == "__main__":
    main()
