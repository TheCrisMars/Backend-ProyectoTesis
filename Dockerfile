FROM python:3.11-slim

# Variables de entorno para Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app


# Actualizar pip, wheel y setuptools
RUN pip install --upgrade pip wheel setuptools

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar la aplicación completa
COPY . .

# Exponer el puerto 5000 para WebSocket + API Flask
EXPOSE 5000

# Ejecutar el servidor con Gevent WebSocket puro
CMD ["python", "back.py"]
