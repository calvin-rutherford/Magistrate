import httpx
import pytest

from app.stt_adapter import TranscriptionError, VoiceInputAdapter


def test_capabilities_never_include_server_credentials(monkeypatch):
    adapter = VoiceInputAdapter()
    adapter.api_key = 'server-secret'
    capability = adapter.capabilities()
    assert capability['configured'] is True
    assert 'api_key' not in capability
    assert 'server-secret' not in str(capability)


@pytest.mark.asyncio
async def test_transcription_requires_real_audio_and_configuration():
    adapter = VoiceInputAdapter()
    adapter.api_key = None
    with pytest.raises(TranscriptionError) as empty:
        await adapter.transcribe_audio(b'', content_type='audio/webm')
    assert empty.value.status_code == 400
    with pytest.raises(TranscriptionError) as unavailable:
        await adapter.transcribe_audio(b'real encoded bytes', content_type='audio/webm')
    assert unavailable.value.status_code == 503


@pytest.mark.asyncio
async def test_transcription_returns_only_provider_text(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers['Authorization'] == 'Bearer test-key'
        return httpx.Response(200, json={'text': 'Ask the API agent to run focused tests.'})

    adapter = VoiceInputAdapter()
    adapter.api_key = 'test-key'
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs['transport'] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', client_factory)
    result = await adapter.transcribe_audio(b'encoded audio', content_type='audio/mp4', filename='speech.m4a')
    assert result['text'] == 'Ask the API agent to run focused tests.'
    assert result['is_final'] is True
    assert 'confidence' not in result


@pytest.mark.asyncio
async def test_transcription_rejects_unsupported_media():
    adapter = VoiceInputAdapter()
    with pytest.raises(TranscriptionError) as error:
        await adapter.transcribe_audio(b'content', content_type='text/plain')
    assert error.value.status_code == 415
