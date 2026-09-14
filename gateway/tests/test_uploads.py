from fastapi.testclient import TestClient

from app.magi_model import MagiModelResult
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


def test_native_message_associates_owned_upload_and_forwards_only_safe_summary(monkeypatch, tmp_path):
    import app.magi_chat_api as native_api

    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    upload = client.post('/api/v1/uploads', headers=TEST_HEADERS, files={'files': ('notes.txt', b'hello', 'text/plain')}).json()['uploads'][0]
    observed = []

    class CapturingModel:
        async def complete(self, messages, **_kwargs):
            observed.append(messages[-1].content)
            return MagiModelResult('Received attachment.')

    monkeypatch.setattr(native_api.magi_chat_service, 'model', CapturingModel())
    response = client.post('/api/v1/magi/messages', headers=TEST_HEADERS, json={
        'client_message_id': 'attachment-message-0001', 'content': 'Review this',
        'attachments': [{
            'upload_id': upload['upload_id'], 'filename': upload['filename'],
            'media_type': upload['media_type'], 'size': upload['size'],
        }],
    })
    assert response.status_code == 200
    assert observed == [
        'Review this\n\nAuthenticated attachment metadata (file bytes are not included):\n'
        '- notes.txt (text/plain, 5 bytes)'
    ]
    assert upload['upload_id'] not in observed[0]
    [attachment] = response.json()['user_message']['attachments']
    assert attachment['upload_id'] == upload['upload_id']
    persisted = client.get('/api/v1/magi/conversations/current', headers=TEST_HEADERS).json()
    canonical_prompt = next(item for item in persisted['messages'] if item.get('client_message_id') == 'attachment-message-0001')
    assert canonical_prompt['attachments'] == [attachment]


def test_native_message_rejects_client_attachment_metadata_tampering(monkeypatch, tmp_path):
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    upload = client.post('/api/v1/uploads', headers=TEST_HEADERS, files={'files': ('notes.txt', b'hello', 'text/plain')}).json()['uploads'][0]
    upload['size'] = 999
    response = client.post('/api/v1/magi/messages', headers=TEST_HEADERS, json={
        'client_message_id': 'attachment-tamper-0001', 'content': 'Review',
        'attachments': [{
            'upload_id': upload['upload_id'], 'filename': upload['filename'],
            'media_type': upload['media_type'], 'size': upload['size'],
        }],
    })
    assert response.status_code == 422


def test_upload_download_requires_authentication():
    assert client.post('/api/v1/uploads', files={'files': ('note.txt', b'hi', 'text/plain')}).status_code == 401
    assert client.get('/api/v1/uploads/not-a-real-upload').status_code == 401
