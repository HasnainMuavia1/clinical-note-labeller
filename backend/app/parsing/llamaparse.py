from __future__ import annotations

import asyncio
from pathlib import Path

from ..config import get_settings


async def llamaparse_text(path: Path) -> str:
    """Parse a document with LlamaParse. Requires egress; worker-only."""
    from llama_parse import LlamaParse

    settings = get_settings()
    parser = LlamaParse(api_key=settings.llama_cloud_api_key, result_type="text", verbose=False)
    documents = await asyncio.to_thread(parser.load_data, str(path))
    return "\n".join(doc.text for doc in documents)
