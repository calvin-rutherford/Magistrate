import os
from typing import Dict, Any, Optional

import httpx

SUPPORTED_AUDIO_TYPES = {
    'audio/m4a', 'audio/mp4', 'audio/mpeg', 'audio/wav', 'audio/webm',
    'audio/x-m4a', 'video/mp4', 'application/octet-stream'
}

class TranscriptionError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code

class VoiceInputAdapter:
    def __init__(self, provider: Optional[str] = None):
        self.provider = provider or os.getenv('VOICE_STT_PROVIDER', 'openai')
        self.api_key = os.getenv('OPENAI_API_KEY')
        self.model = os.getenv('VOICE_STT_MODEL', 'gpt-4o-mini-transcribe')
        self.base_url = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')

    async def transcribe_audio(self, audio_bytes: bytes, source: str = 'iphone',
                               content_type: str = 'application/octet-stream',
                               filename: str = 'speech.m4a') -> Dict[str, Any]:
        if not audio_bytes:
            raise TranscriptionError('No microphone audio was received.', 400)
        if len(audio_bytes) > 25 * 1024 * 1024:
            raise TranscriptionError('Recording exceeds the 25 MB transcription limit.', 413)
        normalized_type = content_type.split(';', 1)[0].lower()
        if normalized_type not in SUPPORTED_AUDIO_TYPES:
            raise TranscriptionError(f'Unsupported audio type: {normalized_type}', 415)
        if self.provider != 'openai' or not self.api_key:
            raise TranscriptionError('Speech transcription is not configured on the gateway.', 503)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0)) as client:
                response = await client.post(
                    f'{self.base_url}/audio/transcriptions',
                    headers={'Authorization': f'Bearer {self.api_key}'},
                    data={'model': self.model, 'response_format': 'json'},
                    files={'file': (filename, audio_bytes, normalized_type)},
                )
            if response.status_code >= 400:
                raise TranscriptionError('Speech provider rejected the recording.', 502)
            text = str(response.json().get('text', '')).strip()
        except TranscriptionError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise TranscriptionError('Speech transcription service is unavailable.', 502) from exc
        if not text:
            raise TranscriptionError('No speech was recognized. Try again closer to the microphone.', 422)

        return {
            'text': text,
            'is_final': True,
            'source': source,
            'provider': self.provider,
            'model': self.model
        }

stt_adapter = VoiceInputAdapter()
