from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.adapters.storage_local import LocalStorage
from app.api.deps import StorageDep, public_limit
from app.domain.errors import NotFoundError

router = APIRouter(tags=["files"])


@router.get("/files/{token}", response_class=FileResponse, dependencies=[Depends(public_limit)])
async def serve(token: str, storage: StorageDep) -> FileResponse:
    """The local store's signed download URL: its HMAC token is the credential (like an S3 presigned URL),
    so there is no bearer. Expired, altered or unknown all answer the same 404."""
    found = storage.open_signed(token) if isinstance(storage, LocalStorage) else None
    if found is None:
        raise NotFoundError
    return FileResponse(
        found.path, media_type=found.content_type, filename=found.filename,
        content_disposition_type="attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )  # fmt: skip
