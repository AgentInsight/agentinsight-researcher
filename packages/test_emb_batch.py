"""测试大批量 embeddings 调用 (模拟 context_manager 场景)."""

import asyncio
import sys

sys.path.insert(0, "/app")

from src.rag.embeddings import get_embeddings_client


async def test():
    c = get_embeddings_client()
    # 模拟 context_manager: query + 20 个 chunks
    texts = ["你好测试"] + [
        f"这是第 {i} 个文档块的内容, 长度约 1000 字符。" + "x" * 950 for i in range(20)
    ]
    print(f"Total texts: {len(texts)}, total chars: {sum(len(t) for t in texts)}")
    try:
        r = await c.embed_texts(texts)
        print("OK, count:", len(r), "dim:", len(r[0]) if r else 0)
    except Exception as e:
        print("Error type:", type(e).__name__)
        print("Error repr:", repr(e))
        print("Error str:", str(e))
        print("Error args:", e.args)


asyncio.run(test())
