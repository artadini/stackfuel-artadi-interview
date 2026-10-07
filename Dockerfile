FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pipeline.py ./
COPY pipeline_functions ./pipeline_functions
COPY data/coach_betreuungsliste.xlsx ./data/coach_betreuungsliste.xlsx

# Mount /app/data read-only for the workbook and /app/db for the database file (the only output).
RUN mkdir -p db/duckdb

CMD ["python", "pipeline.py"]