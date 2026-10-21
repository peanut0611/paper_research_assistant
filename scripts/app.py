"""Serve the local paper question page without additional UI dependencies."""

import argparse
import os
from pathlib import Path

from paper_assistant.web_server import PaperHTTPServer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--local-models-only", action="store_true",
                        help="Use cached embedding weights; OpenAI still requires network access")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(root / "tmp/tiktoken-cache"))
    if args.local_models_only:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        with PaperHTTPServer(args.port, root / "data/index/dense") as server:
            print(f"资料问答页面：http://127.0.0.1:{args.port}", flush=True)
            print("按 Ctrl+C 停止。首次提问需要加载模型；每次提交会调用 OpenAI。", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        print("\n页面服务已停止。")
    except OSError:
        parser.exit(1, "无法启动本地服务。请检查端口占用，或使用 --port 8502。\n")


if __name__ == "__main__":
    main()
