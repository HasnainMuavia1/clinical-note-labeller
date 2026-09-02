import httpx
import respx

from app.specialty.npi import lookup_npi, resolve_specialty_from_npis

API = "https://npiregistry.cms.hhs.gov/api/"


def registry_payload(taxonomy_code, desc, enumeration_type="NPI-1"):
    return {"result_count": 1, "results": [{
        "enumeration_type": enumeration_type,
        "taxonomies": [{"code": taxonomy_code, "desc": desc, "primary": True}],
    }]}


@respx.mock
async def test_lookup_maps_taxonomy_to_specialty():
    respx.get(API).mock(return_value=httpx.Response(
        200, json=registry_payload("207RC0000X", "Cardiovascular Disease")))
    result = await lookup_npi("1234567893")
    assert result.found is True
    assert result.specialty == "Cardiology"
    assert result.is_individual is True


@respx.mock
async def test_lookup_of_unknown_npi_reports_not_found():
    respx.get(API).mock(return_value=httpx.Response(200, json={"result_count": 0, "results": []}))
    result = await lookup_npi("1234567893")
    assert result.found is False
    assert result.specialty is None


async def test_lookup_rejects_an_invalid_npi_without_calling_the_api():
    result = await lookup_npi("1234567890")
    assert result.found is False


@respx.mock
async def test_resolver_prefers_an_individual_over_an_organization():
    def handler(request):
        number = request.url.params["number"]
        if number == "1234567893":
            return httpx.Response(200, json=registry_payload("261QP2300X", "Primary Care Clinic", "NPI-2"))
        return httpx.Response(200, json=registry_payload("207N00000X", "Dermatology", "NPI-1"))

    respx.get(API).mock(side_effect=handler)

    result = await resolve_specialty_from_npis(["1234567893", "1023011178"])
    assert result is not None
    assert result.specialty == "Dermatology"


@respx.mock
async def test_resolver_returns_none_when_nothing_resolves():
    respx.get(API).mock(return_value=httpx.Response(200, json={"result_count": 0, "results": []}))
    assert await resolve_specialty_from_npis(["1234567893"]) is None
