import asyncio

import pytest

from app.parsing.ocr_gate import acquire_ocr_slot, reset_ocr_gate


class FakeRedis:
    def __init__(self):
        self.n = 0

    def incr(self, _key):
        self.n += 1
        return self.n

    def decr(self, _key):
        self.n = max(0, self.n - 1)
        return self.n

    def expire(self, _key, _ttl):
        return True


@pytest.fixture(autouse=True)
def _reset():
    reset_ocr_gate()
    yield
    reset_ocr_gate()


async def test_ocr_gate_caps_inflight_slots(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("app.parsing.ocr_gate._redis", lambda: fake)
    monkeypatch.setattr("app.parsing.ocr_gate._limit", lambda: 5)

    inflight = 0
    peak = 0

    async def one(_):
        nonlocal inflight, peak
        async with acquire_ocr_slot():
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0.05)
            inflight -= 1

    await asyncio.gather(*(one(i) for i in range(12)))
    assert peak == 5
