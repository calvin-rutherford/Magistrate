#!/bin/bash
set -e

echo "Starting Magistrate Server Provisioning (Stage 2)..."

TARGET_USER="spectre"
TARGET_HOME="/home/$TARGET_USER"

# 1. Update and install OS dependencies
echo "Installing core dependencies (tmux, curl, git, nodejs)..."
sudo apt-get update
sudo apt-get install -y tmux curl git

# Install Node.js (Required for Firstmate toolchain)
if ! command -v node &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

# 2. Install OpenCode
echo "Installing OpenCode..."
if ! command -v opencode &> /dev/null; then
    curl -fsSL https://opencode.ai/install | bash
else
    echo "OpenCode already installed."
fi

# 3. Install Ollama and apply memory optimizations
echo "Setting up Ollama daemon..."
if ! command -v ollama &> /dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
fi

echo "Applying VRAM optimizations to Ollama systemd service..."
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo bash -c 'cat > /etc/systemd/system/ollama.service.d/override.conf <<EOF
[Service]
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_NUM_PARALLEL=1"
EOF'
sudo systemctl daemon-reload
sudo systemctl restart ollama

# 4. Pull Local Models
echo "Pulling Magistrate Execution Models..."
ollama pull hermes3:8b
ollama pull qwen2.5-coder:7b

# 5. Clone and Configure Firstmate
if [ ! -d "$TARGET_HOME/firstmate" ]; then
    echo "Cloning Firstmate repository..."
    git clone https://github.com/kunchenguid/firstmate.git "$TARGET_HOME/firstmate"
fi

echo "Generating opencode.json for local Hermes routing..."
cat > "$TARGET_HOME/firstmate/opencode.json" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "ollama/hermes3:8b",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama Local",
      "options": {
        "baseURL": "http://127.0.0.1:11434/v1"
      },
      "models": {
        "hermes3:8b": {
          "name": "Hermes 3 8B"
        }
      }
    }
  }
}
EOF

# 6. Setup Python Dependencies (using uv pip sync)
echo "Syncing Python dependencies..."
cd "$TARGET_HOME/Magistrate/backend"

if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.local/bin/env || source $HOME/.cargo/env || export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -d "venv" ]; then
    uv venv venv
fi

source venv/bin/activate
uv pip install -r requirements.txt

echo "Server provisioning complete!"
echo "IMPORTANT: Please run 'gh auth login' on the server to authenticate the GitHub CLI for Firstmate."
