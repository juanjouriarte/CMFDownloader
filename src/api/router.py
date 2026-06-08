from __future__ import annotations

from fastapi import APIRouter

from .categories import router as categories_router
from .funds import router as funds_router
from .investment_funds import router as fi_router
from .rentability import router as rentability_router
from .shareholders import router as shareholders_router
from .admins import router as admins_router

router = APIRouter()
router.include_router(funds_router)
router.include_router(fi_router)
router.include_router(rentability_router)
router.include_router(categories_router)
router.include_router(shareholders_router)
router.include_router(admins_router)
