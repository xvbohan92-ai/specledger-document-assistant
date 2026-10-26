# SpecLedger v0.3 字段映射微调

已完成真实权重训练、验证集选点、固定 OCR 前后对照、失败分析、工程范围检查及独立待审数据库联调。OCR/Layout/RapidOCR 权重没有训练；模型只选择字段名称，数值、单位和页码继续来自原文。

## 可核查的前后结果

| 固定评测 | 同一预训练基座 | 微调检查点 |
| --- | ---: | ---: |
| 新标签正确数 | 37/48（77.08%） | 40/48（83.33%） |
| 标签 Macro F1 | 0.733935 | 0.795579 |
| 干扰标签错误接收 | 6/6 | 6/6 |
| 新 PDF 字段值／状态正确 | 54/112（48.21%） | 62/112（55.36%） |
| 值／状态及预期页码同时正确 | 54/112 | 62/112 |
| 新 PDF 七字段完全正确 | 0/16 | 0/16 |

原始微调观察到标签正确率 **+6.25 个百分点**、文档字段正确率 **+7.14 个百分点**，但干扰字段拒绝仍失败。不能只报告增幅而省略失败。规则基线在该别名挑战中为 1/112，主要因为新标签刻意不在旧别名表中；主要训练对照采用相同架构和相同描述文本的预训练模型。

[训练记录](../reports/finetuning-training.json) · [完整原始对照](../reports/finetuning-evaluation.json) · [实际 OCR 行与页码](../reports/finetuning-ocr-evidence.json) · [逐文档 CSV](../docs/figures/field-finetuning-documents.csv)

![对照与选点](../docs/figures/field-finetuning-evaluation.png)

![原始模型混淆矩阵](../docs/figures/field-label-confusion.png)

## 后续工程诊断，单独报告

初次测试暴露了把输出电压当成输入电压、包装重量当净重、储存温度当最高工作温度、序列号当型号的问题。随后根据有限 schema 和验证干扰标签加入显式范围检查；原始模型和微调模型使用完全相同的检查。

| 复用同一测试集的事后诊断 | 基座＋范围检查 | 微调＋范围检查 |
| --- | ---: | ---: |
| 字段值／状态正确 | 89/112 | 104/112 |
| 七字段完全正确文档 | 3/16 | 8/16 |
| 正常文档仍需复核 | 10/12 | 6/12 |
| 问题文档被标为待确认 | 0/4 | 0/4 |

**这组诊断是在查看初次测试失败后追加的，复用了测试数据。** 它不是新的独立留出分数，也不能把 104/112 全部归因于权重训练。缺失字段同样会拦住问题文档，因此 0/4 也不证明模型正确理解了所有异常。待确认仍然不是已批准；系统没有自动入库。

模型与规则仍有不足，保留全部 8 个剩余字段错误和 6 份正常文档的复核需求：[追加诊断](../reports/finetuning-scope-diagnostics.json)、[失败清单](../reports/finetuning-analysis.json)。根据这些失败继续训练时，需要另建新测试集。

## 数据与边界

- 闭集八类：7 个设备字段及 `ignore`。训练 96、验证 32、测试 48 条字段标签，分别每类 12／4／6 条。原始标签字符串去大小写后不跨划分重复，但字段概念、词汇与作者共享；没有供应商级、主题级或外部标注者隔离。
- 模型输入是“固定字段说明＋原始标签文本”，不包含型号值、数字、单位、PDF 文件名、预期答案或训练划分。输出只决定 schema 键；原有 `parse_quantity` 以 Decimal 验证原文数值。
- 新建 24 份自制英文 PDF、48 页：8 份验证文档，16 份测试文档；各划分数字／扫描对半。使用表格、冒号列表及横版表格三种简单版式；扫描件无隐藏文字层，其中一份轻微旋转和降低对比度。
- 16 份新测试文档共 112 个字段槽位，另外带有相近量纲的干扰行。4 份问题文档覆盖未知单位、数值范围、AC/DC 冲突和缺失字段。数值、状态和页码真值在生成器中明确指定，未调用待测单位解析器生成答案。
- PDF 里的别名重复使用同一批标签，各文档的结果存在相关性；24 份构造 PDF 不能当作 24 个独立真实厂商样本。数据和分级判断没有外部人工复核。
- 未解决状态必须匹配预期错误类型；任意缺失或拒绝不能都算正确。来源指标检查预期页码，未测高亮框精度。

冻结数据与指纹：[标签 manifest](datasets/manifest.json)、[文档与真值](datasets/documents.json)。PDF 是原文样本，不含任何真实设备认证信息。

## 训练与对照协议

基座为官方 `cross-encoder/ms-marco-MiniLM-L6-v2`，固定 revision `233902d25c440f23af6f7d6e94d2946bac0bee0a`。复用原始单 logit 分类头，对八个固定候选说明的分数计算 softmax 交叉熵。没有换成随机初始化的八分类头，也不是 LoRA/QLoRA。

- 22,713,601 个可训练参数；CPU、2 个 Torch 线程；6 轮、每轮 12 次更新，共 72 次更新；batch 8、AdamW、学习率 2e-5、weight decay 0.01、梯度范数上限 1、seed 20261026。
- 用完整 32 条验证标签的 Macro F1 选点，并列取更早轮次；选中第 6 轮，验证正确 27/32、Macro F1 0.801587，基座为 25/32、0.753608。测试没有参与检查点选择。
- 同一分词器、同一字段说明、128-token 上限；训练样本逐对检查无截断。检查点 SHA-256 与相对初始化权重的 L2 变化（约 0.73734）记录在训练报告，证明实际参数发生变化。
- 每份新 PDF 只捕获一次实际 Docling/RapidOCR 结果，三组复用同一组 OCR 行、页面和位置。OCR 异常不丢弃；本次 24 份均完成解析。独立验证文档用于模型预热，加载与转换计时分开。
- OCR 的共享 P50 约 2.57 秒；映射计时会受重复标签缓存影响，不能把亚毫秒缓存读取写成纯神经推理速度，也不声称线上 SLA 或普遍加速。

环境与模型：[运行环境](../reports/finetuning-environment.json)、[OCR 固定模型清单](ocr-model-spec.json)。依赖版本与 `uv.lock` 一致；本次使用已有本地模型，没有调用收费 API。

## 系统集成与核查

`SPECLEDGER_FIELD_MAPPER=rules|base|finetuned` 显式选择路径。默认 `rules` 保留原有流程；模型缺失、文件指纹变化或字段描述变化会报错，避免静默下载或切换。原文候选保留标签、数值、页码、位置和模型权重指纹；界面区分“模型映射＋单位校验”与人工修订。

字段范围检查是独立于模型权重的有限规则；排除显式输出额定值、含包装重量、储存／最低温度、序列号等，不把拒绝能力外推到任意未知字段。值和单位只从原始行复制，不让模型补写或修正数字。

原有 8 份历史测试 PDF 重新做了真实 OCR 回归，仍为 56/56。新演示使用 `data/finetuning-live` 独立 SQLite：两份验证文档分别提取 6／7 个字段，状态为“需要复核／待确认”；均未批准，查询表计数为 0。原有 `data/ledger.sqlite` 的 SHA-256 保持不变。见[实际联调](../reports/finetuning-live-verification.json)。

77 项 Python 工程测试与 2 项前端测试通过，包括原有审批事务、重新解析和单位精度，以及新增字段值绑定、检查点拒绝、范围检查、冻结数据和指标独立重算。工程测试不等于模型准确率。训练证据页只读保存报告，不触发模型请求。

## 使用与复现

已有本次模型和演示数据时，从项目根目录启动：

```bash
SPECLEDGER_DATA="$PWD/data/finetuning-live" \
SPECLEDGER_FIELD_MODELS="$PWD/data/field-models" \
SPECLEDGER_FIELD_MAPPER=finetuned \
uv run --group docling --group neural specledger serve --port 8770
```

浏览器打开本机 8770。审批按钮需要用户核查后使用；启动不会自动批准任何记录。更换模型后，如需更新旧文档，应显式重新解析并再次审阅，历史记录保留。

完整复现需要新的实验副本，保留参考报告。当前源码提交后可执行：

```bash
mkdir ../specledger-run-v0.3
git archive HEAD | tar -x -C ../specledger-run-v0.3
cd ../specledger-run-v0.3
mkdir reference
mv reports/finetuning-*.json reports/finetuning-*.xml reference/
mv training/datasets/documents.json reference/
uv sync --frozen --group docling --group neural --group viz
uv run --group neural python training/provision.py --download
uv run --group docling python training/provision_ocr.py --download
uv run python training/documents.py
export HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
uv run --group neural python training/train.py
uv run --group neural python training/provision.py
uv run --group docling python training/capture_ocr.py
uv run --group neural python training/evaluate.py
uv run --group neural python training/guard_diagnostics.py
uv run --group viz python training/visualize.py
```

源码提供冻结标签，不要重跑 `common.py` 改写原题集。脚本拒绝覆盖已有训练或测试结果。PDF 和模型可以重新生成／下载，但必须匹配固定指纹；上游文件变化时停止并调查，不能改写旧指纹使旧成绩继续有效。不同硬件可能存在浮点或 OCR 输出差异，应保存新报告并与 reference 比较。

## 简历可用表述

> 为设备规格文档审阅系统微调 22.7M 参数 MiniLM 字段映射组件，构建 96／32／48 条字段标签和 24 份多版式数字／扫描 PDF；在相同 OCR 输出的 16 份构造测试文档上，字段值／状态正确数由 54/112 提升至 62/112（+7.14 个百分点），补充独立范围检查与失败诊断，并集成原文页码追溯、单位校验及人工审批，通过 77 项 Python 和 2 项前端测试。

保留“构造测试文档”的限定。104/112 必须注明事后工程诊断；不能写成通用 OCR 微调成绩、真实厂商准确率或全自动入库效果。后续重点是有权使用的真实设备文档、独立标注和新的留出测试，尤其是未知字段拒绝与设备型号别名。

官方参考：[CrossEncoder 训练](https://www.sbert.net/docs/cross_encoder/training_overview.html)、[相关训练目标](https://www.sbert.net/docs/package_reference/cross_encoder/losses.html)、[MiniLM 模型卡](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2)。保留模型 Apache-2.0 许可，各 OCR 模型另遵其原有许可。
