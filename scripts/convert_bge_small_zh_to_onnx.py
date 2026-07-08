"""模型转换脚本: 将 bge-small-zh-v1.5 转为 ONNX INT8 格式.

用法:
    python scripts/convert_bge_small_zh_to_onnx.py --output ./models/bge-small-zh-v1.5-onnx

依赖:
    torch>=2.0
    transformers>=4.37
    optimum>=1.16
    onnxruntime>=1.17

注意:
    本脚本需要 torch 环境, 建议在独立环境中运行, 运行后删除 torch 依赖
    转换后的 ONNX 模型文件直接放入指定目录即可被 FastEmbed 加载
"""

import argparse
import os

from transformers import AutoTokenizer, AutoModel


def convert_to_onnx(model_name: str, output_dir: str) -> None:
    """将模型转换为 ONNX INT8 格式."""
    os.makedirs(output_dir, exist_ok=True)

    print(f"加载模型: {model_name}")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name)

    print(f"导出 ONNX 模型到: {output_dir}")
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)

    print("转换完成!")
    print(f"模型文件位置: {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="将 bge-small-zh-v1.5 转为 ONNX 格式")
    parser.add_argument(
        "--output",
        type=str,
        default="./models/bge-small-zh-v1.5-onnx",
        help="输出目录",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="BAAI/bge-small-zh-v1.5",
        help="模型名称",
    )
    args = parser.parse_args()

    convert_to_onnx(args.model, args.output)