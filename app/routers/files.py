import hashlib
import os
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import STATUS_PENDING, STATUS_READY, StoredFile, User
from app.schemas import DownloadUrl, FileList, FileOut, PresignRequest, PresignResponse
from app.security import get_current_user
from app.storage import Storage, get_storage
from app.validation import (
    HEAD_BYTES,
    sanitize_filename,
    validate_content,
    validate_extension,
)

router = APIRouter(prefix="/files", tags=["files"])

CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[Session, Depends(get_db)]
Store = Annotated[Storage, Depends(get_storage)]
Cfg = Annotated[Settings, Depends(get_settings)]

CHUNK = 1024 * 1024


def _build_key(user_id: int, file_id: str, ext: str) -> str:
    # The user-supplied filename is NEVER part of the key: no path traversal, no collisions.
    return f"users/{user_id}/{file_id}{ext}"


def _get_owned(db: Session, file_id: str, user: User) -> StoredFile:
    # 404 (not 403) for other people's files: don't leak that the id exists.
    record = db.scalar(
        select(StoredFile).where(StoredFile.id == file_id, StoredFile.owner_id == user.id)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    return record


# --------------------------------------------------------------------------- #
# Flow A: upload through the API (small files, full validation before storing) #
# --------------------------------------------------------------------------- #
@router.post("/upload", response_model=FileOut, status_code=201)
def upload_file(file: UploadFile, user: CurrentUser, db: DB, storage: Store, settings: Cfg):
    filename = sanitize_filename(file.filename or "")
    ext, rule = validate_extension(filename)

    stream = file.file
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    if size == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if size > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413, detail=f"File too large (max {settings.max_upload_size_mb} MB)"
        )

    validate_content(stream.read(HEAD_BYTES), rule)
    stream.seek(0)

    digest = hashlib.sha256()
    while chunk := stream.read(CHUNK):
        digest.update(chunk)
    stream.seek(0)

    file_id = str(uuid.uuid4())
    key = _build_key(user.id, file_id, ext)
    storage.upload_fileobj(stream, key, rule.mime)

    record = StoredFile(
        id=file_id,
        owner_id=user.id,
        filename=filename,
        storage_key=key,
        content_type=rule.mime,
        size_bytes=size,
        sha256=digest.hexdigest(),
        status=STATUS_READY,
    )
    db.add(record)
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(key)  # don't leave an orphan object behind
        raise
    return record


# --------------------------------------------------------------------------- #
# Flow B: direct-to-storage upload with a presigned POST (large files)        #
#   1. POST /files/presign        -> url + fields (size/type enforced by S3)   #
#   2. client POSTs the file straight to the bucket                           #
#   3. POST /files/{id}/confirm   -> API re-checks the object and activates it #
# --------------------------------------------------------------------------- #
@router.post("/presign", response_model=PresignResponse, status_code=201)
def presign_upload(body: PresignRequest, user: CurrentUser, db: DB, storage: Store, settings: Cfg):
    filename = sanitize_filename(body.filename)
    ext, rule = validate_extension(filename)
    if body.content_type.split(";")[0].strip().lower() != rule.mime:
        raise HTTPException(
            status_code=415, detail=f"content_type must be {rule.mime} for {ext} files"
        )
    if body.size_bytes > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413, detail=f"File too large (max {settings.max_upload_size_mb} MB)"
        )

    file_id = str(uuid.uuid4())
    key = _build_key(user.id, file_id, ext)
    post = storage.presigned_post(key, rule.mime, body.size_bytes)

    db.add(
        StoredFile(
            id=file_id,
            owner_id=user.id,
            filename=filename,
            storage_key=key,
            content_type=rule.mime,
            size_bytes=body.size_bytes,
            status=STATUS_PENDING,
        )
    )
    db.commit()
    return PresignResponse(
        file_id=file_id, url=post["url"], fields=post["fields"], expires_in=storage.expires
    )


@router.post("/{file_id}/confirm", response_model=FileOut)
def confirm_upload(file_id: str, user: CurrentUser, db: DB, storage: Store, settings: Cfg):
    record = _get_owned(db, file_id, user)
    if record.status == STATUS_READY:
        return record  # idempotent

    meta = storage.head(record.storage_key)
    if meta is None:
        raise HTTPException(status_code=409, detail="Upload not found in storage yet")

    def reject(status_code: int, detail: str):
        storage.delete(record.storage_key)
        db.delete(record)
        db.commit()
        raise HTTPException(status_code=status_code, detail=detail)

    # Never trust the client: verify what actually landed in the bucket.
    if meta["ContentLength"] != record.size_bytes or meta["ContentLength"] > settings.max_upload_bytes:
        reject(422, "Uploaded size does not match the declared size")

    ext = os.path.splitext(record.filename)[1].lower()
    _, rule = validate_extension(f"x{ext}")
    try:
        validate_content(storage.read_head(record.storage_key, HEAD_BYTES), rule)
    except HTTPException as exc:
        reject(exc.status_code, exc.detail)

    record.status = STATUS_READY
    db.commit()
    return record


# --------------------------------------------------------------------------- #
# Read / download / delete                                                    #
# --------------------------------------------------------------------------- #
@router.get("", response_model=FileList)
def list_files(
    user: CurrentUser,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    base = (StoredFile.owner_id == user.id, StoredFile.status == STATUS_READY)
    total = db.scalar(select(func.count()).select_from(StoredFile).where(*base)) or 0
    items = db.scalars(
        select(StoredFile)
        .where(*base)
        .order_by(StoredFile.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return FileList(items=items, total=total, limit=limit, offset=offset)


@router.get("/{file_id}", response_model=FileOut)
def get_file(file_id: str, user: CurrentUser, db: DB):
    return _get_owned(db, file_id, user)


@router.get("/{file_id}/download-url", response_model=DownloadUrl)
def download_url(file_id: str, user: CurrentUser, db: DB, storage: Store):
    record = _get_owned(db, file_id, user)
    if record.status != STATUS_READY:
        raise HTTPException(status_code=409, detail="File upload not confirmed")
    url = storage.presigned_get(record.storage_key, record.filename, record.content_type)
    return DownloadUrl(url=url, expires_in=storage.expires)


@router.delete("/{file_id}", status_code=204)
def delete_file(file_id: str, user: CurrentUser, db: DB, storage: Store):
    record = _get_owned(db, file_id, user)
    storage.delete(record.storage_key)
    db.delete(record)
    db.commit()
