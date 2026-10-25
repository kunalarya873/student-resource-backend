from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.schemas.storage import SupabaseWebhookResponse
from app.services.supabase_webhook_service import SupabaseWebhookService

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post(
    "/supabase/storage",
    response_model=SupabaseWebhookResponse,
    status_code=status.HTTP_200_OK,
    summary="Supabase Storage Webhook receiver for object upload events",
)
async def supabase_storage_webhook(
    payload: dict[str, Any],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    x_webhook_secret: Annotated[str | None, Header(alias="X-Webhook-Secret")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> SupabaseWebhookResponse:
    """
    Receives automated notifications from Supabase Storage triggers when files are uploaded.
    Verifies the webhook secret, transitions the upload reservation to COMPLETED, commits
    storage quota atomically, and securely activates the resource.
    """
    secret = x_webhook_secret or authorization
    return await SupabaseWebhookService.process_storage_event(
        session=session,
        payload=payload,
        secret_header=secret,
    )
