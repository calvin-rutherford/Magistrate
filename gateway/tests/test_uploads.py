from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.main import app
from conftest import TEST_HEADERS

client = TestClient(app)
PNG_1X1 = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000000020001e221bc330000000049454e44ae426082')


def test_authenticated_upload_accepts_image_and_downloads_only_for_owner(monkeypatch, tmp_path):
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    response = client.post('/api/v1/uploads', headers=TEST_HEADERS, files={'files': ('diagram.png', PNG_1X1, 'image/png')})
    assert response.status_code == 200
    uploaded = response.json()['uploads'][0]
    assert uploaded['filename'] == 'diagram.png'
    assert uploaded['media_type'] == 'image/png'

    download = client.get('/api/v1/uploads/' + uploaded['upload_id'], headers=TEST_HEADERS)
    assert download.status_code == 200
    assert download.content == PNG_1X1


def test_upload_rejects_unsupported_and_oversized_files(monkeypatch, tmp_path):
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    unsupported = client.post('/api/v1/uploads', headers=TEST_HEADERS, files={'files': ('script.exe', b'bad', 'application/x-msdownload')})
    assert unsupported.status_code == 422
    oversized = client.post('/api/v1/uploads', headers=TEST_HEADERS, files={'files': ('big.bin', b'x' * (25 * 1024 * 1024 + 1), 'application/octet-stream')})
    assert oversized.status_code == 413


def test_upload_rejects_content_type_mismatch_and_unsafe_name(monkeypatch, tmp_path):
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    mismatch = client.post('/api/v1/uploads', headers=TEST_HEADERS, files={'files': ('photo.png', b'not-an-image', 'image/png')})
    assert mismatch.status_code == 422
    response = client.post('/api/v1/uploads', headers=TEST_HEADERS, files={'files': ('..\\secret name.txt', b'hello', 'text/plain')})
    assert response.status_code == 200
    assert response.json()['uploads'][0]['filename'] == 'secret_name.txt'


def test_prompt_associates_owned_upload_and_forwards_only_safe_summary(monkeypatch, tmp_path):
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    upload = client.post('/api/v1/uploads', headers=TEST_HEADERS, files={'files': ('notes.txt', b'hello', 'text/plain')}).json()['uploads'][0]
    prompt = AsyncMock(return_value={'status': 'submitted', 'response': None})
    monkeypatch.setattr('app.main.herdr_client.prompt_agent', prompt)
    response = client.post('/api/v1/captain/prompt', headers=TEST_HEADERS, json={
        'text': 'Review this', 'message_id': 'message-123', 'attachments': [upload],
    })
    assert response.status_code == 200
    forwarded = prompt.await_args.args[1]
    assert forwarded == 'Review this\n\nAttached files: notes.txt (text/plain, 5 bytes)'
    assert 'upload_id' not in forwarded
    assert 'message-123' not in forwarded
    [attachment] = response.json()['conversation']['messages'][0]['attachments']
    assert attachment == {
        'id': upload['upload_id'],
        'upload_id': upload['upload_id'],
        'name': 'notes.txt',
        'media_type': 'text/plain',
        'size': 5,
        'url': f"/api/v1/uploads/{upload['upload_id']}",
    }
    persisted = client.get('/api/v1/conversations/captain/messages', headers=TEST_HEADERS).json()
    canonical_prompt = next(item for item in persisted['messages'] if item.get('client_message_id') == 'message-123')
    assert canonical_prompt['attachments'] == [attachment]


def test_prompt_rejects_client_attachment_metadata_tampering(monkeypatch, tmp_path):
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    upload = client.post('/api/v1/uploads', headers=TEST_HEADERS, files={'files': ('notes.txt', b'hello', 'text/plain')}).json()['uploads'][0]
    upload['size'] = 999
    response = client.post('/api/v1/captain/prompt', headers=TEST_HEADERS, json={'text': 'Review', 'attachments': [upload]})
    assert response.status_code == 422


def test_upload_download_requires_authentication():
    assert client.post('/api/v1/uploads', files={'files': ('note.txt', b'hi', 'text/plain')}).status_code == 401
    assert client.get('/api/v1/uploads/not-a-real-upload').status_code == 401
