FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e .

ENV ALPRO_HOME=/data
VOLUME ["/data"]
EXPOSE 8000

# İlk açılışta şema + demo portföy (varsa dokunmaz), sonra API
CMD ["sh", "-c", "python -m alpro init-db && python -m alpro demo && python -m alpro refresh; uvicorn alpro.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
