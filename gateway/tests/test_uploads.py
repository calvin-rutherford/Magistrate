from fastapi.testclient import TestClient

from app.main import app
from conftest import TEST_HEADERS

client = TestClient(app)


def test_authenticated_upload_accepts_image_and_downloads_only_for_owner(monkeypatch, tmp_path):
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    response = client.post('/api/v1/uploads', headers=TEST_HEADERS, files={'files': ('diagram.png', b'png-data', 'image/png')})
    assert response.status_code == 200
    uploaded = response.json()['uploads'][0]
    assert uploaded['filename'] == 'diagram.png'
    assert uploaded['media_type'] == 'image/png'

    download = client.get('/api/v1/uploads/' + uploaded['upload_id'], headers=TEST_HEADERS)
    assert download.status_code == 200
    assert download.content == b'png-data'


def test_upload_rejects_unsupported_and_oversized_files(monkeypatch, tmp_path):
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    unsupported = client.post('/api/v1/uploads', headers=TEST_HEADERS, files={'files': ('script.exe', b'bad', 'application/x-msdownload')})
    assert unsupported.status_code == 422
    oversized = client.post('/api/v1/uploads', headers=TEST_HEADERS, files={'files': ('big.bin', b'x' * (25 * 1024 * 1024 + 1), 'application/octet-stream')})
    assert oversized.status_code == 413


def test_upload_download_requires_authentication():
    assert client.post('/api/v1/uploads', files={'files': ('note.txt', b'hi', 'text/plain')}).status_code == 401
    assert client.get('/api/v1/uploads/not-a-real-upload').status_code == 401
