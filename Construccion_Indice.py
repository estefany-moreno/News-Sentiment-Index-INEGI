import os

import gspread
import pandas as pd


# Configuración de Google Sheets: mismo archivo del flujo noticias → sentimiento → índices.
_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", os.path.join(_DIR, "credentials.json"))
SHEET_KEY = "1KcqVvr-NxAhzYxfIBv3_TXMnQiWCXaq9NFm3z-Ff_Hc"
HOJA_IN = "noticias_con_sentimiento"
HOJA_DIA = "indice_diario"
HOJA_MES = "indice_mensual"


def _leer_hoja(gc, hoja: str) -> pd.DataFrame:
    """Lee una hoja de Google Sheets como DataFrame."""
    return pd.DataFrame(gc.open_by_key(SHEET_KEY).worksheet(hoja).get_all_records())


def _guardar_hoja(gc, hoja: str, df: pd.DataFrame):
    """Crea o limpia una hoja y escribe encabezados + datos."""
    sheet = gc.open_by_key(SHEET_KEY)
    try:
        ws = sheet.worksheet(hoja)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=hoja, rows=1, cols=len(df.columns))
    valores = [df.columns.tolist()] + df.fillna("").astype(str).values.tolist()
    ws.clear()
    if len(valores) > ws.row_count:
        ws.add_rows(len(valores) - ws.row_count)
    if len(df.columns) > ws.col_count:
        ws.add_cols(len(df.columns) - ws.col_count)
    ws.update(values=valores, range_name="A1")


def main():
    # 1. Cargar noticias con sentimiento y preparar fecha/score numéricos.
    gc = gspread.service_account(filename=CREDENTIALS)
    df = _leer_hoja(gc, HOJA_IN)
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df["sentiment_score"] = pd.to_numeric(df["sentiment_score"], errors="coerce")
    df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce")

    # 2. Índice diario: promedio del sentimiento por día + calendario completo.
    dia_agg = (
        df.dropna(subset=["fecha", "sentiment_score"])
        .assign(dia=lambda d: d["fecha"].dt.date)
        .groupby("dia", as_index=False)
        .agg(sentiment_score=("sentiment_score", "mean"), n_noticias=("sentiment_score", "size"), confidence=("confidence", "mean"))
    )
    rango = pd.date_range(pd.to_datetime(dia_agg["dia"]).min(), pd.to_datetime(dia_agg["dia"]).max(), freq="D")
    df_dia = (
        dia_agg.assign(dia_dt=pd.to_datetime(dia_agg["dia"]))
        .set_index("dia_dt")[["sentiment_score", "n_noticias", "confidence"]]
        .reindex(rango)
        .assign(n_noticias=lambda d: d["n_noticias"].fillna(0).astype(int))
        .reset_index(names="dia_dt")
    )
    df_dia.insert(0, "dia", df_dia["dia_dt"].dt.date)
    df_dia = df_dia[["dia", "sentiment_score", "n_noticias", "confidence"]]
    _guardar_hoja(gc, HOJA_DIA, df_dia)

    # 3. Índice mensual: promedio ponderado por volumen de noticias.
    m = df_dia.dropna(subset=["sentiment_score"]).assign(
        mes=lambda d: pd.to_datetime(d["dia"]).dt.to_period("M"),
        score_w=lambda d: d["sentiment_score"] * d["n_noticias"],
        conf_w=lambda d: d["confidence"] * d["n_noticias"],
    )
    df_mes = m.groupby("mes", as_index=False).agg(
        sentiment_score_sum=("score_w", "sum"),
        n_noticias_total=("n_noticias", "sum"),
        n_dias=("sentiment_score", "size"),
        confidence_sum=("conf_w", "sum"),
    )
    df_mes["sentiment_score_pond"] = df_mes["sentiment_score_sum"] / df_mes["n_noticias_total"]
    df_mes["confidence_media"] = df_mes["confidence_sum"] / df_mes["n_noticias_total"]
    df_mes = df_mes[["mes", "sentiment_score_pond", "n_noticias_total", "n_dias", "confidence_media"]]
    _guardar_hoja(gc, HOJA_MES, df_mes)
    print(f"Guardados {len(df_dia)} días en {HOJA_DIA} y {len(df_mes)} meses en {HOJA_MES}.")


if __name__ == "__main__":
    main()
