#!/bin/bash
set -e

echo "Starting Magistrate Framework Orchestration..."

# Ensure we're in the repository root
cd "$(dirname "$0")"

# Kill existing session if it exists to ensure a fresh start
tmux kill-session -t magistrate-core 2>/dev/null || true

# 1. Start the core tmux session with the Backend (Daphne)
echo "Booting Daphne Backend..."
tmux new-session -d -s magistrate-core -n "backend" "cd backend && source venv/bin/activate && daphne -b 0.0.0.0 -p 8001 og_broker.asgi:application"

# 2. Start the Expo Frontend in a new window
echo "Booting Expo Frontend..."
tmux new-window -t magistrate-core -n "frontend" "cd frontend && npm start"

# 3. Create the Dashboard window
echo "Loading Presidential Console..."
tmux new-window -t magistrate-core -n "dashboard" "cd backend && source venv/bin/activate && python ../cli/magistrate"

# Select the dashboard window as the primary focus
tmux select-window -t magistrate-core:dashboard

echo "Initialization Complete. Attaching to Session..."
sleep 1
tmux attach -t magistrate-core
