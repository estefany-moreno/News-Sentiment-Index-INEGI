import os
from time import perf_counter

import gspread
import pandas as pd
from transformers import pipeline


# Configuración de Google Sheets: mismo archivo donde vive noticias_80tokens.
_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", os.path.join(_DIR, "credentials.json"))
SHEET_KEY = "1KcqVvr-NxAhzYxfIBv3_TXMnQiWCXaq9NFm3z-Ff_Hc"
HOJA_IN = "noticias_80tokens"
HOJA_OUT = "noticias_con_sentimiento"


def _leer_hoja(gc, hoja: str = HOJA_IN) -> pd.DataFrame:
    """Lee una hoja desde Google Sheets."""
    try:
        registros = gc.open_by_key(SHEET_KEY).worksheet(hoja).get_all_records()
    except gspread.WorksheetNotFound:
        if hoja != HOJA_OUT:
            raise
        registros = []
    return pd.DataFrame(registros)


def _guardar_hoja(gc, df: pd.DataFrame):
    """Crea o limpia noticias_con_sentimiento y escribe el DataFrame final."""
    sheet = gc.open_by_key(SHEET_KEY)
    try:
        ws = sheet.worksheet(HOJA_OUT)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=HOJA_OUT, rows=1, cols=len(df.columns))
    valores = [df.columns.tolist()] + df.fillna("").astype(str).values.tolist()
    ws.clear()
    if len(valores) > ws.row_count:
        ws.add_rows(len(valores) - ws.row_count)
    if len(df.columns) > ws.col_count:
        ws.add_cols(len(df.columns) - ws.col_count)
    ws.update(values=valores, range_name="A1")


def main():
    # 1. Cargar noticias_80tokens y lo ya etiquetado en noticias_con_sentimiento.
    gc = gspread.service_account(filename=CREDENTIALS)
    df = _leer_hoja(gc)
    df_prev = _leer_hoja(gc, HOJA_OUT)
    print(f"Cargadas {len(df)} noticias desde la hoja {HOJA_IN}")

    ids_prev = set(df_prev["id_unico"].astype(str)) if len(df_prev) else set()
    nuevas = df[~df["id_unico"].astype(str).isin(ids_prev)]
    print(f"Nuevas a etiquetar: {len(nuevas)} (ya etiquetadas: {len(df) - len(nuevas)})")

    label_map = {"negative": -1, "neutral": 0, "positive": 1}
    cols_prim = ["sentimiento", "confidence", "sentiment_score"]

    if len(nuevas):
        # 2. Inferencia con BERT financiero en español (solo filas nuevas).
        nlp = pipeline("sentiment-analysis", model="bardsai/finance-sentiment-es-base")
        t0 = perf_counter()  # Inicio del cronómetro para medir solo el etiquetado.
        preds = nlp(nuevas["texto_input_80tokens"].fillna("").astype(str).tolist())
        dt = perf_counter() - t0  # Tiempo total de inferencia del transformer.
        print(f"Etiquetado completado en {dt:.2f}s ({dt / len(nuevas):.3f}s/noticia)")

        # 3. Convertir etiquetas a clase numérica, confianza y score continuo con signo.
        nuevas = nuevas.copy()
        nuevas["sentimiento"] = [label_map[p["label"]] for p in preds]
        nuevas["confidence"] = [p["score"] for p in preds]
        nuevas["sentiment_score"] = nuevas["confidence"] * nuevas["sentimiento"]

    # 4. Conservar etiquetas previas vigentes y unir con las nuevas; reordenar columnas.
    ids_in = set(df["id_unico"].astype(str))
    df_keep = df_prev[df_prev["id_unico"].astype(str).isin(ids_in)] if len(df_prev) else df_prev
    df = pd.concat([df_keep, nuevas], ignore_index=True)
    df = df[cols_prim + [c for c in df.columns if c not in cols_prim]]
    _guardar_hoja(gc, df)
    print(f"Guardadas {len(df)} noticias en la hoja {HOJA_OUT}")


if __name__ == "__main__":
    main()
