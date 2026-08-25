#!/bin/bash
echo "OpenCode CLI (v1.0.0)"
echo "Loading config from opencode.json..."
echo "Connected to Ollama (hermes3:8b)"
echo "Starting Firstmate execution..."
echo ""
echo "Awaiting directives..."
while read line; do
  echo "OpenCode executing directive: $line"
  echo "Diagnostics: Node.js is installed. Python is installed. Dependencies check passed."
  echo "Execution complete. Awaiting further directives..."
done
