# 五例真实模型评审包（人工待审）

本次为 2026-11-05 在旧版源码和集成版源码上的重新实跑，不是伪造历史上改造前已执行的记录。
每个初始研究请求 $0.10，每轮追问 $0.05。人工通过率尚未评定；自动采集成功不等于质量通过。
源材料由 Codex 做了 AI 辅助定位预核对，尚未取得人工签署。以下回答为真实模型实际返回。
请逐问题、逐轮核对关键事实、原文来源、版本、页码、实验条件、检索范围与缺口。无回答或未知用量均保留，不编造待评结论。

|版本|案例|执行状态|停止原因|token（含预留）|费用/预留 USD|未知预留|
|---|---|---|---|---:|---:|---:|
|baseline|tool-use-comparison|executed|insufficient_evidence|33273|0.03388725|0|
|baseline|feedback-and-evidence|executed|insufficient_evidence|26658|0.0246069|0|
|baseline|vision-transfer|executed|insufficient_evidence|20665|0.0210834|0|
|baseline|contextual-followups|executed|insufficient_evidence|67043|0.05936865|0.0151125|
|baseline|unsupported-business-cost|executed|insufficient_evidence|26700|0.02421465|0|
|integration|tool-use-comparison|executed|insufficient_evidence|31870|0.03259875|0|
|integration|feedback-and-evidence|executed|sufficient_evidence|30983|0.0320085|0|
|integration|vision-transfer|executed|insufficient_evidence|31362|0.03407025|0|
|integration|contextual-followups|executed|insufficient_evidence|67437|0.0672081|0|
|integration|unsupported-business-cost|executed|invalid_decision_limit|30641|0.03073575|0|

## baseline / tool-use-comparison

实际输入：{"topic": "language model tool use ReAct Toolformer", "questions": ["比较 ReAct 和 Toolformer 如何让语言模型使用工具。分别说明是否需要训练、工具调用如何产生，以及实验条件对比较结论的限制。请引用实际读到的正文与页码。"], "start_date": "2020-01-01", "end_date": "2024-01-01", "target_papers": 2, "max_cost_usd": 0.1, "source_mode": "cached_example"}

### 轮次 research

停止：insufficient_evidence；耗时：70.131433 秒；费用/预留：0.03388725；未知预留：0

ReAct is presented as a novel prompt-based paradigm that synergizes reasoning and acting in language models for general task solving, and the paper emphasizes a few-shot learning setup rather than training a separate tool-use policy. It also shows that actions are interleaved with reasoning traces to support decisions in interactive environments. [E1] [E12]

工具轨迹：list_documents(ok) → fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → search_documents(ok) → read_pages(ok) → search_documents(ok) → verify_citations(failed) → verify_citations(ok)

- [E1] 2210.03629v1，物理页 [3]：

> we introduce ReAct, a novel prompt-based paradigm to synergize reasoning and acting in language models for general task solving

- [E12] 2210.03629v1，物理页 [1]：

> This tight synergy between “acting” and “reasoning” allows humans to learn new tasks quickly and perform robust decision making or reasoning


## baseline / feedback-and-evidence

实际输入：{"topic": "language model agents ReAct Reflexion", "questions": ["ReAct 与 Reflexion v1 如何利用环境反馈？请比较反馈如何影响后续行动及跨次尝试的信息，并区分论文直接报告的结果和你的解释。证据不足请说明。"], "start_date": "2020-01-01", "end_date": "2024-01-01", "target_papers": 2, "max_cost_usd": 0.1, "source_mode": "cached_example"}

### 轮次 research

停止：insufficient_evidence；耗时：55.892367 秒；费用/预留：0.0246069；未知预留：0

【本轮未形成可显示回答，必须保留失败/未完成状态】

工具轨迹：ingest_paper(failed) → fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → read_pages(ok) → search_documents(ok)


## baseline / vision-transfer

实际输入：{"topic": "visual representation learning CLIP Vision Transformer", "questions": ["比较 Vision Transformer 和 CLIP 的训练监督信号及迁移使用方式。哪些差异有正文实验或方法描述支持？请保留实验条件，避免将不同评测设置下的数字直接排名。"], "start_date": "2020-01-01", "end_date": "2024-01-01", "target_papers": 2, "max_cost_usd": 0.1, "source_mode": "cached_example"}

### 轮次 research

停止：insufficient_evidence；耗时：50.303719 秒；费用/预留：0.0210834；未知预留：0

The visible evidence supports that ViT uses image-specific patch sequence modeling with a standard Transformer encoder and explores self-supervised masked patch prediction, but it does not yet fully support the requested comparison with CLIP’s training supervision and transfer usage. From the available ViT text, the paper states that it is “the direct application of Transformers to image recognition,” that it does “not introduce any image-specific inductive biases,” and that it “interpret[s] an image as a sequence of patches” processed by “a standard Transformer encoder” [E2]. It also says ViT performs downstream evaluation via “fine-tuning accuracy” and “few-shot accuracies” [E1], and that self-supervised pre-training improved ViT-B/16 to 79.9% on ImageNet, but remained below supervised pre-training by 4% [E2]. However, the current evidence set does not include CLIP’s method or transfer details, so the cross-paper comparison cannot be completed from the visible evidence alone.

工具轨迹：fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → search_documents(ok) → verify_citations(ok)

- [E2] 2010.11929v1，物理页 [9]：

> the direct application of Transformers to image recognition

- [E2] 2010.11929v1，物理页 [9]：

> we do not introduce any image-specific inductive biases into the architecture. Instead, we interpret an image as a sequence of patches and process it by a standard Transformer encoder

- [E1] 2010.11929v1，物理页 [5]：

> We report results on downstream datasets either through few-shot or fine-tuning accuracy.

- [E2] 2010.11929v1，物理页 [9]：

> With self-supervised pre-training, our smaller ViT-B/16 model achieves 79.9% accuracy on ImageNet, a significant improvement of 2% to training from scratch, but still 4% behind supervised pre-training.


## baseline / contextual-followups

实际输入：{"topic": "language model agents ReAct Reflexion", "questions": ["阅读 ReAct 与 Reflexion v1，概述它们如何处理交互中的错误及各自局限，给出正文证据。"], "start_date": "2020-01-01", "end_date": "2024-01-01", "target_papers": 2, "max_cost_usd": 0.1, "source_mode": "cached_example"}

### 轮次 research

停止：insufficient_evidence；耗时：59.222987 秒；费用/预留：0.0263754；未知预留：0

ReAct addresses interaction errors by interleaving reasoning with actions and observations, which makes its problem-solving trajectory more grounded, fact-driven, and trustworthy because it can use an external knowledge base. The paper also reports that this structure has a trade-off: it reduces flexibility in reasoning and can lead to more reasoning errors than CoT, including repetitive loops where the model keeps generating previous thoughts and actions. [E1] [E3]

By contrast, I still do not have visible full-text evidence for Reflexion v1 in the current session, so I cannot give a supported summary of how it handles errors or what its limitations are from the paper text alone.

工具轨迹：fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → verify_citations(failed) → read_pages(failed) → verify_citations(failed) → verify_citations(ok)

- [E1] 2210.03629v1，物理页 [6]：

> the problem solving trajectory of ReAct is more grounded, fact-driven, and trustworthy, thanks to the access of an external knowledge base

- [E3] 2210.03629v1，物理页 [9]：

> ReAct substantially outperforms IM-style prompting (71 vs. 53 overall success rate), with consistent advantages on five out of six tasks.

### 轮次 turn-36689c37b74342d2a4c702c0

停止：insufficient_evidence；耗时：35.64100000000326 秒；费用/预留：0.01788075；未知预留：0

后者（Reflexion v1）与前者（ReAct）相比，处理错误的机制不同之处在于：ReAct把错误处理嵌在“思考—行动—观察”的交替轨迹里，依赖外部知识库与反馈来纠错；其思维模式是“free-form and unlimited”“abstract and diverse”“sparse”，并且能通过观察结果决定何时再思考 [E1]。不过，仅凭当前入库材料，我只能直接核实 ReAct 的错误恢复与局限，尚未取得 Reflexion v1 的正文证据，因此不能可靠概述后者的具体错误处理机制差异。

工具轨迹：search_documents(ok) → list_documents(ok) → verify_citations(ok) → read_pages(ok) → verify_citations(ok)

- [E1] 2210.03629v1，物理页 [8]：

> free-form and unlimited

- [E1] 2210.03629v1，物理页 [8]：

> abstract and diverse

- [E1] 2210.03629v1，物理页 [8]：

> sparse

### 轮次 turn-55d06ff378644be192d1264f

停止：openai_connection_error；耗时：0.502534 秒；费用/预留：0.0151125；未知预留：0.0151125

【本轮未形成可显示回答，必须保留失败/未完成状态】

工具轨迹：


## baseline / unsupported-business-cost

实际输入：{"topic": "language model tool use ReAct Toolformer", "questions": ["仅根据 ReAct 和 Toolformer 的正文，能否给出在企业部署它们各自每月维护成本的美元金额及核算方法？如果取得的证据不足，请明确说明检索范围与缺口，不要推算不存在的成本数据。"], "start_date": "2020-01-01", "end_date": "2024-01-01", "target_papers": 2, "max_cost_usd": 0.1, "source_mode": "cached_example"}

### 轮次 research

停止：insufficient_evidence；耗时：66.362394 秒；费用/预留：0.02421465；未知预留：0

The current evidence is insufficient to state any monthly enterprise maintenance cost in dollars for ReAct or Toolformer, or to give a valid cost calculation method. The available evidence only shows that Toolformer reports no perplexity cost from adding API calls: "Adding API calls comes without a cost in terms of perplexity" [E6]. However, that is not a dollar-denominated maintenance cost, and no evidence here gives monthly enterprise deployment cost figures or a核算方法 for either paper. Therefore, within the available full-text evidence from these two papers, no monthly maintenance cost amount can be extracted, and the cost methodology remains unstated.

工具轨迹：fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → verify_citations(ok)

- [E6] 2302.04761v1，物理页 [8]：

> Adding API calls comes without a cost in terms of perplexity


## integration / tool-use-comparison

实际输入：{"topic": "language model tool use ReAct Toolformer", "questions": ["比较 ReAct 和 Toolformer 如何让语言模型使用工具。分别说明是否需要训练、工具调用如何产生，以及实验条件对比较结论的限制。请引用实际读到的正文与页码。"], "start_date": "2020-01-01", "end_date": "2024-01-01", "target_papers": 2, "max_cost_usd": 0.1, "source_mode": "cached_example", "request_id": "evaluation-23c0620b518e4839befaea429f8e9939"}

### 轮次 research

停止：insufficient_evidence；耗时：85.471621 秒；费用/预留：0.03259875；未知预留：0

ReAct 和 Toolformer 都是在“让语言模型使用外部工具”这一点上做文章，但路径不同。

- ReAct：从已读正文看，它把“reasoning traces”和“task-specific actions”交错生成；作者明确说这种方法“generate both reasoning traces and task-specific actions in an interleaved manner”，让推理轨迹帮助模型“induce, track, and update action plans”，而动作则让模型“interface with and gather additional information from external sources such as knowledge bases or environments” [E11]。这说明 ReAct 的工具使用不是通过额外训练得到的工具调用模块，而是通过提示/推理-行动交替的方式，在推理过程中发出动作并读取外部信息。证据里还指出它在 QA 和 fact verification 上借助 Wikipedia API，减少幻觉和错误传播 [E11]。不过当前可见正文只显示到摘要段，缺少方法/实验页的完整细节，因此只能确认“交错式推理+动作”的框架，不能细说更细的实现步骤。

- Toolformer：当前我还没有可直接引用的 Toolformer 正文证据，因此不能据此确认它是否需要训练、工具调用如何产生，以及实验条件。现有上下文只表明该文已被抓取为 fulltext_ready，但缺少当前会话中可见的正文引文来支持结论。基于已可验证证据，我不能把它和 ReAct 做等强度对比。

比较上的限制：仅就现有证据，ReAct 的结论来自摘要可见正文片段 [E11]，而 Toolformer 缺少可核验正文证据，所以目前只能对 ReAct 下结论，对 Toolformer 只能说明证据不足；这也意味着无法可靠比较二者在“是否需要训练”和“工具调用如何产生”上的差异，尤其不能直接比较实验设置与效果。

综上，当前证据足以支持：ReAct 通过交错生成推理与动作来使用工具，且在问答/事实核查中接入 Wikipedia API；但对 Toolformer 还缺少可核验正文，因此不足以完成二者的严格比较。

工具轨迹：list_documents(ok) → fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → read_pages(ok) → verify_citations(ok)

- [E11] 2210.03629v1，物理页 [1]：

> we explore the use of LLMs to generate both reasoning traces and task-specific actions in an interleaved manner, allowing for greater synergy between the two: reasoning traces help the model induce, track, and update action plans as well as handle exceptions, while actions allow it to interface with and gather additional information from external sources such as knowledge bases or environments.

- [E11] 2210.03629v1，物理页 [1]：

> on question answering (HotpotQA) and fact verification (Fever), ReAct overcomes prevalent issues of hallucination and error propagation in chain-of-thought reasoning by interacting with a simple Wikipedia API


## integration / feedback-and-evidence

实际输入：{"topic": "language model agents ReAct Reflexion", "questions": ["ReAct 与 Reflexion v1 如何利用环境反馈？请比较反馈如何影响后续行动及跨次尝试的信息，并区分论文直接报告的结果和你的解释。证据不足请说明。"], "start_date": "2020-01-01", "end_date": "2024-01-01", "target_papers": 2, "max_cost_usd": 0.1, "source_mode": "cached_example", "request_id": "evaluation-0e4fe08209c9419cad3216f0b97f4047"}

### 轮次 research

停止：sufficient_evidence；耗时：64.59929 秒；费用/预留：0.0320085；未知预留：0

ReAct 与 Reflexion 都利用了环境反馈，但方式不同。

- 对 ReAct，现有可见证据只显示它把“环境中的观察/上下文”持续带入后续决策：论文在设置说明中说，智能体在文本环境里基于当前状态与轨迹历史形成上下文来决定下一步动作，并在每轮失败或完成后终止；这支持“观察会影响后续动作”的基本机制，但我当前没有看到足够的正文证据来逐句证明它如何在具体任务中改写后续动作策略。[E11]
- 对 Reflexion，证据更直接。论文明确说明：若初次回答错误，智能体会被允许“self-reflect on its past searches, lookups, observations, working memory, and final answer”，随后重置环境并用新的 working memory 再试一次。[E12] 论文还指出它可以“improve on its performance not only between trials 1 and 2 but between every consecutive trial”，并给出例子：第一次试验中它先找到台灯但计划有缺陷，反思后记住“desk lamp was found on desk 1”，下一次试验据此制定更准确的计划并更快成功。[E10]
- 论文直接报告的结果方面，Reflexion 在 HotPotQA 的 100 题实验中，第一轮 base ReAct 34% 准确率、Reflexion 32% 准确率，但随着多轮试验，Reflexion 在 7 轮内整体表现超过 base ReAct。[E12]

需要区分的是：我上面的前两条包含对“反馈如何影响后续动作”的解释，其中对 ReAct 的具体影响机制证据不足，只能确认它把观察纳入上下文并据此行动；对 Reflexion 则可以直接确认反思内容会写入动态工作记忆并用于下一轮尝试。[E11][E12][E10]

工具轨迹：fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → read_pages(ok) → search_documents(ok) → fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → verify_citations(ok)

- [E11] 2303.11366v1，物理页 [3]：

> In text-based environments,st considersct, which is the context given to the agent based on its current state and trajectory history, consisting of past observations and actions.

- [E12] 2303.11366v1，物理页 [6]：

> if the agent provided an incorrect answer, the agent was given a chance to self-reflect on its past searches, lookups, observations, working memory, and final answer, reset the environment, and try again with a new working memory.

- [E10] 2303.11366v1，物理页 [7]：

> other hand, the Reflexion agent was able to improve on its performance not only between trials 1 and 2 but between every consecutive trial, demonstrating that the growing, dynamic working memory promoted learning through trial.


## integration / vision-transfer

实际输入：{"topic": "visual representation learning CLIP Vision Transformer", "questions": ["比较 Vision Transformer 和 CLIP 的训练监督信号及迁移使用方式。哪些差异有正文实验或方法描述支持？请保留实验条件，避免将不同评测设置下的数字直接排名。"], "start_date": "2020-01-01", "end_date": "2024-01-01", "target_papers": 2, "max_cost_usd": 0.1, "source_mode": "cached_example", "request_id": "evaluation-2a8f831b2120498d9fc7610ea16792ea"}

### 轮次 research

停止：insufficient_evidence；耗时：52.75 秒；费用/预留：0.03407025；未知预留：0

基于当前可见正文证据，我只能确认 Vision Transformer（ViT）与 CLIP 在训练监督信号和迁移方式上存在部分可核实差异；但 CLIP 的正文证据尚未获得，因此无法完成两者的充分对比。

已能确认的 ViT 相关点：
1) 训练监督信号上，ViT 不是依赖图像文本配对监督，而是作为“纯 transformer”直接作用于图像 patch 序列，并在大规模图像数据上预训练后迁移到下游分类任务。证据显示其“pure transformer applied directly to sequences of image patches”并且“when pre-trained on large amounts of data and transferred to multiple mid-sized or small image recognition benchmarks”表现良好 [E10]。
2) 迁移方式上，ViT 的正文明确写到它先在 ImageNet-21k 或 JFT-300M 上预训练，再“transferred to tasks with fewer datapoints”，并在 ImageNet、CIFAR-100、VTAB 等多基准上评估；这说明它的迁移主要是从大规模监督/预训练到较小数据集的分类迁移 [E15]。
3) 实验条件上，ViT 的最佳结果来自“pre-trained at sufficient scale”这一条件，且文中明确提示其效果依赖大规模训练数据（14M-300M images）[E15]。因此不能把这些分数直接与别的设置下模型横向排名。

当前无法确认的 CLIP 相关点：
- 训练监督信号是否为图像-文本对齐、对比学习或其他形式；
- 迁移使用方式是否是零样本分类、线性探测或微调；
- 与 ViT 的具体实验条件对照。

因此，现阶段只能说：ViT 的正文支持其“图像 patch 直接建模 + 大规模图像预训练 + 迁移到分类基准”的路线，而 CLIP 的对应正文证据缺失，不能据此做完整并列比较或数字排名。

工具轨迹：fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → search_documents(ok) → read_pages(ok) → search_documents(ok) → read_pages(failed) → read_pages(ok) → verify_citations(ok)

- [E10] 2010.11929v1，物理页 [1]：

> a pure transformer applied directly to sequences of image patches can perform very well on image classification tasks

- [E10] 2010.11929v1，物理页 [1]：

> When pre-trained on large amounts of data and transferred to multiple mid-sized or small image recognition benchmarks (ImageNet, CIFAR-100, VTAB, etc.), Vision Transformer (ViT) attains excellent results

- [E15] 2010.11929v1，物理页 [2]：

> When pre-trained on the public ImageNet-21k dataset or the in-house JFT-300M dataset, ViT approaches or beats state of the art on multiple image recognition benchmarks.

- [E15] 2010.11929v1，物理页 [2]：

> the best model reaches the accuracy of 88.55% on ImageNet, 90.72% on ImageNet-ReaL, 94.55% on CIFAR-100, and 77.63% on the VTAB suite of 19 tasks.


## integration / contextual-followups

实际输入：{"topic": "language model agents ReAct Reflexion", "questions": ["阅读 ReAct 与 Reflexion v1，概述它们如何处理交互中的错误及各自局限，给出正文证据。"], "start_date": "2020-01-01", "end_date": "2024-01-01", "target_papers": 2, "max_cost_usd": 0.1, "source_mode": "cached_example", "request_id": "evaluation-83e5a687a7504416ad933b51793f273f"}

### 轮次 research

停止：insufficient_evidence；耗时：64.882996 秒；费用/预留：0.0342855；未知预留：0

ReAct 在正文中把错误处理和局限都讲得比较明确：它的轨迹“more grounded, fact-driven, and trustworthy” because of access to an external knowledge base, but this same structure also “reduces its flexibility in formulating reasoning steps,” which “lead[s] to more reasoning error rate than CoT” [E1]. 文中还指出一种 ReAct 特有的失误模式：模型会“repetitively generates the previous thoughts and actions”，即在循环里反复生成先前步骤，说明它会卡住、难以跳出循环 [E1]. 另外，作者的人工分析表明，ReAct 相比 CoT 更少出现 hallucination，且能通过外部检索降低错误，但代价是推理灵活性下降 [E4].

Reflexion 这篇当前可见证据不足以支持我对其“如何处理交互中的错误及各自局限”的完整概括；现有材料里缺少可直接引用的正文段落来说明它的错误修正机制、何时失败、以及论文作者如何讨论局限。因此，我不能负责任地把 Reflexion 的细节补全到和 ReAct 同等粒度。

工具轨迹：fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → search_documents(ok) → fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → search_documents(ok) → verify_citations(failed) → verify_citations(ok)

- [E1] 2210.03629v1，物理页 [6]：

> the problem solving trajectory of ReActis more grounded, fact-driven, and trustworthy, thanks to the access of an external knowledge base. • While interleaving reasoning, action and observation steps improvesReAct’s groundedness and trustworthiness, such a structural constraint also reduces its flexibility in formulating reasoning steps, leading to more reasoning error rate than CoT

- [E1] 2210.03629v1，物理页 [6]：

> there is one frequent error pattern specific to ReAct, in which the model repetitively generates the previous thoughts and actions

- [E4] 2210.03629v1，物理页 [5]：

> Hallucination is a serious problem for CoT, resulting in much higher false positive rate than ReAct (14% vs. 6%) in success mode, and make up its major failure mode (56%).

### 轮次 turn-f0cfe2c3947a4d558f0ad197

停止：sufficient_evidence；耗时：26.942431 秒；费用/预留：0.01218135；未知预留：0

Reflexion compared with ReAct changes error handling from immediate, step-by-step external reasoning to post-episode self-correction with memory. In the Reflexion paper, the method is described as giving the agent “dynamic memory and self-reflection capabilities” and a “simple heuristic that enables the agent to pinpoint hallucination instances, avoid repetition in action sequences” [E1]. The paper also says it equips an agent with “a self-reflective LLM and a simple heuristic for detecting hallucination and inefficient action execution” so it can “learn from its own mistakes” [E2]. By contrast, the same paper reports that the ReAct-only agent improves much more slowly: on the example discussed, the Reflexion agent succeeds after 2 trials, while the ReAct-only approach solves the task on the 6th trial [E3].

工具轨迹：search_documents(ok) → search_documents(ok) → verify_citations(ok)

- [E1] 2303.11366v1，物理页 [1]：

> dynamic memory and self-reflection capabilities to enhance its existing reasoning trace and task-specific action choice abilities. To achieve full automation, we introduce a straightforward yet effective heuristic that enables the agent to pinpoint hallucination instances, avoid repetition in action sequences

- [E2] 2303.11366v1，物理页 [2]：

> we equip an LLM-based agent with a self-reflective LLM and a simple heuristic for detecting hallucination and inefficient action execution

- [E3] 2303.11366v1，物理页 [7]：

> The agent succeeds after 2 trials, or 1 reflection. In the ReAct-only approach, the agent solves the task on the 6th trial.

### 轮次 turn-dfac21a94dbd440b97f412c6

停止：sufficient_evidence；耗时：32.068912 秒；费用/预留：0.02074125；未知预留：0

就 ReAct 而言，当前证据不足以支持“在所有交互任务中都更好”。论文的结论只明确说它在“a diverse set of experiments on multi-hop question-answering, fact checking, and interactive decision-making tasks”上“leads to superior performance with interpretable decision traces”，这支持的是若干具体任务上的优越性，而不是对所有交互任务的普遍结论 [E9]。论文同时给出边界：复杂任务且动作空间很大时，需要更多 demonstrations，容易超出 in-context learning 的输入长度限制 [E9]；另外，虽然“interleaving reasoning, action and observation steps improves ReAct’s groundedness and trustworthiness”，但这种结构约束“reduces its flexibility in formulating reasoning steps”，并且会带来“more reasoning error rate than CoT” [E11]。论文还指出一种特有失败模式是模型反复生成前一步 thoughts and actions，说明在某些场景下它并不能稳定跳出循环 [E11]。因此，依据正文，ReAct 的实验结果更适合表述为：在若干基准和任务上表现优于对照方法，但其效果受任务类型、动作空间大小和示例数量/长度约束影响，不能据此推出对所有交互任务都更好 [E9][E11]。

工具轨迹：read_pages(failed) → read_pages(ok) → read_pages(ok) → search_documents(ok) → search_documents(ok) → verify_citations(ok)

- [E9] 2210.03629v1，物理页 [10]：

> Through a diverse set of experiments on multi-hop question-answering, fact checking, and interactive decision-making tasks, we show that ReAct leads to superior performance with interpretable decision traces.

- [E11] 2210.03629v1，物理页 [6]：

> While interleaving reasoning, action and observation steps improvesReAct’s groundedness and trustworthiness, such a structural constraint also reduces its flexibility in formulating reasoning steps, leading to more reasoning error rate than CoT.


## integration / unsupported-business-cost

实际输入：{"topic": "language model tool use ReAct Toolformer", "questions": ["仅根据 ReAct 和 Toolformer 的正文，能否给出在企业部署它们各自每月维护成本的美元金额及核算方法？如果取得的证据不足，请明确说明检索范围与缺口，不要推算不存在的成本数据。"], "start_date": "2020-01-01", "end_date": "2024-01-01", "target_papers": 2, "max_cost_usd": 0.1, "source_mode": "cached_example", "request_id": "evaluation-4e6cf1b4e316446d802609fbd0876494"}

### 轮次 research

停止：invalid_decision_limit；耗时：64.10999999998603 秒；费用/预留：0.03073575；未知预留：0

【本轮未形成可显示回答，必须保留失败/未完成状态】

工具轨迹：list_documents(ok) → fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → fetch_paper(ok) → ingest_paper(ok) → search_documents(ok) → verify_citations(failed) → verify_citations(failed)

## 固定原文入口

- react-v1：[2210.03629v1](https://arxiv.org/abs/2210.03629v1)，31 页，SHA256 `6ec7c9ba12a5a7462afaaa759b9d4186e522fcb502ef480afd7fa1821960a576`；本地 PDF：`<LOCAL_WORKSPACE>\tmp\worktrees\agent-v1-issue-8\data\paper_cache\ddddfc34e37811c8b2f4f05e60e983a074b54bf34756ddbb31186e39786170b2\6ec7c9ba12a5a7462afaaa759b9d4186e522fcb502ef480afd7fa1821960a576.pdf`
- toolformer-v1：[2302.04761v1](https://arxiv.org/abs/2302.04761v1)，17 页，SHA256 `6d7483d94653008e40c2058a1c22441c92e3713dae278b6361e8efc447c99522`；本地 PDF：`<LOCAL_WORKSPACE>\tmp\worktrees\agent-v1-issue-8\data\paper_cache\e709d2dfe511d6eb813668c80fa1def32e9b0a159f62d4a583b2e4db72f2bf0e\6d7483d94653008e40c2058a1c22441c92e3713dae278b6361e8efc447c99522.pdf`
- reflexion-v1：[2303.11366v1](https://arxiv.org/abs/2303.11366v1)，17 页，SHA256 `965a720f42e0318facf0de5d1b9d7a135ded227284fc615ca5b3ec1763c46066`；本地 PDF：`<LOCAL_WORKSPACE>\tmp\worktrees\agent-v1-issue-8\data\paper_cache\05365dbf2943c97146aeaf236bc0451f621f3098d56d11fd85265a83226c243a\965a720f42e0318facf0de5d1b9d7a135ded227284fc615ca5b3ec1763c46066.pdf`
- vit-v1：[2010.11929v1](https://arxiv.org/abs/2010.11929v1)，21 页，SHA256 `d33feb34fcf7aea256439a7f487197dcbb896e31a9d86e30e37d252672c4da78`；本地 PDF：`<LOCAL_WORKSPACE>\tmp\worktrees\agent-v1-issue-8\data\paper_cache\16dbdbf9d8d0133eb7ea7735107f2806d8819af91fe4a225d575bcca1e10e7d5\d33feb34fcf7aea256439a7f487197dcbb896e31a9d86e30e37d252672c4da78.pdf`
- clip-v1：[2103.00020v1](https://arxiv.org/abs/2103.00020v1)，48 页，SHA256 `6478b6e571a7d6fcd846d8ef77bfd60c285f1986abb8f475eedc43de403074f5`；本地 PDF：`<LOCAL_WORKSPACE>\tmp\worktrees\agent-v1-issue-8\data\paper_cache\3f8ac9988c703131efbe2eaf63e51c7dd355dc50a584187fa18241f53908660c\6478b6e571a7d6fcd846d8ef77bfd60c285f1986abb8f475eedc43de403074f5.pdf`

## 如何完成真正的人工验收

填写两个 reviews.template.json 中真实评审者、带时区的时间、五项 checks、逐轮所有关键结论与支持理由。引用模板只是实际模型引文，不保证正确或足够，必须增删核查。
缺正文、缺回答或未核对内容维持 not_reviewed，不能为满足字段要求造引文；失败亦需如实记录。
填好后使用集成分支 evaluation_review review/compare 生成派生结果。原始采集目录保持不变。
至少 4/5 且完整人工评审后才能讨论关闭 #2、#8、#1；当前三个 Issue 均保持打开。
