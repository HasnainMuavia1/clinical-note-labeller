from fastapi import APIRouter, Depends, Query

from ...codes.dictionaries import get_dictionaries
from ...security import require_api_key

router = APIRouter(tags=["codes"], dependencies=[Depends(require_api_key)])


@router.get("/codes/lookup")
def lookup(code: str = Query(min_length=2, max_length=10)) -> dict:
    dicts = get_dictionaries()
    source = dicts.contains(code)
    normalized = code.strip().upper()
    return {
        "code": normalized,
        "found": source is not None,
        "source": source,
        "description": (dicts.descriptions.get(normalized)
                        or dicts.descriptions.get(normalized.replace(".", ""))),
    }
