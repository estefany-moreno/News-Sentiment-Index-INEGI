FROM python:3.12-slim

# HF_HOME: caché de modelos Hugging Face en ruta fija (se monta como volumen).
ENV HF_HOME=/models \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencias primero para aprovechar el cacheo de capas.
# requirements.txt ya define el --index-url de torch CPU.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Código, diccionarios y entrypoint.
COPY . .
RUN chmod +x entrypoint.sh

# El entrypoint reconstruye credentials.json desde una variable si hace falta.
ENTRYPOINT ["./entrypoint.sh"]

# Por defecto: pipeline completo en orden.
CMD ["bash", "-lc", "python Recoleccion_Noticias.py && python Preprocesamiento_Noticias.py && python Sentiment_Score.py && python Construccion_Indice.py"]
