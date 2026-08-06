FROM python:3.11-slim

WORKDIR /app

# Dependencias primero (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Crear directorio de imágenes estáticas
RUN mkdir -p /app/static/imagenes

# Sin --reload en producción
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]