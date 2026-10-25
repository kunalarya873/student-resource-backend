import asyncio
import hashlib
import logging
import re
import uuid
from typing import Any

from fastapi import HTTPException, status
from supabase import Client, create_client

from app.core.config import settings

logger = logging.getLogger("amu_backend.supabase_storage")


def sanitize_path_component(name: str) -> str:
    """Sanitize folder or file component to avoid path traversal and illegal chars."""
    clean = re.sub(r"[^\w\s\.-]", "", name.strip())
    clean = re.sub(r"[\s]+", "_", clean)
    return clean or "default"


class SupabaseStorageService:
    """Service governing Supabase storage uploads, user folder segregation, and secure signed URLs."""

    _client: Client | None = None

    @classmethod
    def get_client(cls) -> Client:
        """Get or initialize singleton Supabase Client (prefers service role key for admin storage operations)."""
        if cls._client is None:
            auth_key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_KEY
            if not settings.SUPABASE_URL or not auth_key:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Supabase storage is not configured. Please check SUPABASE_URL and SUPABASE_KEY in .env.",
                )
            cls._client = create_client(settings.SUPABASE_URL, auth_key)
        return cls._client

    @classmethod
    def _upload_sync(
        cls,
        object_key: str,
        file_data: bytes,
        content_type: str,
    ) -> Any:
        """Synchronous Supabase upload worker executed in worker thread."""
        client = cls.get_client()
        bucket = settings.SUPABASE_BUCKET or "files"
        return client.storage.from_(bucket).upload(
            path=object_key,
            file=file_data,
            file_options={"content-type": content_type, "upsert": "true"},
        )

    @classmethod
    async def upload_file_async(
        cls,
        user_id: uuid.UUID,
        folder_name: str,
        filename: str,
        file_data: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        """
        Upload asset to user-isolated folder: users/{user_id}/{folder_name}/{filename}.
        Guarantees that each user's resources are segregated in their own directory.
        """
        clean_folder = sanitize_path_component(folder_name)
        clean_filename = sanitize_path_component(filename)
        # Avoid collisions with unique prefix
        file_uuid = uuid.uuid4().hex[:8]
        object_key = f"users/{user_id}/{clean_folder}/{file_uuid}_{clean_filename}"

        checksum = hashlib.sha256(file_data).hexdigest()
        byte_size = len(file_data)

        try:
            await asyncio.to_thread(cls._upload_sync, object_key, file_data, content_type)
            logger.info("Uploaded %d bytes to Supabase at %s", byte_size, object_key)
        except Exception as exc:
            logger.error("Supabase upload failed for %s: %s", object_key, exc)
            # Check for common RLS error
            err_msg = str(exc)
            if "row-level security" in err_msg.lower() or "403" in err_msg:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Supabase upload rejected by storage policy. Please ensure the 'files' bucket has an INSERT RLS policy enabled or use a service role key.",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Storage provider error during upload: {type(exc).__name__}",
            ) from exc

        return {
            "object_key": object_key,
            "byte_size": byte_size,
            "checksum": checksum,
            "content_type": content_type,
            "storage_provider": "SUPABASE",
        }

    @classmethod
    def _create_signed_url_sync(cls, object_key: str, expires_in: int = 900) -> str:
        """Create time-bounded signed download URL to protect privacy."""
        client = cls.get_client()
        bucket = settings.SUPABASE_BUCKET or "files"
        try:
            res = client.storage.from_(bucket).create_signed_url(
                path=object_key, expires_in=expires_in
            )
            if isinstance(res, dict):
                signed = res.get("signedURL") or res.get("signedUrl") or res.get("url")
                if isinstance(signed, str):
                    return signed
                return str(signed or res)
            return str(res)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not generate signed URL for %s, falling back: %s",
                object_key,
                exc,
            )
            # Fallback to public URL if bucket is configured as public
            return client.storage.from_(bucket).get_public_url(object_key)

    @classmethod
    async def create_signed_download_url(
        cls,
        storage_path: str,
        expires_in: int = 900,
    ) -> str:
        """Asynchronously generate a signed download URL valid for `expires_in` seconds (default 15m)."""
        return await asyncio.to_thread(cls._create_signed_url_sync, storage_path, expires_in)

    @classmethod
    def _delete_sync(cls, storage_path: str) -> bool:
        client = cls.get_client()
        bucket = settings.SUPABASE_BUCKET or "files"
        try:
            client.storage.from_(bucket).remove([storage_path])
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to remove file from Supabase at %s: %s", storage_path, exc)
            return False

    @classmethod
    async def delete_file_async(cls, storage_path: str) -> bool:
        """Remove file from Supabase storage asynchronously."""
        return await asyncio.to_thread(cls._delete_sync, storage_path)
