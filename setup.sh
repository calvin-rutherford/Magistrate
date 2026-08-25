#!/bin/bash
set -e

echo "Starting Magistrate VPS Setup..."

# Ensure python3-venv, docker, and docker-compose are installed
if ! command -v docker &> /dev/null; then
    echo "Docker could not be found. Please install Docker."
    exit 1
fi

# Check for docker-compose (v1) or docker compose plugin (v2)
DOCKER_COMPOSE_CMD=""
if command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker-compose"
elif docker compose version &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker compose"
else
    echo "Neither docker-compose nor the docker compose plugin could be found. Please install it."
    exit 1
fi

echo "Setting up Python Virtual Environment for the TUI Dashboard..."
python3 -m venv venv
source venv/bin/activate

echo "Installing TUI Dependencies locally..."
pip install -r requirements-cli.txt
pip install -e .

echo "Setting up Environment Variables..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example. PLEASE EDIT .env TO ADD YOUR API KEY."
fi

echo "Building and Starting the entire Magistrate Engine in Docker..."
$DOCKER_COMPOSE_CMD up --build -d

echo "Waiting for the Broker to be ready..."
sleep 15

echo "Running Django Migrations inside the Broker container..."
$DOCKER_COMPOSE_CMD exec broker python backend/manage.py migrate

echo "Setup Complete!"
echo "--------------------------------------------------------"
echo "The Database, Message Queue, WebSockets Server, and AI Workers"
echo "are now all running silently in the background via Docker!"
echo ""
echo "To launch your fleet dashboard, simply run:"
echo "  source venv/bin/activate && magistrate top"
echo "--------------------------------------------------------"
