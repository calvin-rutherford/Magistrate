#!/bin/bash

echo "Starting Magistrate Government Services..."

# Ensure we're in the right directory
cd /home/spectre/Magistrate

# Start Django Backend in a detached tmux session using Daphne (ASGI for WebSockets)
tmux new-session -d -s magistrate-backend "cd backend && source venv/bin/activate && daphne -b 0.0.0.0 -p 8000 og_broker.asgi:application"

# Start Expo Frontend in a detached tmux session
# Note: Expo requires Node.js. If Node isn't installed, it should be installed via nvm.
tmux new-session -d -s magistrate-frontend "cd frontend && npm start"

echo "Magistrate started!"
echo "Use 'tmux attach -t magistrate-backend' to view backend logs."
echo "Use 'tmux attach -t magistrate-frontend' to view Expo QR code for your phone."
