"""测试 SSE 流式 chat completions API."""
import json
import sys
import urllib.request

body = json.dumps({
    "model": "agentinsight-researcher",
    "messages": [{"role": "user", "content": "用一句话介绍量子计算"}],
    "stream": True
}).encode("utf-8")

req = urllib.request.Request(
    "http://localhost:8066/v1/chat/completions",
    data=body,
    headers={"Content-Type": "application/json; charset=utf-8"},
    method="POST",
)

chunks = 0
content_chars = 0
finish_reason = None
first_chunk_time = None
import time
start = time.time()

with urllib.request.urlopen(req, timeout=180) as resp:
    print(f"STATUS: {resp.status}")
    print(f"CONTENT-TYPE: {resp.headers.get('Content-Type')}")
    buf = b""
    for line in resp:
        buf += line
        if buf.endswith(b"\n\n"):
            text = buf.decode("utf-8", errors="replace")
            buf = b""
            for evt in text.split("\n"):
                if evt.startswith("data: "):
                    data = evt[6:]
                    if data.strip() == "[DONE]":
                        continue
                    try:
                        obj = json.loads(data)
                        chunks += 1
                        if first_chunk_time is None:
                            first_chunk_time = time.time() - start
                        if obj.get("choices"):
                            delta = obj["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                content_chars += len(content)
                            fr = obj["choices"][0].get("finish_reason")
                            if fr:
                                finish_reason = fr
                    except json.JSONDecodeError:
                        pass

elapsed = time.time() - start
print(f"TOTAL_CHUNKS: {chunks}")
print(f"CONTENT_CHARS: {content_chars}")
print(f"FINISH_REASON: {finish_reason}")
print(f"FIRST_CHUNK_LATENCY: {first_chunk_time:.2f}s" if first_chunk_time else "FIRST_CHUNK_LATENCY: None")
print(f"TOTAL_TIME: {elapsed:.2f}s")
