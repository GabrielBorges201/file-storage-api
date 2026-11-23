# File Storage API

![CI](https://github.com/GabrielBorges201/file-storage-api/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-009688)
![License](https://img.shields.io/badge/license-MIT-green)

REST API for secure file uploads to **S3-compatible storage** (MinIO locally, AWS S3 / Cloudflare R2 in production).

The interesting part is not "saving a file": it is doing it **safely**: content validation, size enforcement at several layers, presigned URLs and per-user isolation.

> **Live demo:** _add your Swagger URL here (Render / Railway / Fly.io)_

## Tech stack

FastAPI · PostgreSQL · SQLAlchemy 2.0 · boto3 · MinIO/S3 · JWT · Docker Compose · pytest + moto · GitHub Actions

## Features

- **Two upload flows**
  - **Through the API:** validated before anything reaches the bucket (small files).
  - **Direct to storage** with a **presigned POST:** the file never passes through the API (large files). S3 itself enforces exact size and content type; the API re-verifies on `confirm`.
- **Real content validation:** extension allowlist + magic-byte sniffing. The client `Content-Type` is never trusted.
- **Size limits at three layers:** `Content-Length` middleware, in-endpoint check, S3 policy condition.
- **Presigned download URLs** that expire (default 5 min) and force `Content-Disposition: attachment`.
- **Per-user isolation:** other users' files return `404`, not `403`, so IDs don't leak.
- **Safe storage keys:** `users/{user_id}/{uuid}.{ext}`. The user's filename is sanitized and kept only as metadata.
- **SHA-256 checksum** stored for API uploads.
- **Cleanup on failure:** no orphan objects if the DB write fails; spoofed presigned uploads are deleted on `confirm`.

## Architecture

```
            ┌──────────────┐  1. POST /files/presign        ┌───────────┐
  Client ──▶│  FastAPI API │◀──────────────────────────────▶│ PostgreSQL│
            └──────┬───────┘  3. POST /files/{id}/confirm   └───────────┘
                   │  (validates, signs URLs)
                   │ 2. client uploads directly with the presigned POST
                   ▼
            ┌──────────────┐
            │ MinIO / S3   │
            └──────────────┘
```

## Getting started

```bash
git clone https://github.com/GabrielBorges201/file-storage-api.git
cd file-storage-api
docker compose up --build
```

| Service | URL |
|---|---|
| API docs (Swagger) | http://localhost:8000/docs |
| MinIO console | http://localhost:9001 (`minioadmin` / `minioadmin`) |

Without Docker: copy `.env.example` to `.env`, run PostgreSQL and MinIO yourself, then
`pip install -r requirements.txt && uvicorn app.main:app --reload`.

## Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create account |
| POST | `/auth/login` | Get JWT (OAuth2 password form) |
| POST | `/files/upload` | Upload through the API (multipart) |
| POST | `/files/presign` | Get a presigned POST for direct upload |
| POST | `/files/{id}/confirm` | Verify the direct upload and activate the file |
| GET | `/files` | List your files (paginated) |
| GET | `/files/{id}` | File metadata |
| GET | `/files/{id}/download-url` | Temporary download link |
| DELETE | `/files/{id}` | Delete object and record |
| GET | `/health` | Health check |

### Example: upload through the API

```bash
curl -X POST localhost:8000/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","password":"supersecret123"}'

TOKEN=$(curl -s -X POST localhost:8000/auth/login \
  -d 'username=me@example.com&password=supersecret123' | jq -r .access_token)

curl -X POST localhost:8000/files/upload -H "Authorization: Bearer $TOKEN" -F file=@photo.png
```

### Example: direct upload (presigned POST)

```bash
# 1. ask for a presigned POST
curl -s -X POST localhost:8000/files/presign \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"filename":"photo.png","content_type":"image/png","size_bytes":'$(stat -c%s photo.png)'}' > presign.json

# 2. upload straight to MinIO (the "file" field must come last)
curl -X POST "$(jq -r .url presign.json)" \
  $(jq -r '.fields | to_entries[] | "-F \(.key)=\(.value)"' presign.json) \
  -F file=@photo.png

# 3. confirm
curl -X POST localhost:8000/files/$(jq -r .file_id presign.json)/confirm \
  -H "Authorization: Bearer $TOKEN"
```

### Allowed file types

`.pdf` `.png` `.jpg` `.jpeg` `.webp` `.txt` `.csv`. Edit `RULES` in `app/validation.py` to change them.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./dev.db` | SQLAlchemy URL |
| `SECRET_KEY` | `change-me` | JWT signing key (use 32+ random bytes) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Token lifetime |
| `S3_ENDPOINT_URL` | _(AWS)_ | How the API reaches the storage |
| `S3_PUBLIC_ENDPOINT_URL` | = endpoint | Host baked into presigned URLs (must be reachable by the client) |
| `S3_REGION` | `us-east-1` | Bucket region |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | – | Storage credentials |
| `S3_BUCKET` | `uploads` | Bucket name (created on startup if missing) |
| `MAX_UPLOAD_SIZE_MB` | `10` | Max file size |
| `PRESIGNED_EXPIRATION_SECONDS` | `300` | Lifetime of presigned URLs |

## Project structure

```
app/
├── main.py          # app, lifespan, size-limit middleware, error handlers
├── config.py        # settings (pydantic-settings)
├── database.py      # engine / session
├── models.py        # User, StoredFile
├── schemas.py       # Pydantic models
├── security.py      # password hashing, JWT, current user
├── validation.py    # extension allowlist, magic bytes, filename sanitizing
├── storage.py       # S3/MinIO wrapper (upload, presign, head, delete)
└── routers/
    ├── auth.py
    └── files.py
tests/               # 21 tests, S3 mocked with moto
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

S3 is mocked with [moto](https://github.com/getmoto/moto); no Docker needed.
Covered: valid uploads, forged extensions and content, oversize/empty files, filename sanitizing,
user isolation, expiring download URLs, deletion, and the full presigned flow (including
spoofed content and size mismatches).

## Design notes

- **Two S3 clients.** Inside Docker the API reaches MinIO at `http://minio:9000`, but presigned URLs
  must carry a host the browser can reach (`http://localhost:9000`). The signature covers the host,
  so URLs are signed with `S3_PUBLIC_ENDPOINT_URL`.
- **Pending state.** Presigned uploads create a `pending` record that only becomes `ready` after `confirm`.
  Pending records are invisible in listings and cannot be downloaded.
- **Browser uploads** need a CORS rule on the bucket.
- **Migrations.** `create_all` runs on startup to keep the demo simple; use Alembic in production.

## Roadmap

- [ ] Alembic migrations
- [ ] Job (or S3 lifecycle rule) to purge `pending` records and objects older than 24h
- [ ] Antivirus scanning (ClamAV) before marking a file `ready`
- [ ] Image thumbnails via a Celery worker
- [ ] Multipart upload for very large files

## License

MIT
