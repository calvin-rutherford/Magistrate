from io import BytesIO
import zipfile

from fastapi.testclient import TestClient

from app.auth import issue_session
from app.magi_model import MagiModelResult
from app.main import app
from app.uploads import cleanup_expired_uploads, get_upload
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
    observed_tools = []

    class CapturingModel:
        async def complete(self, messages, **kwargs):
            observed.append(messages[-1].content)
            observed_tools.append(kwargs.get('tools'))
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
    assert observed_tools == [None]
    [attachment] = response.json()['user_message']['attachments']
    assert attachment['upload_id'] == upload['upload_id']
    persisted = client.get('/api/v1/magi/conversations/current', headers=TEST_HEADERS).json()
    canonical_prompt = next(item for item in persisted['messages'] if item.get('client_message_id') == 'attachment-message-0001')
    assert canonical_prompt['attachments'] == [attachment]


def test_native_message_enforces_aggregate_limit_before_attachment_lookup():
    response = client.post('/api/v1/magi/messages', headers=TEST_HEADERS, json={
        'client_message_id': 'attachment-total-0001', 'content': 'Review',
        'attachments': [
            {'upload_id': f'upload-{index:016d}', 'filename': f'{index}.pdf',
             'media_type': 'application/pdf', 'size': 25 * 1024 * 1024}
            for index in range(3)
        ],
    })
    assert response.status_code == 413


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


def test_signed_access_remains_authenticated_owner_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    upload = client.post('/api/v1/uploads', headers=TEST_HEADERS,
                         files={'files': ('evidence.txt', b'private', 'text/plain')}).json()['uploads'][0]
    access = client.post(f"/api/v1/uploads/{upload['upload_id']}/access", headers=TEST_HEADERS)
    assert access.status_code == 200
    signed_url = access.json()['url']
    assert signed_url.startswith(f"/api/v1/uploads/{upload['upload_id']}?expires=")
    assert client.get(signed_url).status_code == 401
    assert client.get(signed_url, headers=TEST_HEADERS).content == b'private'
    assert client.get(signed_url.replace('signature=', 'signature=0'), headers=TEST_HEADERS).status_code == 403

    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_USER_ID', 'other_owner')
    other = issue_session('test-bootstrap-secret')['session_token']
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_USER_ID', 'default_user')
    other_headers = {'Authorization': f'Bearer {other}'}
    assert client.get(signed_url, headers=other_headers).status_code == 403
    assert client.get(f"/api/v1/uploads/{upload['upload_id']}", headers=other_headers).status_code == 404


def test_extension_is_not_trusted_for_office_or_images(monkeypatch, tmp_path):
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    fake_docx = client.post('/api/v1/uploads', headers=TEST_HEADERS,
                            files={'files': ('report.docx', b'plain text', 'application/octet-stream')})
    assert fake_docx.status_code == 422
    disguised_png = client.post('/api/v1/uploads', headers=TEST_HEADERS,
                                 files={'files': ('report.txt', PNG_1X1, 'text/plain')})
    assert disguised_png.status_code == 422

    archive_bytes = BytesIO()
    with zipfile.ZipFile(archive_bytes, 'w') as archive:
        archive.writestr('../../outside.txt', 'unsafe')
    traversal = client.post('/api/v1/uploads', headers=TEST_HEADERS,
                            files={'files': ('archive.zip', archive_bytes.getvalue(), 'application/zip')})
    assert traversal.status_code == 422


def test_scanner_rejection_and_retention_cleanup(monkeypatch, tmp_path):
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    monkeypatch.setenv('MAGISTRATE_UPLOAD_SCAN_COMMAND', '/bin/false')
    rejected = client.post('/api/v1/uploads', headers=TEST_HEADERS,
                           files={'files': ('unsafe.txt', b'payload', 'text/plain')})
    assert rejected.status_code == 422
    assert not [path for path in (tmp_path / 'files').rglob('*') if path.is_file()]

    monkeypatch.delenv('MAGISTRATE_UPLOAD_SCAN_COMMAND')
    stored = client.post('/api/v1/uploads', headers=TEST_HEADERS,
                         files={'files': ('short.txt', b'expire', 'text/plain')}).json()['uploads'][0]
    record = get_upload('default_user', stored['upload_id'])
    assert record and record['scan_status'] == 'not-configured'
    assert cleanup_expired_uploads(now=record['expires_at']) >= 1
    assert get_upload('default_user', stored['upload_id']) is None


def test_unattached_upload_can_be_removed_but_attached_object_cannot(monkeypatch, tmp_path):
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    loose = client.post('/api/v1/uploads', headers=TEST_HEADERS,
                        files={'files': ('draft.txt', b'draft', 'text/plain')}).json()['uploads'][0]
    assert client.delete(f"/api/v1/uploads/{loose['upload_id']}", headers=TEST_HEADERS).status_code == 204
    assert client.get(f"/api/v1/uploads/{loose['upload_id']}", headers=TEST_HEADERS).status_code == 404

    attached = client.post('/api/v1/uploads', headers=TEST_HEADERS,
                           data={'message_id': 'message-12345678'},
                           files={'files': ('kept.txt', b'kept', 'text/plain')}).json()['uploads'][0]
    response = client.delete(f"/api/v1/uploads/{attached['upload_id']}", headers=TEST_HEADERS)
    assert response.status_code == 409
