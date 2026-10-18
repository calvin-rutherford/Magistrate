FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements-backend.txt /app/
RUN pip install --upgrade pip
RUN pip install -r requirements-backend.txt

# Copy project
COPY . /app/

# The default command will be overridden by docker-compose for each service
CMD ["uvicorn", "og_broker.asgi:application", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--http", "h11", "--ws", "websockets-sansio", "--lifespan", "off", "--no-proxy-headers", "--no-access-log"]
