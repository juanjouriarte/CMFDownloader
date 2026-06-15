from __future__ import annotations

from fastapi import APIRouter

from .categories import router as categories_router
from .mutual_funds import router as mutual_funds_router
from .investment_funds import router as fi_router
from .rentability import router as rentability_router
from .shareholders import router as shareholders_router
from .admins import router as admins_router
from .industry import router as industry_router
from .ref_codes import router as ref_codes_router

router = APIRouter()
router.include_router(mutual_funds_router)
router.include_router(fi_router)
router.include_router(rentability_router)
router.include_router(categories_router)
router.include_router(shareholders_router)
router.include_router(admins_router)
router.include_router(industry_router)
router.include_router(ref_codes_router)
