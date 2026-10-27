from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.auth import router as auth_router
from app.api.v1.departments import router as departments_router
from app.api.v1.folders import router as folders_router
from app.api.v1.public import router as public_router
from app.api.v1.resources import router as resources_router
from app.api.v1.search import router as search_router
from app.api.v1.sharing import router as sharing_router
from app.api.v1.storage import router as storage_router
from app.api.v1.telemetry import router as telemetry_router
from app.api.v1.uploads import router as uploads_router
from app.api.v1.users import router as users_router
from app.api.v1.webhooks import router as webhooks_router

api_v1_router = APIRouter()

api_v1_router.include_router(admin_router)

api_v1_router.include_router(auth_router)
api_v1_router.include_router(users_router)
api_v1_router.include_router(departments_router)
api_v1_router.include_router(folders_router)
api_v1_router.include_router(uploads_router)
api_v1_router.include_router(storage_router)
api_v1_router.include_router(search_router)
api_v1_router.include_router(sharing_router)
api_v1_router.include_router(resources_router)
api_v1_router.include_router(public_router)
api_v1_router.include_router(webhooks_router)
api_v1_router.include_router(telemetry_router)
