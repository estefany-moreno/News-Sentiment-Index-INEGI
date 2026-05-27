# Concatena noticias de API DataBursatil, RSS El Economista y RSS Reforma desde Google Sheets.
# Columnas: sentimiento (vacía), titulo, fecha, descripcion, url, id_unico, fuente.
# Solo se guardan noticias únicas (sin repetidos por url, titulo y descripcion).

import os
import gspread
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", os.path.join(_DIR, "credentials.json"))
SHEET_KEY = "1KcqVvr-NxAhzYxfIBv3_TXMnQiWCXaq9NFm3z-Ff_Hc"
HOJA_RAW = "noticias_raw"
HOJA_SALIDA = "noticias"

COLUMNAS_SALIDA = ["sentimiento", "titulo", "fecha", "descripcion", "url", "id_unico", "fuente"]


def _google_client():
    """Autentica con Google Sheets usando credentials.json local."""
    return gspread.service_account(filename=CREDENTIALS)


def _leer_google_sheet(gc, spreadsheet_key: str) -> pd.DataFrame:
    """Lee la primera hoja de noticias_raw y añade sentimiento vacía; no deduplica."""
    registros = gc.open_by_key(spreadsheet_key).worksheet(HOJA_RAW).get_all_records()
    if not registros:
        return pd.DataFrame(columns=COLUMNAS_SALIDA)
    df = pd.DataFrame(registros)
    df["sentimiento"] = ""
    df["fuente"] = df.iloc[:, 1].astype(str).str.lower().map(
        lambda x: "Axis Negocios" if "axis" in x or "databursatil" in x else "El Economista" if "eleconomista" in x else "Reforma" if "reforma" in x else "Otro"
    )
    return df


def _filtrar_duplicados_y_ordenar(union: pd.DataFrame) -> pd.DataFrame:
    """Filtra duplicados por url, titulo y descripcion y ordena por fecha."""
    if union.empty:
        return pd.DataFrame(columns=COLUMNAS_SALIDA)
    out = union[COLUMNAS_SALIDA].drop_duplicates(subset=["url"], keep="first")
    out = out.drop_duplicates(subset=["titulo"], keep="first")
    out = out.drop_duplicates(subset=["descripcion"], keep="first")
    return out.sort_values(by="fecha")


def _guardar_google_sheet(gc, spreadsheet_key: str, df: pd.DataFrame):
    """Limpia la hoja de salida y escribe encabezados + noticias procesadas."""
    sheet = gc.open_by_key(spreadsheet_key)
    try:
        ws = sheet.worksheet(HOJA_SALIDA)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=HOJA_SALIDA, rows=1, cols=len(COLUMNAS_SALIDA))
    valores = [df.columns.tolist()] + df.fillna("").astype(str).values.tolist()
    ws.clear()
    if len(valores) > ws.row_count:
        ws.add_rows(len(valores) - ws.row_count)
    if len(df.columns) > ws.col_count:
        ws.add_cols(len(df.columns) - ws.col_count)
    ws.update(values=valores, range_name="A1")


def main():
    gc = _google_client()
    union_total = _leer_google_sheet(gc, SHEET_KEY)
    out = _filtrar_duplicados_y_ordenar(union_total)
    _guardar_google_sheet(gc, SHEET_KEY, out)
    _guardar_google_sheet_80tokens(gc, SHEET_KEY, out)
    print(f"Guardadas {len(out)} noticias únicas en Google Sheets (de {len(union_total)} filas en noticias_raw).")


def _guardar_google_sheet_80tokens(gc, spreadsheet_key: str, df: pd.DataFrame):
    """Guarda el texto recortado a 80 tokens en la hoja noticias_80tokens."""
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained("dccuchile/bert-base-spanish-wwm-cased")
    df = df.copy()

    # Unir titulo + descripcion (sin modificar la hoja noticias)
    df["texto_input"] = (df["titulo"].fillna("") + " " + df["descripcion"].fillna("")).str.strip()
    df["texto_input_80tokens"] = df["texto_input"].apply(
        lambda t: "" if pd.isna(t) or not str(t).strip()
        else tokenizer.decode(tokenizer(str(t), truncation=True, max_length=80)["input_ids"], skip_special_tokens=True)
    )
    df["n_tokens"] = df["texto_input_80tokens"].apply(
        lambda t: len(tokenizer.encode(t, add_special_tokens=True)) if str(t).strip() else 0
    )
    print("Tokens por fila (máx 80):")
    print(df["n_tokens"].describe())

    # Exportar dataset con texto recortado (sin titulo ni descripcion) a Google Sheets
    df_out = df.assign(sentimiento="")[["sentimiento", "texto_input_80tokens", "n_tokens", "fecha", "url", "id_unico"]]

    sheet = gc.open_by_key(spreadsheet_key)
    try:
        ws_out = sheet.worksheet("noticias_80tokens")
    except gspread.WorksheetNotFound:
        ws_out = sheet.add_worksheet(title="noticias_80tokens", rows=1, cols=len(df_out.columns))

    valores = [df_out.columns.tolist()] + df_out.fillna("").astype(str).values.tolist()
    ws_out.clear()
    if len(valores) > ws_out.row_count:
        ws_out.add_rows(len(valores) - ws_out.row_count)
    if len(df_out.columns) > ws_out.col_count:
        ws_out.add_cols(len(df_out.columns) - ws_out.col_count)
    ws_out.update(values=valores, range_name="A1")

    print(f"Guardadas {len(df_out)} noticias en la hoja noticias_80tokens")


if __name__ == "__main__":
    main()
