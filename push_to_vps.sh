#!/bin/bash

# This script runs continuously and uses rsync to detect 
# and sync any changed files directly to your VPS!

echo "🚀 Starting Magistrate Auto-Sync Watcher..."
echo "Monitoring for file changes. Press Ctrl+C to stop."
echo "----------------------------------------------------"

while true; do
  # We use rsync because it inherently detects which files have been modified 
  # based on timestamps and file sizes, so it only pushes the exact files you save!
  
  # We filter out the noisy boilerplate text from rsync so it only prints 
  # the names of the files that actually got synced.
  output=$(rsync -avz --exclude '.git' --exclude 'venv' --exclude '__pycache__' --exclude '*.pyc' -e "ssh -p 2222" . spectre@127.0.0.1:/home/spectre/Magistrate/ | grep -v 'building file list' | grep -v 'sent .* bytes' | grep -v 'total size is' | grep -v '^$')
  
  if [ -n "$output" ]; then
    # Format the output to fit on one line nicely
    clean_output=$(echo "$output" | tr '\n' ' ')
    echo "[$(date +%T)] Synced: $clean_output"
  fi
  
  # Wait 2 seconds before checking again
  sleep 2
done
