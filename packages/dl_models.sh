#!/bin/sh
# 一次性脚本: 在 python:3.12-slim 容器内下载 BGE 模型到 HF Hub 缓存格式
set -e
pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple huggingface_hub
echo "=== START DOWNLOAD ==="
python /dl_model.py "$1" /data
echo "=== DONE ==="
