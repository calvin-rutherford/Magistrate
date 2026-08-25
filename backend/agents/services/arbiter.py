import requests
from typing import Any, Dict, List
import json

class ArbiterService:
    """
    The brain of the Magistrate Government.
    Powered by Hermes 3 8B. 
    Strictly constrained to structural routing, task decomposition, and institutional memory.
    """

    OLLAMA_API = "http://127.0.0.1:11434/api/chat"
    MODEL = "hermes3:8b"
    
    @classmethod
    def call_arbiter(cls, messages: List[Dict[str, str]], context_tokens: int = 16384) -> Dict[str, Any]:
        """
        Invokes the Arbiter model with a strict context budget to protect VRAM.
        """
        payload = {
            "model": cls.MODEL,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {
                "num_ctx": context_tokens,
                "temperature": 0.15,
            },
            "keep_alive": "30m",
        }
        
        response = requests.post(cls.OLLAMA_API, json=payload, timeout=300)
        response.raise_for_status()
        
        data = response.json()
        raw_text = data.get('message', {}).get('content', '{}')
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            return {"error": "Arbiter returned invalid JSON"}

    @classmethod
    def process_presidential_directive(cls, directive: str, current_state: Dict) -> Dict:
        """
        Packages a directive into a compact Decision Packet for the Arbiter.
        """
        system_prompt = (
            "You are Hermes, the Arbiter of the Magistrate AI Government.\n"
            "You are a Central Broker Orchestrator. Your job is to read the President's command, identify the intent, "
            "and decompose the objective into a sequential array of sub-tasks for execution by worker agents.\n"
            "CRITICAL RULES:\n"
            "1. Break the objective into logical steps (e.g., Code -> Test -> Push).\n"
            "2. The final sub-task MUST ALWAYS be to commit changes, push to GitHub, and use the 'gh pr create' CLI to open a Pull Request.\n"
            "3. Assign a specific model provider to each sub-task based on its complexity:\n"
            "   - Use 'gemini-1.5-pro' for complex reasoning, heavy coding, and architecture.\n"
            "   - Use 'claude-3-5-sonnet' for extreme edge-case coding or Enterprise API logic.\n"
            "   - Use 'ollama/qwen2.5:3b' for simple bash commands, testing, or git operations (to save compute).\n"
            "Always return strict JSON conforming to this schema:\n"
            "{\n"
            '  "intent": "string",\n'
            '  "risk_tier": "integer",\n'
            '  "sub_tasks": [\n'
            '    {\n'
            '      "title": "string",\n'
            '      "description": "string (Detailed instructions for the worker agent)",\n'
            '      "model_provider": "string (gemini-1.5-pro | claude-3-5-sonnet | ollama/qwen2.5:3b)"\n'
            '    }\n'
            '  ]\n'
            "}"
        )
        
        decision_packet = {
            "objective": directive,
            "current_state": current_state,
            "requested_decision": "Select a worker model route and execution plan"
        }
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(decision_packet)}
        ]
        
        return cls.call_arbiter(messages)
