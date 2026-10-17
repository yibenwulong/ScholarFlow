# ScholarFlow

面向研究生和科研人员的多智能体科研文献研究与报告生成平台，基于 [GPT Researcher](https://github.com/assafelovic/gpt-researcher) 改造。新工作台入口为 **`/projects`**，原通用研究入口保留。

## 核心流程

创建研究问题 → 生成／编辑检索计划 → **人工批准** → 学术检索与筛选建议 → **人工确认纳入集合** → 文档准备 → 并行逐篇阅读 → 综合写作 → 逐处引用核验 → 保存与导出。

- 接入 arXiv、Semantic Scholar、OpenAlex；没有开放 PDF 的论文仍保留元数据和可用摘要。
- 区分全文与仅摘要依据；证据绑定文档哈希、不可变版本、原文片段和从 1 开始的 PDF 物理页。
- 比较矩阵逐项检查研究问题、方法、研究对象／数据集、发现和局限；区分尚未完整提取、当前摘要／所读全文未提供及人工未填写，记录缺失原因，人工修改另存修订并待复核。
- 每篇阅读成功后即可在“证据与矩阵”查看结果；失败论文单独显示原因，点击“仅重试此篇”只补读这一篇。成功结果保留，单篇重试不会自动生成报告。
- 阅读成功批次单独保存；连接中断后复用成功批次。旧提取结果可逐篇“补充提取”，新结果完成后再替换矩阵；整篇空结果不会标为阅读成功。
- 阅读器按论文自身内容提取事实，并选择原文片段编号；程序回填对应原句和位置，多处依据分别保存。在证据阅读中可切换结论和原文依据，逐条核查支持关系。
- 每次报告编辑创建新版本。同一篇论文的不同引用位置分别核验，不能以一个位置通过代替另一处检查。
- Markdown、DOCX 和 BibTeX 绑定同一已保存版本；问题草稿需要确认明确版本及检查记录，导出保留草稿标记。
- SQLite 持久化任务、进度事件与图检查点；默认纳入最多 20 篇、阅读并发 2、模型尝试预算 100、自动改写最多 1 次。

执行完成和质量通过分开显示。模型的支持性判断用于辅助复核，不构成事实保证，也不保证检索覆盖完整。

## 本地启动

需要 Python **3.12+**、Node **22+**。在项目根目录创建并激活虚拟环境后：

```powershell
python -m pip install -e ".[test,scholarflow]"
Copy-Item .env.example .env
```

在 `.env` 设置模型密钥及 `SCHOLARFLOW_MODEL=provider:model`。支持 OpenAI 兼容接口、Azure OpenAI、Anthropic、Ollama；Anthropic 需要另装 `langchain-anthropic`。密钥只配置在服务端。

分别在两个终端运行：

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
python -m backend.scholarflow.worker
```

第三个终端启动前端：

```powershell
cd frontend/nextjs
npm ci
npm run dev
```

打开 **http://localhost:3000/projects**。API 与 worker 必须使用相同的 `SCHOLARFLOW_DATA_DIR`；前端服务端代理地址由 `SCHOLARFLOW_API_URL` 配置，默认 `http://127.0.0.1:8000`。

## 无密钥完整演示

```powershell
python scripts/scholarflow_demo.py --port 8000
```

另行启动前端。此命令运行真实 API 和独立 worker，但明确使用**自建合成文献、PDF 和固定模型响应**；页面显示演示标签，不调用外部模型。创建项目并批准计划，选择两篇 “Synthetic Graph Collaboration” 文献，就能体验物理页证据、矩阵、报告编辑与导出。真实模式缺少密钥时不会自动切换成演示。

## 容器、测试与边界

`docker compose up --build` 启动 API、worker、前端，入口为 **`/projects`**；`.env` 中设置 `SCHOLARFLOW_DEMO=true` 可显式运行演示。实例数据存储于共享具名卷。默认仅监听本机。此 Compose 配置覆盖 ScholarFlow 工作台，继承的通用研究界面另有浏览器 API／输出代理地址配置。

```powershell
$env:GPTR_BLOCK_NETWORK='1'
python -m pytest tests/scholarflow -q
cd frontend/nextjs
npm run lint
npm exec tsc -- --noEmit
npm run build
$env:PLAYWRIGHT_CHANNEL='msedge'
npm run test:e2e -- --workers=1
$env:SCHOLARFLOW_LIVE_TEST='true'
npm run test:e2e -- e2e/scholarflow-live.spec.ts --workers=1
```

没有 Edge 时安装 Playwright Chromium：`npx playwright install chromium`。可通过 `PLAYWRIGHT_PYTHON` 指定 Python，测试端口可通过 `PLAYWRIGHT_PORT`、`PLAYWRIGHT_API_PORT` 修改。

v0.1 面向本地或可信网络的单用户使用，没有公共多用户认证。支持带文本层的 PDF；扫描件需先做 OCR。外部服务可能限流或缺少摘要／全文，页面保留具体错误与覆盖信息。原文、检查记录和报告版本不因后续编辑而覆盖。调用完成但响应落库前中断，可能产生重复调用及费用；无法取得用量或价格时显示未知。URL 检查仍有 DNS 二次解析的已知边界。

论文原件、报告、数据库、缓存和密钥不随代码上传；仅在具有分发权时分享原文。停止 API／worker 后备份整个数据目录。

## 项目讲解与面试资料

[项目讲解与面试手册](docs/scholarflow/interview-guide.md) 按项目速通、简历表述、模块讲解、技术问答、源码导读和演示排查组织，包含 24 个技术问答与 8 个工程问题复盘。提供 [PDF 阅读版](docs/scholarflow/ScholarFlow项目讲解与面试手册.pdf) 和 [Word 编辑版](docs/scholarflow/ScholarFlow项目讲解与面试手册.docx)。技术结论与当前代码对应，实测记录和待评价的科研质量分别说明。

完整说明见 [架构与数据边界](docs/scholarflow/architecture.md)、[评估协议](docs/scholarflow/evaluation.md)、[贡献说明](CONTRIBUTING.md)。项目遵循根 [Apache-2.0 LICENSE](LICENSE)，上游归属及继承的许可证元数据差异处理见 [UPSTREAM.md](UPSTREAM.md)。
