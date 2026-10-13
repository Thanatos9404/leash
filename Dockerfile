# Single image: FastAPI backend serving the prebuilt React app from frontend/dist.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PAYPAL_ENV=sandbox
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY leash ./leash
COPY data ./data
COPY frontend/dist ./frontend/dist
RUN useradd --create-home leash && mkdir -p /app/var && chown -R leash /app/var
USER leash
EXPOSE 8000
CMD ["sh", "-c", "uvicorn leash.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
