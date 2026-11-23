import logging
from contextlib import asynccontextmanager

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.config import get_settings
from app.database import Base, engine
from app.routers import auth, files
from app.storage import get_storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # create_all keeps the demo simple; use Alembic migrations in a real deployment.
    Base.metadata.create_all(engine)
    get_storage().ensure_bucket()
    yield


app = FastAPI(
    title="File Storage API",
    description="Secure file uploads to S3/MinIO with validation and presigned URLs.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def reject_oversized_uploads(request: Request, call_next):
    """Cheap early rejection based on Content-Length, before the body is parsed.

    Requests without Content-Length (chunked) are still caught by the size check
    inside the endpoint.
    """
    if request.method == "POST" and request.url.path == "/files/upload":
        limit = get_settings().max_upload_bytes + 64 * 1024  # multipart overhead
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > limit:
            return JSONResponse({"detail": "File too large"}, status_code=413)
    return await call_next(request)


@app.exception_handler(ClientError)
@app.exception_handler(BotoCoreError)
async def storage_error_handler(_: Request, exc: Exception):
    logger.exception("Storage error", exc_info=exc)
    return JSONResponse({"detail": "Storage service unavailable"}, status_code=502)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(files.router)
