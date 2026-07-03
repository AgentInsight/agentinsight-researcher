"""一次性脚本: 下载 BGE 模型到 HF Hub 缓存格式 (TEI 期望的格式)."""

import os
import sys

from huggingface_hub import snapshot_download

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

model_id = sys.argv[1]
cache_dir = sys.argv[2] if len(sys.argv) > 2 else "/data"

# 清理旧的扁平结构
flat = os.path.join(cache_dir, model_id.split("/")[-1])
if os.path.isdir(flat):
    import shutil

    shutil.rmtree(flat)
    print(f"Removed old flat dir: {flat}")

# 清理旧的 BAAI/<model>/ 结构
nested = os.path.join(cache_dir, *model_id.split("/"))
if os.path.isdir(nested):
    import shutil

    shutil.rmtree(nested)
    print(f"Removed old nested dir: {nested}")

print(f"Downloading {model_id} -> {cache_dir}")
path = snapshot_download(model_id, cache_dir=cache_dir)
print(f"Downloaded to: {path}")
print("Done.")
