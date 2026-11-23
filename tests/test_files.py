from botocore.exceptions import ClientError

from tests.conftest import BUCKET

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"0" * 64


def upload(client, headers, name="photo.png", data=PNG, mime="image/png"):
    return client.post("/files/upload", files={"file": (name, data, mime)}, headers=headers)


def object_exists(s3, key) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError:
        return False


# ------------------------------ direct upload ------------------------------ #
def test_upload_valid_png(client, headers, s3):
    r = upload(client, headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "ready"
    assert body["size_bytes"] == len(PNG)
    assert len(body["sha256"]) == 64
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert len(keys) == 1 and keys[0].endswith(".png")
    assert "photo" not in keys[0]  # user filename never reaches the storage key


def test_upload_rejects_disallowed_extension(client, headers):
    r = upload(client, headers, name="malware.exe", data=b"MZ" + b"\x00" * 64)
    assert r.status_code == 415


def test_upload_rejects_content_that_does_not_match_extension(client, headers, s3):
    r = upload(client, headers, name="fake.png", data=b"<?php echo 'hi'; ?>")
    assert r.status_code == 415
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET)


def test_upload_rejects_wrong_binary_type(client, headers):
    r = upload(client, headers, name="doc.pdf", data=PNG, mime="application/pdf")
    assert r.status_code == 415


def test_upload_rejects_too_large(client, headers):
    r = upload(client, headers, data=PNG + b"0" * (2 * 1024 * 1024))
    assert r.status_code == 413


def test_upload_rejects_empty(client, headers):
    assert upload(client, headers, data=b"").status_code == 400


def test_upload_accepts_utf8_text_and_rejects_binary_text(client, headers):
    assert upload(client, headers, "notes.txt", "olá, mundo".encode(), "text/plain").status_code == 201
    assert upload(client, headers, "bad.txt", b"\xff\xfe\x00\x00", "text/plain").status_code == 415


def test_filename_is_sanitized(client, headers):
    r = upload(client, headers, name="../../relatório final.pdf", data=PDF, mime="application/pdf")
    assert r.status_code == 201
    assert r.json()["filename"] == "relatorio_final.pdf"


# ------------------------------ read / download ----------------------------- #
def test_list_get_and_pagination(client, headers):
    for i in range(3):
        assert upload(client, headers, name=f"p{i}.png").status_code == 201
    r = client.get("/files?limit=2", headers=headers)
    assert r.json()["total"] == 3 and len(r.json()["items"]) == 2
    file_id = r.json()["items"][0]["id"]
    assert client.get(f"/files/{file_id}", headers=headers).status_code == 200


def test_download_url_is_presigned(client, headers):
    file_id = upload(client, headers).json()["id"]
    r = client.get(f"/files/{file_id}/download-url", headers=headers)
    assert r.status_code == 200
    url = r.json()["url"]
    assert "X-Amz-Signature" in url and "X-Amz-Expires=300" in url


def test_user_cannot_access_other_users_files(client, make_headers):
    alice = make_headers("alice@example.com")
    bob = make_headers("bob@example.com")
    file_id = upload(client, alice).json()["id"]
    assert client.get(f"/files/{file_id}", headers=bob).status_code == 404
    assert client.get(f"/files/{file_id}/download-url", headers=bob).status_code == 404
    assert client.delete(f"/files/{file_id}", headers=bob).status_code == 404
    assert client.get("/files", headers=bob).json()["total"] == 0


def test_delete_removes_object_and_record(client, headers, s3):
    body = upload(client, headers).json()
    assert client.delete(f"/files/{body['id']}", headers=headers).status_code == 204
    assert client.get(f"/files/{body['id']}", headers=headers).status_code == 404
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET)


# ------------------------------ presigned flow ------------------------------ #
def presign(client, headers, name="photo.png", mime="image/png", size=None):
    size = len(PNG) if size is None else size
    return client.post(
        "/files/presign",
        json={"filename": name, "content_type": mime, "size_bytes": size},
        headers=headers,
    )


def test_presign_returns_signed_post_with_size_condition(client, headers):
    r = presign(client, headers)
    assert r.status_code == 201, r.text
    data = r.json()
    assert "policy" in data["fields"] and data["fields"]["key"].startswith("users/")
    assert client.get("/files", headers=headers).json()["total"] == 0  # still pending


def test_presign_validates_request(client, headers):
    assert presign(client, headers, name="x.exe", mime="application/octet-stream").status_code == 415
    assert presign(client, headers, mime="application/pdf").status_code == 415
    assert presign(client, headers, size=5 * 1024 * 1024).status_code == 413


def test_presign_confirm_happy_path(client, headers, s3):
    data = presign(client, headers).json()
    s3.put_object(Bucket=BUCKET, Key=data["fields"]["key"], Body=PNG, ContentType="image/png")
    r = client.post(f"/files/{data['file_id']}/confirm", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "ready"
    assert client.get("/files", headers=headers).json()["total"] == 1
    assert client.post(f"/files/{data['file_id']}/confirm", headers=headers).status_code == 200


def test_confirm_without_upload_conflicts(client, headers):
    data = presign(client, headers).json()
    assert client.post(f"/files/{data['file_id']}/confirm", headers=headers).status_code == 409
    assert client.get(f"/files/{data['file_id']}/download-url", headers=headers).status_code == 409


def test_confirm_rejects_and_removes_spoofed_content(client, headers, s3):
    fake = b"<script>alert(1)</script>"
    data = presign(client, headers, size=len(fake)).json()
    key = data["fields"]["key"]
    s3.put_object(Bucket=BUCKET, Key=key, Body=fake)
    assert client.post(f"/files/{data['file_id']}/confirm", headers=headers).status_code == 415
    assert not object_exists(s3, key)
    assert client.get(f"/files/{data['file_id']}", headers=headers).status_code == 404


def test_confirm_rejects_size_mismatch(client, headers, s3):
    data = presign(client, headers, size=10).json()
    key = data["fields"]["key"]
    s3.put_object(Bucket=BUCKET, Key=key, Body=PNG)
    assert client.post(f"/files/{data['file_id']}/confirm", headers=headers).status_code == 422
    assert not object_exists(s3, key)
