# Paper Research Assistant

已实现 PDF 解析、清洗、按页分块和向量检索。默认使用
`intfloat/multilingual-e5-small` 处理中英文及混合文本；尚未接入 LLM。
跨语言检索质量需要进一步评测。

## Windows / PowerShell 运行

需要 Python >= 3.11，先在项目目录执行：

```powershell
python -m pip install uv
python -m uv sync --python 3.12
python -m uv run pytest -q
```

把带文本层的 PDF 放进 `paper/`，构建并查询：

```powershell
python -m uv run python scripts/build_dense_index.py --recursive
python -m uv run python scripts/search_dense_index.py "时间停止手表能暂停多长时间？" --top-k 3
```

首次构建需联网下载模型，默认缓存到 `data/models/`。默认索引输出到
`data/index/dense/`，查询会读取索引中记录的模型名称。更换模型或论文后需重新构建索引。
模型、生成数据和索引不进入 Git。

## 分块与来源追溯

- 默认每块最多 384 tokens，包含 `passage:` 前缀和特殊 token；相邻块重叠
  64 个正文 token。参数为 `--max-tokens` 和 `--overlap-tokens`，替代原来的词数参数。
- 分块使用与 Embedding 相同的 tokenizer；每块截取原文后再次计数，编码前仍有超限检查。
- 不跨页，每块保留文件名、页码和 chunk ID，以及清洗后页内字符范围
  `char_start` / `char_end`（从 0 开始、右端不包含）。`token_count` 包含前缀和特殊 token。
- 旧的按词分块 Python 接口仍保留；中文应使用字符范围追溯，词数不表示中文分词结果。
- `chunk_papers.py` 也改用 token 分块，因此首次运行同样需要加载模型。

```powershell
python -m uv run python scripts/chunk_papers.py --max-tokens 384 --overlap-tokens 64
```

## 测试和限制

单元测试使用临时生成的 PDF 和假 tokenizer / embedder，不需要下载模型，
也不依赖 `paper/` 中的文件。受限环境可将临时目录指定在项目内：

```powershell
New-Item -ItemType Directory -Path tmp -Force | Out-Null
python -m uv run pytest -q --basetemp=tmp/pytest-local -p no:cacheprovider
```

扫描版 PDF 暂无 OCR；PDF 的阅读顺序、表格和断词仍依赖文本层质量。
清洗支持 IEEE 页脚，以及页末密集重复的 `WATERMARK` 及其截断片段，
并非通用水印识别。检索返回原文证据，不生成答案，也没有无证据拒答机制。
