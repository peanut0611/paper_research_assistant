# Paper Research Assistant 项目交接说明

> 上下文构造更新：新增 `context_builder.py` 和 `scripts/preview_context.py`，
> 已支持 E 编号、来源映射、同页重叠片段按准确字符区间合并、重复/空文本处理、
> 全消息预算与排除原因记录。核心计数器可替换，预览仅使用 E5 tokenizer 估算，
> 接 LLM 时必须换成生成模型的 chat-template 计数并预留输出预算。
> 默认预览走 Hybrid，输出 `data/context/context_preview.json`（Git 忽略），
> 已用当前中文材料验证 3 个检索片段合并为 2 条带来源证据。最新测试 95 passed。
> 尚未调用 LLM；下一步实现模型适配、答案生成和引用验证。

> 三路检索实验更新：已新增 `lexical_retrieval.py`（BM25 与 RRF）、
> `retrieval_comparison.py` 和 `scripts/compare_retrieval.py`。保持原有 4 chunks、
> 向量模型和 20 题标注不变，完成三轮实测。完整报告在
> `data/evaluation/retrieval_comparison/comparison.md` / `comparison.json`。
> 16 可答题证据 Hit@1：Dense 68.75%、BM25 81.25%、Hybrid 81.25%；
> MRR@5：0.8333 / 0.8438 / 0.9062。按预设事实组指标 BM25 第一，但
> 英文跨语言两题均失败；中英文通用建议 Hybrid。两种判定在报告中分别记录。
> 参数固定，未按开发集调参；当前结果不代表真实多论文语料。默认查询仍为 Dense，
> `search_dense_index.py --method hybrid` 可使用建议方案。最新测试：78 passed。

> 2026-10-18 更新：默认模型已改为 `intfloat/multilingual-e5-small`。
> 两个分块/索引 CLI 默认按 token 切分（384 tokens，正文重叠 64 tokens），
> 使用模型 tokenizer，最大长度包含前缀及特殊 token；新增清洗后页内字符范围。
> CLI 参数改为 `--max-tokens` / `--overlap-tokens`，旧按词 Python 接口保留。
> 测试改用自动生成的临时 PDF，不再依赖用户可替换的 `paper/` 语料。
> 当前语料已替换为中文 `paper/paper.pdf`，旧英文论文已由用户移除。
> 最新验证：41 项单元测试通过；真实多语言模型已下载，默认索引已重建。
> 当前中文 PDF 为 2 页、4 chunks、384 维；完整输入 token 数为 384/384/344/218。
> 两个中文问题及英文检索样例已做冒烟验证，这不等于完成正式检索评测。
> 运行说明以 README.md 为准；下文的英文模型、19 chunks、29 passed 等为旧基线记录。

> 检索评测更新：新增 `src/paper_assistant/evaluation.py` 和
> `scripts/evaluate_retrieval.py`，支持页级/证据级指标、完整证据覆盖、截断 MRR、
> 延迟、按语言/类别及事实组平均、逐题失败详情。无答案题单列，不虚构拒答指标。
> 初始数据在 `eval_data/`：20 题（16 可答、4 无答案），仍待人工复核。
> 已使用本地多语言模型跑通，报告在 `data/evaluation/retrieval_report.json`（Git 忽略）。
> 开发集逐题平均证据 Hit@1=0.6875、Hit@3=1.0、MRR@5=0.8333；
> 完整证据覆盖题目比例 @1=0.625、@3=0.9375。当前仅 4 chunks，@5 返回全部索引。
> 最新单元测试为 63 passed；未修改检索参数或标注来提高指标。下一步先分析失败题，
> 完成人工复核并扩展真实论文评测语料，再考虑检索改进和 LLM 接入。

请接手并继续开发以下项目。请先阅读本说明和现有代码，不要重新从零搭建。

## 一、项目基本信息

项目路径：

```text
/Users/peanut/Code/paper_research_assistant
```

GitHub：

```text
https://github.com/peanut0611/paper_research_assistant.git
```

当前分支：

```text
main
```

截至 2026-10-18，工作区是干净的，没有未提交修改。

最近提交：

```text
03430b0 Document chunk and embedding
31ca7ff Recursive query of all PDF files
ca15619 Paper content ingestion
95d45a4 Initialize the repository
```

Python 环境：

```text
Python 3.14.6
项目要求 Python >= 3.11
包管理器：uv
```

主要依赖：

```text
numpy >= 2.0, < 3.0
pypdf >= 6.0, < 7.0
sentence-transformers >= 6.0, < 7.0
pytest >= 8.0, < 10.0
```

当前测试状态：

```text
29 passed
```

在 Codex 沙箱中运行测试时，项目内旧 `.uv-cache` 可能不可写，可以使用：

```bash
UV_CACHE_DIR=/tmp/paper-research-assistant-uv-cache uv run pytest -q
```

测试出现的 `PytestCacheWarning` 仅表示沙箱无法更新 `.pytest_cache`，不是测试失败，也不是业务代码问题。

---

# 1. 项目目标

最终目标是构建一个面向 AI/ML 论文的 Agentic Research Assistant。

目标用户是需要阅读、检索和比较多篇学术论文的学生或初级研究人员。

系统最终应当能够：

1. 导入多篇论文 PDF。
2. 对论文进行解析、清洗、分块和索引。
3. 根据用户问题检索相关证据。
4. 基于证据生成答案。
5. 为关键结论提供论文名称、页码和原文引用。
6. 没有充分证据时拒绝回答。
7. 完成跨论文比较。
8. 后期通过单 Agent 执行查询拆解、检索、比较和引用验证。

项目最终定位可以描述为：

```text
Agentic Research Assistant with Evidence-Grounded Hybrid RAG
```

但开发顺序必须保持：

```text
PDF ingestion
→ Chunking
→ Dense Embedding/Retrieval
→ Retrieval Evaluation
→ BM25/Hybrid/Reranker
→ LLM Answer Generation
→ Citation Verification
→ Single-Agent Workflow
```

不要现在直接引入 Multi-Agent、MCP、Memory、RLHF 或复杂前端。

---

# 2. 当前代码架构

```text
paper_research_assistant/
├── paper/
│   └── SLM-based_Hybrid_Retrieval_....pdf
├── src/
│   └── paper_assistant/
│       ├── __init__.py
│       ├── pdf_parser.py
│       ├── text_cleaner.py
│       ├── corpus_ingestion.py
│       ├── chunking.py
│       └── embedding.py
├── scripts/
│   ├── inspect_pdf.py
│   ├── ingest_papers.py
│   ├── chunk_papers.py
│   ├── build_dense_index.py
│   └── search_dense_index.py
├── tests/
│   ├── test_pdf_parser.py
│   ├── test_text_cleaner.py
│   ├── test_corpus_ingestion.py
│   ├── test_chunking.py
│   └── test_embedding.py
├── data/
│   ├── extracted/
│   ├── chunks/
│   ├── index/
│   └── models/
├── pyproject.toml
├── uv.lock
└── .gitignore
```

`data/` 下的生成数据、模型和索引均被 `.gitignore` 忽略。Git 当前只跟踪代码、测试、配置和一篇测试 PDF。新环境 clone 后需要重新生成中间数据、下载模型并构建索引。

---

# 3. 已完成的功能

## 3.1 PDF 逐页解析

`pdf_parser.py` 定义：

```python
@dataclass(frozen=True)
class PageDocument:
    document_id: str
    filename: str
    page_number: int
    text: str
```

主要接口：

```python
parse_pdf(path, clean=True)
```

功能包括：

- 验证文件存在。
- 验证 `.pdf` 扩展名。
- 使用 `pypdf.PdfReader` 逐页提取文本。
- 页码从 1 开始。
- 保留文件名和 `document_id`。
- 支持 `clean=False` 返回原始提取结果，便于调试。

## 3.2 文本清洗

`text_cleaner.py` 实现：

```python
clean_page_text()
remove_page_boilerplate()
```

已经处理：

- Unicode 规范化。
- 非标准连字符转为普通 `-`。
- Windows/macOS 换行统一。
- PDF 视觉换行合并。
- 重复空格清理。
- 连字符附近异常空格。
- 保留真正的段落边界。
- IEEE 下载授权水印。
- IEEE 版权信息。
- 页面末尾独立页码。
- 重复会议页脚。

清洗策略是保守的。例如：

```text
Retrieval -
Augmented
```

会修复为：

```text
Retrieval-Augmented
```

但类似：

```text
latency bud gets
```

不会自动猜测为 `budgets`，因为规则式合并可能破坏正常文本。

## 3.3 批量 PDF ingestion

`corpus_ingestion.py` 定义：

```python
BlankPage
IngestionFailure
IngestionReport
```

主要接口：

```python
ingest_pdf_directory(directory, recursive=False)
write_pages_jsonl(pages, output_path)
```

支持：

- 扫描目录中的所有 PDF。
- 可选递归扫描。
- 稳定排序。
- 单篇 PDF 失败时隔离错误，不中断整个批次。
- 记录成功文件、空白页和失败文件。
- 导出每页一条记录的 JSONL。

脚本：

```bash
uv run python scripts/ingest_papers.py
```

默认输出：

```text
data/extracted/pages.jsonl
```

## 3.4 Chunking

`chunking.py` 定义：

```python
@dataclass(frozen=True)
class TextChunk:
    chunk_id: str
    document_id: str
    filename: str
    page_number: int
    chunk_index: int
    word_start: int
    word_end: int
    text: str
```

当前 chunking 策略：

```text
单页独立切分
最大 180 words
Overlap 36 words
```

主要接口：

```python
chunk_page()
chunk_pages()
write_chunks_jsonl()
```

Chunk ID 格式：

```text
document-id::page-0001::chunk-0001
```

这样每个检索结果都能追溯到论文、页码、chunk 编号和词范围。

默认不跨页切分，原因是第一版优先保证 citation 页码明确。

脚本：

```bash
uv run python scripts/chunk_papers.py
```

默认输出：

```text
data/chunks/chunks.jsonl
```

当前测试论文：

```text
4 pages
→ 19 chunks
```

## 3.5 Embedding

`embedding.py` 使用：

```text
intfloat/e5-small-v2
```

该模型：

- 面向英文语义检索。
- 输出 384 维向量。
- 最大输入长度为 512 tokens。
- 要求文档增加 `passage:` 前缀。
- 要求查询增加 `query:` 前缀。

主要抽象：

```python
class TextEmbedder(Protocol)
class SentenceTransformerEmbedder
```

`TextEmbedder` 只要求：

```python
model_name
embed_documents()
embed_query()
```

这样以后可以替换为 BGE、OpenAI Embeddings 或测试用假模型，而不用修改索引和检索逻辑。

模型缓存被限制在：

```text
data/models/
```

代码在导入 Hugging Face 库前配置：

```text
HF_HOME
HF_HUB_CACHE
HF_XET_CACHE
XDG_CACHE_HOME
```

这是为了避免模型和下载日志散落到用户主目录，也解决沙箱环境无法写默认缓存的问题。

## 3.6 Token 超限保护

E5 最大长度为 512 tokens。

早期使用：

```text
250 words + 50 overlap
```

实际发现有 chunk 达到：

```text
639 tokens
```

这会导致模型静默截断。

因此默认参数调整为：

```text
180 words + 36 overlap
```

实际验证：

```text
最大 token 数：478
模型限制：512
超限 chunks：0
```

`SentenceTransformerEmbedder` 会在编码前显式 tokenize。如果任何文档或问题超过模型限制，会抛出错误，而不是允许模型静默丢失文本。

## 3.7 Dense Index

`embedding.py` 定义：

```python
@dataclass(frozen=True)
class DenseIndex:
    chunks: tuple[TextChunk, ...]
    embeddings: NDArray[np.float32]
    model_name: str
```

向量和 chunks 按行严格对应：

```text
embeddings[0] ↔ chunks[0]
embeddings[1] ↔ chunks[1]
...
```

索引持久化为：

```text
data/index/dense/
├── embeddings.npy
├── chunks.jsonl
└── manifest.json
```

`manifest.json` 记录：

```text
model_name
dimension
chunk_count
normalized
```

加载索引时会验证：

- embeddings 必须是二维矩阵。
- 向量行数必须等于 chunk 数。
- 向量维度必须与 manifest 一致。
- chunk 数必须与 manifest 一致。

## 3.8 Dense Retrieval

主要接口：

```python
build_dense_index()
search_dense_index()
save_dense_index()
load_dense_index()
```

文档和查询向量均执行 L2 normalization，因此：

```text
cosine_similarity = document_vector @ query_vector
```

当前使用 NumPy 完成 brute-force dense retrieval：

```python
scores = index.embeddings @ query_vector
ranked_indexes = np.argsort(-scores)[:top_k]
```

脚本：

```bash
uv run python scripts/build_dense_index.py
```

查询：

```bash
uv run python scripts/search_dense_index.py \
  "Which retrieval method achieved the highest recall under resource-constrained settings?" \
  --top-k 3
```

真实验证结果中，前两名都来自第 4 页，并包含：

```text
SLM-based method
Recall 0.102224
over 3x higher Recall
```

当前向量矩阵：

```text
shape = (19, 384)
```

向量范数约为：

```text
0.99999994 ～ 1.0
```

---

# 4. 重要设计决策及原因

## 4.1 先做纯 Retrieval，再接 LLM

LLM 只能根据检索到的证据生成答案。如果 retrieval 不可靠，LLM 只会更流畅地生成错误答案。

因此必须先建立：

```text
可运行 baseline
→ 正式检索评测
→ 检索改进
→ LLM generation
```

## 4.2 不直接使用 LangChain/LlamaIndex 封装核心流程

这个项目的目的之一是理解并展示：

- PDF ingestion。
- Chunking。
- Embedding。
- Cosine similarity。
- Metadata/citation mapping。
- Retrieval evaluation。

不要过早把核心逻辑隐藏到框架 API 后面。后期可以引入框架，但必须能解释底层流程。

## 4.3 第一版按页独立 chunk

优点：

- 页码引用明确。
- 调试简单。
- 不会出现一个 chunk 对应多个页码。
- 容易验证 evidence。

代价：

- 跨页句子会被拆开。
- 无法利用跨页上下文。

这是有意的 baseline，后续再通过评测决定是否改为跨页或 section-aware chunking。

## 4.4 Word-window 是 baseline，不是最终策略

当前使用固定词数窗口是为了简单、透明、可重复。

以后可能尝试：

- Sentence-aware chunking。
- Section-aware chunking。
- Token-aware chunking。
- Parent-child retrieval。
- 跨页 chunking。

任何改动都必须通过相同评测集比较，不能只凭感觉认为“更先进”。

## 4.5 使用 E5 的 query/passsage 前缀

E5 是非对称检索模型，文档和查询角色不同：

```text
passage: 文档内容
query: 用户问题
```

前缀不可随意删除，否则检索效果可能下降。

## 4.6 模型和索引必须严格绑定

不能用 E5 编码文档，再用其他 embedding 模型编码查询。即使维度相同，它们也不在同一个向量空间。

因此 `DenseIndex` 保存 `model_name`，查询时会检查模型是否一致。

## 4.7 当前不引入向量数据库

目前只有 19 个 chunks，NumPy brute-force 足够：

- 逻辑透明。
- 易于测试。
- 无额外服务。
- 适合建立 baseline。

论文库扩大后再考虑 FAISS、Qdrant、Chroma、Milvus 等。

---

# 5. 当前正在处理的问题

当前正准备进入“正式 Retrieval Evaluation”阶段。

尚未实现评测代码，也没有正式的人工标注问题集。

在接入 LLM 之前，需要建立：

```text
eval_data/retrieval_questions.jsonl
```

建议每条记录包含：

```json
{
  "id": "q001",
  "question": "Which retrieval method achieved the highest recall?",
  "relevant_pages": [4],
  "evidence_quote": "the SLM-based method achieves over 3x higher Recall (0.102224)",
  "answer": "The SLM-based hybrid method.",
  "category": "fact"
}
```

第一版建议准备 15～20 个问题：

- 直接事实问题。
- 数值问题。
- 语义改写问题。
- 方法问题。
- 对比问题。
- 论文中无法回答的问题。

正确证据必须人工从 PDF 中确认，不能让检索系统自己生成 gold label。

需要计算：

```text
Hit@1
Hit@3
Hit@5
Recall@K
MRR
查询延迟
```

---

# 6. 已知 bug / 技术债

## PDF 和文本处理

- 不支持扫描版 PDF OCR。
- 双栏阅读顺序依赖 PDF 自带文本层。
- 表格、公式、图片没有结构化提取。
- 页眉页脚规则目前主要针对测试用 IEEE 论文。
- 某些 PDF 可能出现错误拆词，例如 `bud gets`；当前保守策略不会自动猜测修复。
- `document_id` 当前只是文件 stem。递归目录中如果两篇 PDF 同名，可能产生 ID 冲突。
- `inspect_pdf.py` 当前会打印每页完整清洗文本，长论文输出可能非常多，后续可以增加 preview 长度参数。

## Chunking

- 可能从句子中间切断。
- 跨页句子被拆开。
- 没有章节识别。
- 标题、作者、参考文献仍可能进入 chunks。
- 最后一块可能很短，造成较高重复率。
- 默认按 words 切分，而非真正 token-aware；当前仅通过编码前检查防止超限。
- `test_chunk_real_corpus_respects_word_limit` 仍显式测试旧参数 `250/50`，虽然不是 bug，但容易与当前默认 `180/36` 混淆，后续应改为测试默认参数或明确注明是在验证自定义参数。

## Embedding / Retrieval

- 当前模型 `e5-small-v2` 只适合英文。
- 查询和文档前缀默认写死为 E5 规则；如果更换模型，需要同步配置前缀。
- 当前向量索引是 NumPy 全量扫描，不适合大规模语料。
- 尚无检索评测集。
- 尚无分数阈值和无答案拒绝机制。
- Dense Retriever 即使问题无答案也一定会返回 Top-K。
- 尚无 BM25、Hybrid Search 或 Reranker。
- 没有记录索引构建配置中的 chunk size、overlap、语料哈希或构建时间。当前 manifest 只包含模型、维度、chunk 数和 normalized 状态。
- 索引加载验证结构一致性，但没有校验文件内容哈希。
- `DenseIndex` 是 frozen dataclass，但内部 NumPy array 本身仍可变。
- 查询脚本每次运行都会重新加载 embedding 模型，后续服务化时应复用单个模型实例。
- Hugging Face 未认证下载会出现 rate-limit warning，但不影响已缓存模型的正常使用。

## 产品功能

- 尚未接入 LLM。
- 尚未生成答案。
- 尚未实现引用格式化。
- 尚未实现 citation verification。
- 尚未实现 API 或 Web UI。
- 尚未实现 Agent。
- 尚未加入 Docker、CI、日志和正式 README。

---

# 7. 下一步计划

## Priority 1：建立 Retrieval Evaluation

1. 创建 `eval_data/retrieval_questions.jsonl`。
2. 人工标注正确页码和 evidence quote。
3. 新增 `evaluation.py`。
4. 新增 `scripts/evaluate_retrieval.py`。
5. 计算 Hit@1/3/5、Recall@K、MRR 和 latency。
6. 输出逐题失败分析。
7. 将配置与结果保存为 JSON。

建议第一阶段目标，但不是行业硬标准：

```text
Hit@1 >= 70%
Hit@3 >= 85%
Hit@5 >= 90%
MRR >= 0.75
```

## Priority 2：根据失败案例改进 Retrieval

每次只改变一个变量：

```text
当前 180/36 chunking
→ Sentence-aware chunking
→ 不同 chunk size
→ BM25
→ Dense + BM25 Hybrid
→ Reranker
```

必须使用同一评测集比较，避免同时修改多个变量而无法归因。

## Priority 3：接入 LLM

只有 retrieval baseline 和评测完成后才接入。

LLM 第一版目标：

```text
Question
→ Top-K chunks
→ Context construction
→ LLM
→ Answer
→ filename + page number + evidence quote
```

要求：

- 只能根据 context 回答。
- 无证据时明确拒答。
- 每个关键结论附引用。
- 保存 retrieved chunks，方便调试 hallucination。

## Priority 4：扩展为 Agentic Research Assistant

在普通 RAG 稳定后再加入单 Agent：

```text
拆解问题
→ 选择检索策略
→ 多次检索
→ 跨论文比较
→ 检查证据
→ 生成结构化报告
```

先做单 Agent，不要马上做 Multi-Agent。

---

# 8. 代码风格和修改方式要求

这是一个学习型但目标为简历级别的项目。开发方式应保持：

1. 每次只完成一个清晰、可验证的小步骤。
2. 修改前说明本步目标和设计原因。
3. 修改后运行测试，并汇报真实结果。
4. 不要一次加入大量框架和功能。
5. 不要只给最终代码；需要解释核心逻辑、输入输出和设计取舍。
6. 优先使用 Python 标准库、清晰的数据类和小函数。
7. 使用类型注解。
8. 使用 dataclass 表示结构化数据。
9. 业务逻辑放在 `src/paper_assistant/`。
10. CLI 参数和打印逻辑放在 `scripts/`。
11. 每个新模块应配套 pytest 测试。
12. 测试不能依赖每次联网下载真实模型；embedding 单元测试使用 `FakeEmbedder`。
13. 真实模型应另外执行集成验证。
14. 保留 metadata 和 citation traceability，不能在处理中丢失文件名、页码或 chunk ID。
15. 对可能静默损坏结果的问题主动报错，例如 token 截断、模型不匹配或索引错位。
16. 不要为了“先进”过早加入 Agent、向量数据库或复杂框架。
17. 修改必须限制在项目目录：
    
```text
/Users/peanut/Code/paper_research_assistant
```

18. 保留用户已有修改，不进行 destructive reset。
19. 不自动提交 Git，除非我明确要求。
20. 新对话开始后，应先检查 `git status`、项目结构和测试状态，再修改代码。

我希望采用配对编程式指导：你可以实际修改代码，但需要帮助我理解每一步，而不是替我完成全部工作后只给结果。

---

# 9. 重要文件及职责

## 核心代码

`src/paper_assistant/pdf_parser.py`

- 定义 `PageDocument`。
- 单篇 PDF 逐页解析。
- 原始/清洗模式切换。

`src/paper_assistant/text_cleaner.py`

- Unicode 和空白规范化。
- 连字符修复。
- IEEE 页码、版权信息和下载水印清理。

`src/paper_assistant/corpus_ingestion.py`

- 批量 PDF 扫描。
- 错误隔离。
- 空白页记录。
- 页级 JSONL 导出。

`src/paper_assistant/chunking.py`

- 定义 `TextChunk`。
- 单页 word-window chunking。
- Overlap。
- Chunk JSONL 导出。

`src/paper_assistant/embedding.py`

- `TextEmbedder` 接口。
- `SentenceTransformerEmbedder`。
- Token 上限检查。
- 向量归一化。
- Dense index 建立、保存和读取。
- Cosine similarity Top-K 检索。

## 命令行脚本

`scripts/inspect_pdf.py`

- 查看单篇 PDF 原始或清洗后的逐页文本。

`scripts/ingest_papers.py`

- 批量导入论文。
- 输出 `pages.jsonl`。

`scripts/chunk_papers.py`

- 批量导入并生成 chunks。
- 输出 `chunks.jsonl`。

`scripts/build_dense_index.py`

- 完整执行 ingestion、chunking、embedding 和索引保存。

`scripts/search_dense_index.py`

- 加载索引。
- 编码问题。
- 返回 Top-K chunks、分数和页码。

## 测试

`tests/test_pdf_parser.py`

- PDF 页数、页码、metadata 和原始模式。

`tests/test_text_cleaner.py`

- 换行、Unicode 连字符、段落和页脚清理。

`tests/test_corpus_ingestion.py`

- 批量导入、损坏 PDF、目录错误和 JSONL。

`tests/test_chunking.py`

- Overlap、metadata、ID、参数校验和 JSONL。

`tests/test_embedding.py`

- Fake embeddings。
- 归一化。
- 排名。
- 持久化 round-trip。
- 零向量。
- Top-K 参数。
- Token 超限保护。

---

# 10. 新对话继续开发时必须知道的上下文

1. 当前项目不是一个普通 PDF chatbot，而是要发展成可评测、有引用、可扩展为 Agent 的论文研究助手。
2. 当前已完成到 Dense Retrieval，不是只完成了 embedding。
3. 当前没有 LLM，也没有最终答案生成。
4. 当前最重要的下一步是 Retrieval Evaluation，不应直接跳到 Agent。
5. 当前测试语料只有一篇 4 页英文 IEEE 论文。
6. 当前结果是 19 个 chunks、每个 384 维。
7. 当前默认 chunk 参数为 180 words、36 overlap。
8. 当前 embedding 模型为 `intfloat/e5-small-v2`。
9. E5 文档必须加 `passage:`，问题必须加 `query:`。
10. 当前 token 最大值已验证为 478，低于模型的 512 上限。
11. 当前索引位于 `data/index/dense/`，但 `data/` 被 Git 忽略；新环境需要重新构建。
12. 模型缓存位于 `data/models/`，也不进入 Git。
13. 测试 PDF 本身已被 Git 跟踪。
14. 当前 Git 工作区干净，分支为 `main`。
15. 当前全量测试为 `29 passed`。
16. 在 Codex 沙箱中如 `.uv-cache` 不可写，使用 `/tmp` 作为 `UV_CACHE_DIR`。
17. 不要根据一次成功查询就宣称检索质量已经得到证明；正式指标尚未建立。
18. 后续所有检索改进都必须基于同一人工标注评测集进行可重复对比。
19. 保持核心逻辑可解释，避免用高级框架掩盖底层过程。
20. 开始下一步前，请先重新检查仓库状态和运行测试，然后提出 Retrieval Evaluation 的小步实现计划。
