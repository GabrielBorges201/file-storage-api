# File Storage API

![CI](https://github.com/GabrielBorges201/file-storage-api/actions/workflows/ci.yml/badge.svg)

REST API for secure file uploads to **S3-compatible storage** (MinIO locally, AWS S3 in production).
Built with FastAPI, PostgreSQL, SQLAlchemy 2.0, boto3 and JWT auth.

The interesting part is not "saving a file": it is doing it **safely**.

## Features

- **Two upload flows**
  - **Through the API**: validated before anything reaches the bucket (small files).
  - **Direct to storage** with a **presigned POST**: the file never passes through the API (large files).
    S3 itself enforces exact size and content type; the API re-verifies on `confirm`.
- **Real content validation**: extension allowlist + magic-byte sniffing. The client `Content-Type` is never trusted.
- **Size limits** at three layers: `Content-Length` middleware, in-endpoint check, S3 policy condition.
- **Presigned download URLs** that expire (default 5 min) and force `Content-Disposition: attachment`.
- **Per-user isolation**: other users' files return `404`, not `403`.
- **Safe storage keys**: `users/{user_id}/{uuid}.{ext}`; the user's filename is sanitized and kept only as metadata.
- **SHA-256** checksum stored for API uploads.
- Cleanup on failure: no orphan objects if the DB write fails; spoofed presigned uploads are deleted on `confirm`.

## Architecture

```
            ┌──────────────┐  1. POST /files/presign        ┌───────────┐
  Client ──▶│  FastAPI API │◀───────────────────────────────│ PostgreSQL│
            └──────┬───────┘  3. POST /files/{id}/confirm   └───────────┘
                   │  (validates, signs URLs)
                   │ 2. client uploads directly with the presigned POST
                   ▼
            ┌──────────────┐
            │ MinIO / S3   │
            └──────────────┘
```

## Run locally

```bash
docker compose up --build
```

- API docs (Swagger): http://localhost:8000/docs
- MinIO console: http://localhost:9001 (`minioadmin` / `minioadmin`)

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

### Example: direct upload

```bash
TOKEN=$(curl -s -X POST localhost:8000/auth/login -d 'username=me@example.com&password=supersecret123' | jq -r .access_token)

# 1. ask for a presigned POST
curl -s -X POST localhost:8000/files/presign \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"filename":"photo.png","content_type":"image/png","size_bytes":'$(stat -c%s photo.png)'}' > presign.json

# 2. upload straight to MinIO (the "file" field must come last)
curl -X POST "$(jq -r .url presign.json)" \
  $(jq -r '.fields | to_entries[] | "-F \(.key)=\(.value)"' presign.json) \
  -F file=@photo.png

# 3. confirm
curl -X POST localhost:8000/files/$(jq -r .file_id presign.json)/confirm -H "Authorization: Bearer $TOKEN"
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

S3 is mocked with [moto](https://github.com/getmoto/moto); no Docker needed.

## Design notes

- **Two S3 clients.** Inside Docker the API reaches MinIO at `http://minio:9000`, but presigned URLs
  must carry a host the browser can reach (`http://localhost:9000`). The signature covers the host,
  so URLs are signed with `S3_PUBLIC_ENDPOINT_URL`.
- **Pending state.** Presigned uploads create a `pending` record that only becomes `ready` after `confirm`.
  Pending records are invisible in listings and cannot be downloaded.
- **Browser uploads** need a CORS rule on the bucket (`mc anonymous`/`mc admin` or the S3 console).

## Possible next steps

- Alembic migrations instead of `create_all`
- Background job (or S3 lifecycle rule) to purge `pending` records and objects older than 24h
- Antivirus scanning (ClamAV) before marking a file `ready`
- Image thumbnails via a Celery worker
- Multipart upload for files larger than 5 GB
