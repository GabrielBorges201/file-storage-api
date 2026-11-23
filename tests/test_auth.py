from tests.conftest import PASSWORD


def test_register_duplicate_email_conflicts(client):
    body = {"email": "a@example.com", "password": PASSWORD}
    assert client.post("/auth/register", json=body).status_code == 201
    assert client.post("/auth/register", json=body).status_code == 409


def test_login_wrong_password(client):
    client.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    r = client.post("/auth/login", data={"username": "a@example.com", "password": "wrong-pass-1"})
    assert r.status_code == 401


def test_files_require_authentication(client):
    assert client.get("/files").status_code == 401
