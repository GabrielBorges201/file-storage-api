"""File validation: extension allowlist + content sniffing (magic bytes).

The client-declared Content-Type is never trusted: it is trivially forged.
"""
import codecs
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath

import filetype
from fastapi import HTTPException

HEAD_BYTES = 2048  # enough for every signature we check


@dataclass(frozen=True)
class FileRule:
    mime: str
    binary: bool  # True: must match a magic-byte signature. False: must be valid UTF-8 text.


RULES: dict[str, FileRule] = {
    ".pdf": FileRule("application/pdf", True),
    ".png": FileRule("image/png", True),
    ".jpg": FileRule("image/jpeg", True),
    ".jpeg": FileRule("image/jpeg", True),
    ".webp": FileRule("image/webp", True),
    ".txt": FileRule("text/plain", False),
    ".csv": FileRule("text/csv", False),
}


def sanitize_filename(name: str) -> str:
    """Strip paths, accents and unsafe characters. Never used to build the storage key."""
    name = PurePosixPath(name.replace("\\", "/")).name
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._ ")
    if not name:
        raise HTTPException(status_code=400, detail="A valid filename is required")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        return name[:120]
    return f"{stem[:120]}.{ext}"


def validate_extension(filename: str) -> tuple[str, FileRule]:
    ext = PurePosixPath(filename).suffix.lower()
    rule = RULES.get(ext)
    if rule is None:
        allowed = ", ".join(sorted(RULES))
        raise HTTPException(status_code=415, detail=f"File type not allowed. Allowed: {allowed}")
    return ext, rule


def validate_content(head: bytes, rule: FileRule) -> None:
    if rule.binary:
        guessed = filetype.guess_mime(head)
        if guessed != rule.mime:
            raise HTTPException(
                status_code=415,
                detail="File content does not match its extension",
            )
        return

    if b"\x00" in head:
        raise HTTPException(status_code=415, detail="File content does not match its extension")
    try:
        # incremental decoder tolerates a multibyte char cut at the end of the sample
        codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="Text files must be UTF-8 encoded")
