from fastapi import APIRouter

from . import approvals, codes, jobs, specialties, system

api_v1 = APIRouter(prefix="/api/v1")
for _module in (system, specialties, codes, jobs, approvals):
    api_v1.include_router(_module.router)
