# Paper Research Assistant

已实现 PDF 解析、清洗、按页分块、向量检索和批量检索评测。默认使用
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

## 检索评测

使用已缓存的模型评测当前标注集：

```powershell
python -m uv run python scripts/evaluate_retrieval.py --offline
```

模型尚未下载时先构建索引。可使用 `--questions`、`--index`、`--output`
指定文件，`--ks 1 2 3` 指定评测截断位置。默认 K 为 1、3、5，报告位于
`data/evaluation/retrieval_report.json`（不进入 Git）。对比实验应使用不同 `--output`
文件名保留各次结果。

报告包含逐题排名、完整片段、分数、来源、缺失证据及查询延迟，并汇总：

- `page_hit@K` / `page_recall@K`：正确文档与页码的命中及去重后的页面召回。
- `evidence_hit@K` / `evidence_coverage@K` / `complete_evidence@K`：至少一条引文命中、
  引文覆盖比例、全部标注引文覆盖。仅在同一片段内完整匹配引文，并核对文档和页码。
- `page_mrr@最大K` / `evidence_mrr@最大K`：第一条相关页面或证据的倒数排名均值。
- 按语言、问题类型汇总，以及先组内平均再按事实组平均的 `group_macro_metrics`。
- 全部查询的平均、P50、P95、最大延迟；加载及一次预热不计入时间。

无答案题独立列出，不参与上述相关性均值，也不生成拒答准确率。完整数据结构与
标注状态见 `eval_data/README.md`。若存在 `.meta.json`，会检查标注文件和源 PDF
哈希，文件发生变化时停止评测；人工复核变动后需同步更新元数据。

当前只有 4 个 chunks，Top-5 等同于返回全部语料，不能证明检索准确。
本次开发集基线为：证据 Hit@1=68.75%、Hit@3=100%、MRR@5=0.8333；
完整证据覆盖题目比例在 Top-1/3 分别为 62.5%/93.75%。这是 16 道可回答题
的逐题平均，数据待人工复核，并非独立论文检索性能证明。

## 三种检索方式对比

在同一分块、模型和标注集上，对比 Dense、BM25 与等权 RRF Hybrid：

```powershell
python -m uv run python scripts/compare_retrieval.py --offline
```

默认预热后重复三轮，并轮换方法执行顺序。三组分别保存 JSON，完整对比和
Markdown 报告位于 `data/evaluation/retrieval_comparison/`。参数固定为
BM25 `k1=1.5, b=0.75`，RRF 常数 60、每路候选最多 20 个；未用标注调参。
BM25 在原始片段上使用中文单字/相邻双字与英文单词，不使用 Embedding tokenizer，
也没有词典分词、翻译或词干化；零词项匹配时返回空列表。

当前开发集（16 道可答题）的证据 Hit@1：Dense 68.75%、BM25 81.25%、
Hybrid 81.25%；证据 MRR@5 分别为 0.8333、0.8438、0.9062。
BM25 的中文 Hit@1 为 92.86%，但两道英文跨语言题均无词项匹配；
Hybrid 的中文 Hit@1 为 78.57%，英文两题均命中。

按实验前固定的事实组指标，BM25 排第一；考虑中英文通用需求，本次建议选择
Hybrid，它未降低任何一种语言的 Hit@1/3，并改善了总体排序。
报告分别保留指标优胜者 `selection` 和使用建议 `bilingual_recommendation`，
避免把后者误当作预设评分结论。这仍是小型待复核开发集的结果，不代表所有论文。

单次查询可指定方法，默认仍保留 Dense，分数在不同方法之间不可直接比较：

```powershell
python -m uv run python scripts/search_dense_index.py "大雄为什么坚持训练？" --method dense --top-k 3 --offline
python -m uv run python scripts/search_dense_index.py "大雄为什么坚持训练？" --method bm25 --top-k 3
python -m uv run python scripts/search_dense_index.py "大雄为什么坚持训练？" --method hybrid --top-k 3 --offline
```

BM25 不加载向量模型；Hybrid 每次查询实际运行两路检索，不缓存查询向量。
若后续优化分块或模型，应保持标注不变，另存报告，并在未用于调参的新问题上复验。

## 上下文构造（LLM 接入前）

`src/paper_assistant/context_builder.py` 提供 `build_context()`，输入问题、按排名
排列的 `SearchResult`、消息 token 计数函数、输入预算和计数器名称，输出 `ContextBundle`：

- `messages`：可以供后续 LLM 适配器使用的 system/user 消息，规则与引用资料分开。
  user 内容为 JSON，包含问题及带 E1/E2 编号的原文、文件名、页码和来源 chunk ID。
- `citations`：证据编号到完整来源记录的映射，含检索排名、分数、原始词范围和字符范围。
- `excluded`：因重复 ID、空文本或预算不足而排除的 chunk 及原因。
- `prompt_token_count` / `max_input_tokens`：整个消息列表的计数与预算。

按检索顺序贪心选择完整片段，不摘要、不截断。只合并同一文档、文件、页面内，
字符区间重叠且重叠文字完全一致的片段；冲突会报错。不会跨页拼接，也不会
把有间隔的片段连成原文。旧的无字符偏移分块只对同页完全相同的正文去重。
E 编号在每次构造完成后分配，与原始来源一一映射；一条合并证据可关联多个 chunk。

本地预览默认采用 Hybrid，不调用 LLM：

```powershell
python -m uv run python scripts/preview_context.py "时间停止手表能暂停多长时间？" --offline
```

提示词和来源映射默认写入 `data/context/context_preview.json`（Git 忽略）。
可设置 `--method dense|bm25|hybrid`、`--top-k`、`--max-input-tokens` 和 `--output`。

**预览的计数使用现有 E5 tokenizer，只是估算，不是生成模型的实际计数。**
预览即使选择 BM25，也需要加载 E5 以取得计数用的 tokenizer。未来接入 LLM 时，
应传入该模型的计数函数，计算包括角色标记和生成前缀的 chat template，并先为
输出预留 token，再传入输入预算。不能直接把 E5 计数或保存的 chunk token 数当作
生成请求的 token 数。

空检索结果或所有片段超预算时，仍返回 `evidence=[]` 的合法提示词，
`has_evidence=false`。`has_evidence=true` 只说明选入了文本，不表示证据足够回答问题。
提示词要求证据不足时说明无法回答，但提示词约束与 JSON 转义不保证模型遵守，
后续仍需实现答案与引用验证。

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
