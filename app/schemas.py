from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=64)

    @field_validator("password")
    @classmethod
    def fits_bcrypt(cls, value: str) -> str:
        # bcrypt only accepts 72 BYTES; accented chars take 2+ bytes each
        if len(value.encode()) > 72:
            raise ValueError("Password must be at most 72 bytes in UTF-8")
        return value


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
