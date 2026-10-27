"""Serve the literature research Agent without additional UI dependencies."""

import argparse
import os
import sys

from paper_assistant.web.server import PaperHTTPServer
from paper_assistant.config import PROJECT_ROOT


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--local-models-only", action="store_true",
                        help="Use cached embedding weights; OpenAI still requires network access")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    root = PROJECT_ROOT
    os.chdir(root)
    if args.local_models_only:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        with PaperHTTPServer(args.port, project_root=root) as server:
            print(f"文献研究 Agent：http://127.0.0.1:{args.port}", flush=True)
            print("按 Ctrl+C 停止。页面加载不调用模型；开始研究后调用 OpenAI。", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        print("\n页面服务已停止。")
    except OSError:
        parser.exit(1, "无法启动本地服务。请检查端口占用，或使用 --port 8502。\n")


if __name__ == "__main__":
    main()
