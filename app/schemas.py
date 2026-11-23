from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=64)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str | None
    status: str
    created_at: datetime


class FileList(BaseModel):
    items: list[FileOut]
    total: int
    limit: int
    offset: int


class PresignRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str
    size_bytes: int = Field(gt=0)


class PresignResponse(BaseModel):
    file_id: str
    url: str
    fields: dict[str, str]
    expires_in: int


class DownloadUrl(BaseModel):
    url: str
    expires_in: int
