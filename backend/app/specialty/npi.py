from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from ..codes.patterns import is_valid_npi
from .taxonomy import specialty_for_taxonomy

REGISTRY_URL = "https://npiregistry.cms.hhs.gov/api/"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


@dataclass(frozen=True)
class NpiResult:
    npi: str
    specialty: str | None
    taxonomy_code: str | None
    is_individual: bool
    found: bool


async def lookup_npi(npi: str, client: httpx.AsyncClient | None = None) -> NpiResult:
    if not is_valid_npi(npi):
        return NpiResult(npi, None, None, False, False)

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        response = await client.get(REGISTRY_URL, params={"version": "2.1", "number": npi})
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001 - a registry failure must not fail the job
        return NpiResult(npi, None, None, False, False)
    finally:
        if owns_client:
            await client.aclose()

    results = payload.get("results") or []
    if not results:
        return NpiResult(npi, None, None, False, False)

    record = results[0]
    taxonomies = record.get("taxonomies") or []
    primary = next((t for t in taxonomies if t.get("primary")), taxonomies[0] if taxonomies else None)
    code = (primary or {}).get("code")
    return NpiResult(
        npi=npi,
        specialty=specialty_for_taxonomy(code),
        taxonomy_code=code,
        is_individual=record.get("enumeration_type") == "NPI-1",
        found=True,
    )


async def resolve_specialty_from_npis(npis: list[str]) -> NpiResult | None:
    """Prefer an individual clinician's primary taxonomy over an organization's."""
    if not npis:
        return None
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        results = await asyncio.gather(*(lookup_npi(n, client) for n in npis[:5]))

    resolved = [r for r in results if r.found and r.specialty]
    if not resolved:
        return None
    individual = next((r for r in resolved if r.is_individual), None)
    return individual or resolved[0]
