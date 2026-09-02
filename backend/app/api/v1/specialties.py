from fastapi import APIRouter, Depends

from ...security import require_api_key
from ...specialty.taxonomy import SPECIALTIES, folder_name

router = APIRouter(tags=["specialties"], dependencies=[Depends(require_api_key)])


@router.get("/specialties")
def list_specialties() -> dict:
    items = [{"name": s, "folder": folder_name(s)} for s in SPECIALTIES]
    return {"items": items, "count": len(items)}
