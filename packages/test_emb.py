"""快速测试 embeddings 连接."""

import asyncio
import sys

sys.path.insert(0, "/app")

from src.rag.embeddings import get_embeddings_client


async def test():
    c = get_embeddings_client()
    try:
        r = await c.embed_texts(["hello world"])
        print("OK, dim:", len(r[0]) if r else 0)
    except Exception as e:
        print("Error type:", type(e).__name__)
        print("Error repr:", repr(e))
        print("Error str:", str(e))


asyncio.run(test())
