"""命令行问答演示（pipeline 的薄封装）。

用法：python3 src/qa_demo.py "你的问题" [默认地区]
LLM（DeepSeek）已接入；无网络 / 无 key 时自动降级为规则引擎。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import answer_structured, format_text

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('用法：python3 src/qa_demo.py "你的问题" [默认地区，如 上海]')
    region = sys.argv[2] if len(sys.argv) > 2 else None
    print(format_text(answer_structured(sys.argv[1], default_region=region)))
