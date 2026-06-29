"""FastAPI entry point — 3-role architecture (super_admin · detailer · fabricator)."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from config import settings
from db import ensure_indexes, get_client
from routes.admin import router as admin_router
from routes.analyses import router as analyses_router
from routes.auth import router as auth_router
from routes.estimation import router as estimation_router
from routes.files import router as files_router
from routes.platform import router as platform_router
from routes.projects import router as projects_router
from routes.rfis import router as rfis_router
from seed import seed_admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s · %(name)s · %(levelname)s · %(message)s",
)
logger = logging.getLogger("structmind")

app = FastAPI(
    title="STRUCTMIND API",
    version="4.0.0",
    description="Structural Steel Intelligence Platform by 4XStruct Inc. — 3-role architecture.",
)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=settings.cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(files_router)
app.include_router(analyses_router)
app.include_router(rfis_router)
app.include_router(estimation_router)
app.include_router(platform_router)
app.include_router(admin_router)


@app.get("/api")
async def root():
    return {
        "service": "STRUCTMIND API",
        "version": "4.0.0",
        "status": "ok",
        "powered_by": "4XStruct Inc.",
        "architecture": "3-role · super_admin · detailer · fabricator",
    }


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def on_startup():
    await ensure_indexes()
    await seed_admin()
    logger.info("STRUCTMIND backend ready · v4.0.0 · 3-role architecture")


@app.on_event("shutdown")
async def on_shutdown():
    get_client().close()
