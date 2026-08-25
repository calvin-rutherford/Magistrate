import sys
import json
import requests
import os

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "hermes3:8b"

def load_agents_md():
    """Firstmate expects the harness to read AGENTS.md for instructions."""
    try:
        with open("AGENTS.md", "r") as f:
            return f.read()
    except FileNotFoundError:
        return "No AGENTS.md found. Act as a generic engineering assistant."

def run_hermes_inference(prompt):
    agents_context = load_agents_md()
    
    system_prompt = f"""You are the Magistrate Local Engineering Harness (Firstmate Crewmate).
Follow the rules defined in the project's AGENTS.md:
{agents_context}
"""

    payload = {
        "model": MODEL_NAME,
        "system": system_prompt,
        "prompt": prompt,
        "stream": False
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload)
        response.raise_for_status()
        data = response.json()
        return data.get("response", "")
    except Exception as e:
        return f"Error communicating with Ollama: {str(e)}"

def main():
    print(f"Magistrate Hermes Harness [Model: {MODEL_NAME}]")
    print("Type your command and press Enter. Type 'exit' to quit.")
    
    while True:
        try:
            user_input = input("Firstmate> ")
            if user_input.strip().lower() in ['exit', 'quit']:
                break
            
            # Simple intercept for dangerous commands would go here, 
            # or in the adapter that monitors the tmux pane.
            
            response = run_hermes_inference(user_input)
            print(f"\n{response}\n")
            
        except (KeyboardInterrupt, EOFError):
            break

if __name__ == "__main__":
    main()
