FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# NOTE: data/ is mounted as a volume at runtime (see docker-compose.yml),
# so the SQLite DB can be rebuilt without rebuilding the image.

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
