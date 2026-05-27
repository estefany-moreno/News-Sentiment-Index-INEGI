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


def _leer_hoja(gc) -> pd.DataFrame:
    """Lee noticias_80tokens desde Google Sheets."""
    return pd.DataFrame(gc.open_by_key(SHEET_KEY).worksheet(HOJA_IN).get_all_records())


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
    # 1. Cargar datos desde la hoja noticias_80tokens.
    gc = gspread.service_account(filename=CREDENTIALS)
    df = _leer_hoja(gc)
    print(f"Cargadas {len(df)} noticias desde la hoja {HOJA_IN}")

    # 2. Inferencia con BERT financiero en español.
    nlp = pipeline("sentiment-analysis", model="bardsai/finance-sentiment-es-base")
    t0 = perf_counter()  # Inicio del cronómetro para medir solo el etiquetado.
    preds = nlp(df["texto_input_80tokens"].fillna("").astype(str).tolist())
    dt = perf_counter() - t0  # Tiempo total de inferencia del transformer.
    print(f"Etiquetado completado en {dt:.2f}s ({dt / len(df):.3f}s/noticia)")

    # 3. Convertir etiquetas a clase numérica, confianza y score continuo con signo.
    label_map = {"negative": -1, "neutral": 0, "positive": 1}
    df["sentimiento"] = [label_map[p["label"]] for p in preds]
    df["confidence"] = [p["score"] for p in preds]
    df["sentiment_score"] = df["confidence"] * df["sentimiento"]

    # 4. Guardar con sentimiento, confidence y sentiment_score como primeras columnas.
    cols_prim = ["sentimiento", "confidence", "sentiment_score"]
    df = df[cols_prim + [c for c in df.columns if c not in cols_prim]]
    _guardar_hoja(gc, df)
    print(f"Guardadas {len(df)} noticias en la hoja {HOJA_OUT}")


if __name__ == "__main__":
    main()
