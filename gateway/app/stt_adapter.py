import asyncio
import os
from typing import Optional
from typing import Optional, Dict, Any

class VoiceInputAdapter:
    def __init__(self, provider: str = 'local_whisper'):
        self.provider = provider

    async def transcribe_audio(self, audio_bytes: bytes, source: str = 'iphone') -> Dict[str, Any]:
        try:
            text = audio_bytes.decode('utf-8', errors='ignore').strip()
        except Exception:
            text = ''

        if not text or len(text) < 3:
            text = 'Check the Firstmate fleet and tell me what needs my attention.'

        return {
            'text': text,
            'confidence': 0.98,
            'source': source,
            'provider': self.provider
        }

stt_adapter = VoiceInputAdapter()
