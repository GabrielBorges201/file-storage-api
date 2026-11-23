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


def test_password_over_72_bytes_is_rejected_not_a_500(client):
    # 40 accented chars = 80 bytes: passes the 64-char limit but breaks bcrypt
    r = client.post("/auth/register", json={"email": "a@example.com", "password": "é" * 40})
    assert r.status_code == 422


def test_login_with_very_long_password_is_401_not_500(client):
    client.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    r = client.post("/auth/login", data={"username": "a@example.com", "password": "é" * 40})
    assert r.status_code == 401
