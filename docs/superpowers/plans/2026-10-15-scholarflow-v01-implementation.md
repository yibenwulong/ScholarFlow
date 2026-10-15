# ScholarFlow v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有项目改造为可持久化、可人工干预、可回到论文原文的多智能体文献调研工作台，交付文献检索/导入、证据矩阵、报告编辑及三种格式导出。

**Architecture:** 保留现有 Next.js、FastAPI、LangGraph 和模型适配器，新增 `backend/scholarflow` 领域边界及 `/api/v1`。SQLite 保存不可变来源、阶段产物和任务；独立 worker 驱动科研图，前端通过项目 API 和持久化事件恢复状态。先交付单篇论文纵向流程，再接通多源检索及并发提取。

**Tech Stack:** Python >=3.12、Pydantic 2、FastAPI、SQLite/标准库 sqlite3、PyMuPDF、LangGraph、langgraph-checkpoint-sqlite、python-docx、Next.js 14、React 18、TypeScript、Tailwind、pytest、Playwright。

**Spec:** [2026-10-15-scholarflow-v01-design.md](../specs/2026-10-15-scholarflow-v01-design.md)。执行者先读设计和本计划；本文件是待用户审阅的实施计划，任务尚未执行。

## Global Constraints

- 首版采用单人、本地或受信任内网部署模式；手机和桌面使用同一实例及 API。
- 保留 Next.js、FastAPI、LangGraph、模型适配器及现有学术检索工具。
- 默认每个来源最多取 20 条、每次最多纳入 20 篇，均可配置。
- 首版逐篇并发默认为 2；Verifier 发现可修正问题时最多自动改写一次。
- 默认单次运行最多发起 100 次模型调用，可配置；恢复可能重复调用，不能保证 exactly-once 计费。
- PDF 页码统一为从 1 开始的物理页；摘要没有真实页码。
- 未选择候选可以保留 pending；AI 建议不能覆盖人工决定。
- 新 PDF 创建新 DocumentVersion；历史报告固定关联旧来源。
- 报告正文与引用快照不可变；修改创建新 ReportVersion，复核和导出追加记录。
- 报告导出 Markdown、DOCX、BibTeX；固定使用一种数字顺序引用格式。
- 所有新领域 API 使用 `/api/v1`；保留旧 API 和检索器的 `title/href/body` 契约。
- 修改和审批带 `expected_revision`，冲突返回 `409`；异步操作返回 `202` 和稳定 ID。
- 新流程不得使用现有达到修订上限后直接接受的审稿路由。
- 测试默认离线，不加载真实密钥或调用付费服务。
- Git 作者为用户提供的 yibenwulong；推送目标为其 ScholarFlow 仓库，不推送到现有上游 origin。

## Review Focus

1. 重复幂等键带不同请求正文：返回 409，不能悄悄复用错误任务（Task 5、9）。
2. 同一论文在两个位置被引用：每个位置独立关联 Claim 和 Evidence，一个通过不使另一个通过（Task 7）。
3. 下载重定向到内网、伪装 PDF 或上传路径穿越：每跳校验且限制文件大小，不写用户提供路径（Task 4）。
4. 人工等待后输入发生变化：审批固定明确 revision，旧 manifest 不被当前项目状态替换（Task 8）。
5. 有未保存编辑或服务端版本冲突：保留用户草稿，导出指向明确已保存版本（Task 12）。

## 执行前置与验证命令

- 使用 using-git-worktrees 技能检测或建立隔离分支；当前设计文档在主工作区暂存，禁止丢弃它。将批准的设计/计划带入实施分支后再写产品代码。
- 当前主机为 Windows PowerShell，Python 3.13.5、Node 24.19.0；主工作区没有 `.venv` 和前端 `node_modules`。版本是环境观测，支持下限仍以项目 Python >=3.12 为准。
- Task 1 配置隔离 Python 环境；后续命令中的 `PY` 指 Windows `.venv/Scripts/python.exe`，Linux `.venv/bin/python`。命令执行时使用实际路径，不创建名为 PY 的占位脚本。
- Python 验证前设置 `GPTR_BLOCK_NETWORK=1`。后端新测试统一在 `tests/scholarflow/`，使用独立 ASGI 工厂和依赖注入，避免导入旧 app 的重依赖；Task 9 单独检查集成入口。
- 前端命令在 `frontend/nextjs` 执行，PowerShell 使用 `npm.cmd`。新端到端命令为 `npm.cmd run test:e2e`；生产检查为 `npm.cmd run lint`、`npm.cmd exec tsc -- --noEmit`、`npm.cmd run build`。
- 每个任务执行红/绿验证并记录实际结果；只有涉及的检查通过后才提交。本文各 Task 的 Commit 是预期提交，不表示现在已产生提交。

## 文件与共享契约

新 Python 包的 `__init__.py` 按需创建且不触发模型初始化。模块边界：

| 路径 | 职责 |
| --- | --- |
| `backend/scholarflow/config.py` | 数据目录、默认限制、模型和数据源配置 |
| `domain/projects.py`、`domain/library.py` | 项目/计划/选择快照；论文/版本/来源 |
| `domain/evidence.py`、`domain/runs.py`、`domain/reports.py` | 原文/提取/论断；运行/任务；报告/引用/复核 |
| `persistence/database.py`、`persistence/migrations/001_initial.sql` | 连接、外键、迁移和短事务 |
| `persistence/project_store.py`、`paper_store.py`、`evidence_store.py`、`run_store.py`、`report_store.py` | 各领域持久化；禁止网络或模型调用 |
| `services/projects.py`、`library.py`、`documents.py`、`extractions.py`、`reports.py`、`exports.py` | 用例与版本约束 |
| `services/providers/`、`services/model_gateway.py`、`services/verification.py` | 结构化学术接入、模型调用与核验 |
| `worker/jobs.py`、`worker/runner.py`、`worker/__main__.py` | 阶段幂等执行、租约、独立进程入口 |
| `workflows/state.py`、`workflows/nodes.py`、`workflows/graph.py` | 只携带 ID/manifest 的科研图 |
| `api/app.py`、`api/dependencies.py`、`api/projects.py`、`library.py`、`runs.py`、`evidence.py`、`reports.py` | 独立 ASGI 工厂、领域路由和集成 router |
| `frontend/nextjs/lib/scholarflow/`、`types/scholarflow.ts` | API/事件客户端和显式数据类型 |
| `frontend/nextjs/app/projects/`、`components/scholarflow/` | 项目工作台；不扩展旧聊天状态容器 |

共享模型契约，分配到上表 domain 文件：

以下任务中的 Python 模块相对路径统一相对于 `backend/scholarflow/`；`test_*.py` 的省略前缀统一为 `tests/scholarflow/`。前端任务中省略的路径统一相对于 `frontend/nextjs/`。明确标出的根目录文件不使用这些前缀。

- 所有 ID 为 UUID 字符串，时间为有时区 UTC，revision 为从 1 开始的整数。
- `ProjectCreate(question, language='zh-CN', year_from=None, year_to=None, sources=['arxiv','semantic_scholar','openalex'])`；空问题、逆序年份拒绝。
- `PlanDraft(queries, inclusion_criteria, exclusion_criteria)`；批准记录引用独立不可变 PlanRevision。
- `PaperCandidate(title, authors, year, identifiers, source, source_id, abstract, pdf_url, publication_status, raw_metadata)`；无全文不丢弃，未知值用 null。
- `SelectionSnapshot(id, project_id, revision, paper_version_ids, approved_at)`；至少 1 篇、默认最多 20 篇，全部属于对应项目。
- `DocumentVersion(id, paper_version_id, kind, sha256, storage_key, parse_status)`；Passage 带不可变文档 ID、正文、页码和偏移；abstract 页码为 null。
- `ExtractionRevision(id, paper_version_id, document_version_ids, fields, origin, previous_id)`；fields 固定为 question、method、study_object、findings、limitations，各含 value、claim_ids、evidence_ids 和 review_status。
- `ClaimRevision(id, text, type, status, source_scope)`；status 使用设计中的五种状态，source_scope 为 fulltext/abstract。
- `RunCreate(kind='literature_review', start_at='plan', plan_revision_id=None, selection_id=None)`；Run 带 request_snapshot、append-only manifests、status、stage、quality_status、pending_action。
- `CitationBinding(key, paper_version_id, bibliography_snapshot)` 与 `CitationOccurrence(id, block_id, token_index, claim_revision_ids, evidence_ids)` 分开；报告 block 保存 text 和逐项论断字符范围。
- `ReportVersion(id, project_id, run_id, previous_id, blocks, bindings, occurrences, manifest)`；`VerificationRecord` 固定目标版本及逐项结果；草稿接受固定二者 ID。
- API 列表统一 `{items: [...], next_cursor: null|string}`，错误统一 `{error: {code, message, retryable, details}}`；请求未知字段拒绝。

## Task 1: 可持久化领域模型与隔离测试入口

**Files:** Create `backend/scholarflow/config.py`、上述五个 domain 文件、`persistence/database.py`、`persistence/migrations/001_initial.sql`、`tests/scholarflow/conftest.py`、`tests/scholarflow/test_database.py`、`test_domain.py`；Modify `pyproject.toml`、`requirements.txt`、`.gitignore`。

**Interfaces:** `Database(path: Path).initialize() -> None`、`schema_version() -> int`、`transaction() -> ContextManager[sqlite3.Connection]`；`ScholarSettings.from_env() -> ScholarSettings`。模型使用 Pydantic 2，不导入模型 provider。

- [ ] Step 1：写迁移重复执行、外键、UTC、非法输入及配置默认值测试。固定核心断言：
  ```python
  db.initialize(); db.initialize()
  assert db.schema_version() == 1
  assert settings.max_included_papers == 20
  assert settings.reader_concurrency == 2
  assert settings.max_model_calls == 100
  ```
- [ ] Step 2：运行 `PY -m pytest tests/scholarflow/test_database.py tests/scholarflow/test_domain.py -q`，确认因新模块未实现失败。
- [ ] Step 3：实现模型、幂等迁移、外键与 WAL/忙等待设置；默认数据路径由 `SCHOLARFLOW_DATA_DIR` 决定。新增可选 extra `scholarflow` 包含 SQLite checkpointer，保留旧依赖下限；在隔离环境安装 `.[test,scholarflow]`。`.gitignore` 覆盖运行数据库、PDF、检查点和临时导出，固定测试样例除外。
- [ ] Step 4：重跑该组测试；新增“import 领域模块不初始化 provider”检查；记录实际依赖版本，不在全局 Anaconda 环境安装项目依赖。
- [ ] Step 5：只提交本任务文件，Commit：`feat: add ScholarFlow domain models and SQLite persistence`。

## Task 2: 项目、计划审批与最小 ASGI 工厂

**Files:** Create `persistence/project_store.py`、`services/projects.py`、`api/app.py`、`api/dependencies.py`、`api/projects.py`、`tests/scholarflow/test_projects_api.py`、`test_plan_revisions.py`。

**Interfaces:** `create_app(settings: ScholarSettings) -> FastAPI`；`create_router(services: ScholarServices) -> APIRouter`（前缀 `/api/v1`）；`ProjectService.create(data: ProjectCreate) -> Project`、`save_plan(project_id, draft, expected_revision) -> PlanRevision`、`approve_plan(project_id, plan_revision_id, expected_revision) -> Approval`。`ScholarServices` 为持有已初始化数据库及各服务的容器，启动期间不创建 LLM 客户端。

- [ ] Step 1：写项目创建/列表/更新、空问题、年份、旧 revision 409、审批归属测试。API fixture 使用 HTTPX ASGITransport 和显式 lifespan；断言新项目可在第二个客户端重新读取，批准旧计划不覆盖新计划。
  ```python
  assert stale_update.status_code == 409
  assert stored_plan.id == approved_plan_id
  assert list_response.json()['items'][0]['id'] == project_id
  ```
- [ ] Step 2：运行 `PY -m pytest tests/scholarflow/test_projects_api.py tests/scholarflow/test_plan_revisions.py -q`，确认失败。
- [ ] Step 3：实现 `/projects`、`/projects/{id}`、计划列表/修订/批准路由；审批记录独立于计划内容，事务内校验 revision，不在 API 进程执行远程规划。
- [ ] Step 4：重跑测试，确认未知字段 422、项目不存在 404、冲突返回统一错误；测试数据库重开后数据一致。
- [ ] Step 5：Commit：`feat: add persistent research projects and plan approvals`。

## Task 3: 学术元数据、候选文献和纳入快照

**Files:** Create `services/providers/base.py`、`arxiv.py`、`semantic_scholar.py`、`openalex.py`、`persistence/paper_store.py`、`services/library.py`、`api/library.py`、`tests/scholarflow/test_provider_metadata.py`、`test_library.py`、`test_selections.py`。

**Interfaces:** `AcademicProvider.search(query: str, limit: int) -> Awaitable[SearchResult]`；SearchResult 含 candidates、query、source、fetched_at 和可区分的 error；`LibraryService.import_candidates(project_id, candidates) -> list[ProjectPaper]`、`decide(project_id, paper_id, decision, reason, expected_revision) -> ProjectPaper`、`freeze_selection(project_id, paper_version_ids, expected_revision) -> SelectionSnapshot`。

- [ ] Step 1：固定三种 provider 响应样例，测试保留作者/年份/外部标识、无 OA PDF、空/异常响应、标识归一化及版本分离。包含：
  ```python
  assert candidate.authors == ['Alice Example']
  assert candidate.pdf_url is None  # 论文仍保留
  assert selection.paper_version_ids == [chosen_version_id]
  assert other_candidate.human_decision == 'pending'
  ```
- [ ] Step 2：运行 `PY -m pytest tests/scholarflow/test_provider_metadata.py tests/scholarflow/test_library.py tests/scholarflow/test_selections.py -q`，确认失败。
- [ ] Step 3：实现结构化 adapters 与来源记录；优先提取共享请求能力，旧 `.search()` 仍投影为三字段。DOI 大小写及 URL 前缀归一化，arXiv 明确保留版本；标题相似只产生合并建议。排除原因必填，人工决定优先。实现论文列表、纳排及 selection API。
- [ ] Step 4：验证一篇被排除论文不能进入 selection、跨项目版本拒绝、pending 不阻塞、至少 1 篇/默认最多 20 篇；同时运行现有 Semantic Scholar/OpenAlex 检索器测试，防止兼容性回退。
- [ ] Step 5：Commit：`feat: preserve scholarly metadata and explicit literature selections`。

## Task 4: PDF 与摘要版本、原文片段和安全文件读取

**Files:** Create `persistence/file_store.py`、`persistence/evidence_store.py`、`services/documents.py`、`services/downloads.py`、`tests/scholarflow/test_documents.py`、`test_downloads.py`、`test_file_access.py`；Extend `api/library.py`。在 conftest 用 PyMuPDF 生成两页文本 PDF fixture，不捆绑第三方论文。

**Interfaces:** `FileStore.put(data: bytes) -> StoredFile`、`resolve(storage_key: str) -> Path`；`DocumentService.import_pdf(project_id, paper_version_id, data, original_name) -> DocumentVersion`、`import_abstract(paper_version_id, text, source_record_id) -> DocumentVersion`、`parse(document_version_id) -> list[Passage]`；`download_pdf(url: str, *, max_bytes=52428800) -> Awaitable[bytes]`。

- [ ] Step 1：写两页定位、相同内容哈希复用、新内容创建新版本、摘要无页码、扫描/损坏/加密状态、路径穿越及下载重定向测试。
  ```python
  assert [p.page_number for p in passages] == [1, 2]
  assert abstract_passage.page_number is None
  assert new_document.id != old_document.id
  assert old_report_document.sha256 == old_document.sha256
  ```
- [ ] Step 2：运行 `PY -m pytest tests/scholarflow/test_documents.py tests/scholarflow/test_downloads.py tests/scholarflow/test_file_access.py -q`，确认失败。
- [ ] Step 3：使用 PyMuPDF 逐页提取、保存正文及偏移；用户文件名只作展示。工程默认上传/下载 50 MiB、最多 500 页、最多 5 次重定向，设为配置项。复用 `validate_url(url, allow_private=False)` 对每跳执行检查，拒绝非 PDF 内容；文件以哈希存储和原子改名。实现文档、受控 file 和 passages 路由。
- [ ] Step 4：验证旧版本仍可读取、未知版本 404、文件在 data 根目录外不可访问；无文本层不发起 LLM 调用。保留已知 DNS 二次解析限制，不宣称完全消除 DNS rebinding。
- [ ] Step 5：Commit：`feat: retain immutable PDF versions and page-level evidence passages`。

## Task 5: 持久化任务、幂等、事件与租约

**Files:** Create `persistence/run_store.py`、`worker/jobs.py`、`worker/runner.py`、`worker/__main__.py`、`tests/scholarflow/test_jobs.py`、`test_job_recovery.py`、`test_run_events.py`。

**Interfaces:** `RunStore.enqueue(run_id, kind, stage, input_manifest, operation_key) -> Job`、`claim_next_driver(owner, now, ttl_seconds) -> JobLease|None`、`heartbeat(lease, now) -> bool`、`finish(lease, result) -> bool`；`ArtifactExecutor.execute(run_id, stage, manifest, operation) -> Awaitable[ArtifactRef]`；`Worker.run_once() -> Awaitable[bool]`，Worker 构造时注入 `driver: Callable[[JobLease], Awaitable[ArtifactRef]]`，Task 8 再接入科研图。时间可注入，不用测试长时间 sleep。

- [ ] Step 1：测试重复同键复用、同键不同请求 409、过期认领、旧 attempt 提交拒绝、事件序号和取消。固定断言：
  ```python
  assert repeated_job.id == original_job.id
  assert store.finish(expired_lease, result) is False
  assert [e.seq for e in store.events(run_id, after=1)] == [2, 3]
  ```
- [ ] Step 2：运行 `PY -m pytest tests/scholarflow/test_jobs.py tests/scholarflow/test_job_recovery.py tests/scholarflow/test_run_events.py -q`，确认失败。
- [ ] Step 3：实现短事务认领及 fencing；默认租约 60 秒、续租 15 秒，配置可调整。区分 driver Job 与 artifact Job：worker 只领取 driver；图节点通过 ArtifactExecutor 执行并持久化子阶段，不让 driver 占满子任务并发配额而死锁。单 worker 同时驱动一个 Run，逐篇工作全局最多 2 个。重试网络错误默认 3 次退避，永久错误不自动重试。
- [ ] Step 4：测试 worker 重建后复用已落库产物、六篇成功后只执行剩余部分、取消后不再发布报告；原始调用响应落库前崩溃的模拟需记录重复 attempt，不断言零重复计费。
- [ ] Step 5：Commit：`feat: add durable research jobs with fenced leases and recovery`。

## Task 6: 模型网关、逐篇阅读与证据定位

**Files:** Create `services/model_gateway.py`、`services/extractions.py`、`services/verification.py`、`workflows/prompts.py`、`tests/scholarflow/test_model_gateway.py`、`test_extractions.py`、`test_evidence_location.py`；Modify `gpt_researcher/utils/llm.py` 并补充旧 helper 的重试参数回归测试。

**Interfaces:** `ModelGateway.complete(request: ModelRequest) -> Awaitable[ModelResponse]`，响应含 text、provider_request_id 及可未知的 usage；`ExtractionService.extract(run_id, paper_version_id, document_ids) -> Awaitable[ExtractionRevision]`、`edit(project_id, paper_id, changes, expected_revision) -> ExtractionRevision`；`locate_quote(passage: Passage, quote: str) -> QuoteLocation`；`Verifier.verify_claim(claim, evidence, passages) -> Awaitable[ClaimCheck]`。

- [ ] Step 1：使用注入 FakeModel 测试三类模型输出：有效结构化结果、无效 JSON、凭空证据；核对摘要边界、未报告字段、人工修改和调用预算。
  ```python
  assert missing_field.value is None
  assert abstract_claim.source_scope == 'abstract'
  assert wrong_number_check.status == 'insufficient_evidence'
  assert dispatch_count_at_limit == 100
  ```
- [ ] Step 2：运行 `PY -m pytest tests/scholarflow/test_model_gateway.py tests/scholarflow/test_extractions.py tests/scholarflow/test_evidence_location.py -q`，确认失败。
- [ ] Step 3：在新 gateway 中适配现有 `create_chat_completion`，不要复用吞掉异常返回 None 的 call_model。该 helper 当前非流式最多内部重试 10 次：新增可选 `max_attempts: int|None=None`，保持 None 时旧默认不变，科研 gateway 传 1，统一由任务层登记每次尝试后重试；provider SDK 隐式重试同样禁用或逐尝试记账，不以 gateway 次数冒充物理请求硬上限。调用前事务检查 100 次预算；输出先经 Pydantic 校验。按原文片段分包、记录覆盖范围，超出配置时分批读取而非静默截断；保存完整原文 offset。引句只允许有偏移映射的空白/断词规范化，不删除数字或否定词。
- [ ] Step 4：验证引用原文不存在时不能 supported、模型无用量则显示未知、不把模型判断称作事实保证；增加 helper 的 max_attempts=1 只调用 provider 一次、旧默认不变及预算耗尽不再 dispatch 的测试。人工修改产生新提取/论断 revision，原值可追踪。报告 outdated 投影在 Task 7 实现。
- [ ] Step 5：Commit：`feat: extract structured literature evidence with source boundaries`。

## Task 7: 报告版本、逐位置引用与质量记录

**Files:** Create `persistence/report_store.py`、`services/reports.py`、`services/citations.py`、`tests/scholarflow/test_report_versions.py`、`test_citation_occurrences.py`、`test_report_verification.py`；Extend `services/verification.py`。

**Interfaces:** `ReportService.compose(run_id, extraction_manifest) -> Awaitable[ReportDraft]`、`save(project_id, draft, expected_revision) -> ReportVersion`、`check(version_id) -> Awaitable[VerificationRecord]`、`accept_draft(version_id, verification_id) -> DraftAcceptance`；`render_citations(version: ReportVersion) -> RenderedReport`；`report_store.is_outdated(version_id) -> bool` 是当前状态投影。

- [ ] Step 1：测试允许引用集合、未知键、重复引用位置、正文编辑、不可变版本和独立质量记录。核心案例：
  ```python
  assert first_occurrence_check.status == 'supported'
  assert second_occurrence_check.status == 'conflicting'
  assert edited_version.previous_id == original_version.id
  assert original_version.blocks == original_blocks
  ```
- [ ] Step 2：运行 `PY -m pytest tests/scholarflow/test_report_versions.py tests/scholarflow/test_citation_occurrences.py tests/scholarflow/test_report_verification.py -q`，确认失败。
- [ ] Step 3：写作模型输出 blocks、精确论断范围和稳定引用键；程序建立逐位置绑定，按首次出现编号。支持研究范围、主题分类、方法比较、分歧、局限与后续方向及附录。无证据的“首次/最佳/无人研究”等表述作为未支持或推断。后续核验追加记录，不能改写历史正文。
- [ ] Step 4：验证草稿确认只适用于精确版本和检查记录；修改一个段落只降低相关引用状态；修改矩阵后关联报告投影为 outdated；不同论文同为 arxiv.org 不得被视为同一引用；无法比较的指标不自动生成统一排名。
- [ ] Step 5：Commit：`feat: add immutable reports with occurrence-level citation checks`。

## Task 8: 科研图、人工阶段与持久化检查点

**Files:** Create `workflows/state.py`、`workflows/nodes.py`、`workflows/graph.py`、`tests/scholarflow/test_workflow_gates.py`、`test_workflow_recovery.py`；Extend `worker/runner.py`。

**Interfaces:** `build_graph(services: ScholarServices, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph`；`drive_run(run_id, resume_payload=None) -> Awaitable[Run]`。使用 AsyncSqliteSaver 与 `graph.ainvoke`，`thread_id=f'{run.id}:{run.execution_generation}'`；图状态只包含 run ID、阶段及 manifest ID。checkpoint 文件在同一实例目录，生命周期覆盖整个 worker。

- [ ] Step 1：测试 plan/search/analyze 三入口、两个人工阶段、版本变化、重启恢复与修订上限。固定：
  ```python
  assert run.status == 'waiting_for_user'
  assert remote_search_calls_before_approval == 0
  assert selected_manifest.paper_version_ids == confirmed_ids
  assert unresolved_run.status == 'needs_review'
  ```
- [ ] Step 2：运行 `PY -m pytest tests/scholarflow/test_workflow_gates.py tests/scholarflow/test_workflow_recovery.py -q`，确认失败。
- [ ] Step 3：连接 Planner/Retriever/Screener/Reader/SynthesizerWriter/Verifier；审批通过 `interrupt` 与服务端校验后的 Command 恢复。恢复节点会重入，副作用经 ArtifactExecutor 幂等。分别固定启动、计划、selection、文档、提取 manifest；已固定输入变化创建新 Run。计划输入不要求已有 selection。
- [ ] Step 4：测试 1 次自动改写后仍冲突则停为 needs_review；修订上限不能接受；辅助 import/check/export 使用独立运行；completed/cancelled 不能直接 continue。租约过期接管时事务递增 execution_generation，新 generation 从领域 manifest 恢复并复用已完成产物；人工审批正常恢复沿用 generation。旧 worker 即使晚写检查点也只能写入旧 thread，不能覆盖新执行分支或正式产物，为此增加接管后旧 checkpoint 晚到的测试。
- [ ] Step 5：Commit：`feat: orchestrate resumable scholarly research with human approvals`。

## Task 9: 完整领域 API、受控导出与旧 app 集成

**Files:** Create `api/runs.py`、`api/evidence.py`、`api/reports.py`、`services/exports.py`、`tests/scholarflow/test_runs_api.py`、`test_reports_api.py`、`test_exports.py`、`test_app_integration.py`；Extend `api/library.py`、`api/app.py`、`backend/server/app.py`。

**Interfaces:** `ExportService.request(version_id, format, verification_id, draft_acceptance_id, operation_key) -> ExportJob`、`build(export_job_id) -> ExportArtifact`；format 精确为 markdown/docx/bibtex。实现设计第 8 节全部路由，WS 为 `/api/v1/runs/{id}/ws?after={seq}`，HTTP events 用相同序号。

- [ ] Step 1：测试异步 202/稳定 ID、冲突错误、resource 归属、草稿确认范围、三种导出、事件补读和旧 app 路由共存。
  ```python
  assert create_run_response.status_code == 202
  assert changed_body_same_key.status_code == 409
  assert export.document_version_ids == saved_report.document_version_ids
  assert exported_reference_numbers == saved_reference_numbers
  ```
- [ ] Step 2：运行 `PY -m pytest tests/scholarflow/test_runs_api.py tests/scholarflow/test_reports_api.py tests/scholarflow/test_exports.py tests/scholarflow/test_app_integration.py -q`，确认失败。
- [ ] Step 3：将 router 挂到现有 app，初始化依赖不改变旧 lifespan 职责；独立工厂仍可测试。DOCX 使用已有 python-docx 转换能力但写入项目受控目录，不调用硬编码 outputs 路径的旧函数；Markdown 与 BibTeX 同源生成。导出构建为独立任务，失败不重跑研究。
- [ ] Step 4：解析 DOCX ZIP 内正文和参考文献核对文本、编号与版本；必要时按 documents 技能做导出样例渲染检查。测试错误引用阻止正式导出，问题草稿保留标记。运行旧 reports/WS/URL 安全相关回归，集成 app import smoke 不依赖付费调用。
- [ ] Step 5：Commit：`feat: expose ScholarFlow APIs and version-consistent report exports`。

## Task 10: 前端 API 代理、项目、计划与文献工作区

**Files:** Create `frontend/nextjs/types/scholarflow.ts`、`lib/scholarflow/api.ts`、`lib/scholarflow/events.ts`、`app/api/scholarflow/[...path]/route.ts`、`app/projects/page.tsx`、`app/projects/[projectId]/page.tsx`、`components/scholarflow/ProjectWorkspace.tsx`、`ProjectList.tsx`、`PlanPanel.tsx`、`LibraryPanel.tsx`、`frontend/nextjs/playwright.config.ts`、`e2e/scholarflow-library.spec.ts`；Modify `package.json`、`.gitignore`，生成并提交前端 package-lock。

**Interfaces:** `scholarRequest<T>(path: string, options?: RequestInit): Promise<T>`；`ScholarApiError` 保留 status/code/details；`useRunEvents(runId: string|undefined)` 按 seq 去重并能 HTTP 补读。TS 字段从后端显式契约定义，不用 any。

- [ ] Step 1：添加 Playwright dev dependency 和 `test:e2e` 脚本；写项目创建/刷新、计划批准、AI 建议与人工决定分离、pending 候选不阻塞、409 保留输入的浏览器测试，先指向确定性 mock API。
- [ ] Step 2：运行 `npm.cmd run test:e2e -- e2e/scholarflow-library.spec.ts`，确认功能断言失败；测试启动需支持端口配置，不依赖已存在 dev server。
- [ ] Step 3：实现 Next.js 14 App Router 页面。新代理只转发固定领域路径至服务端 `SCHOLARFLOW_API_URL`，配置默认后端 localhost:8000；保留 query、状态、Content-Type、Range、Idempotency-Key 与二进制流，禁用缓存，不把密钥交给浏览器。JSON 经同源代理，WS 使用公开后端 URL 配置或 HTTP 补读回退。工作台使用服务端项目数据，新增根页可见项目入口。
- [ ] Step 4：重跑浏览器测试并执行 `npm.cmd run lint`、`npm.cmd exec tsc -- --noEmit`；检查手机视口使用相同 API，不回落到旧 `/api/chat`。新页面文案使用中文且状态有文字标签。
- [ ] Step 5：Commit：`feat: add the ScholarFlow project and literature workspace`。

## Task 11: PDF 证据阅读和可编辑比较矩阵

**Files:** Create `components/scholarflow/EvidencePanel.tsx`、`PdfEvidenceViewer.tsx`、`ComparisonMatrix.tsx`、`e2e/scholarflow-evidence.spec.ts`；Extend `ProjectWorkspace.tsx`、`lib/scholarflow/api.ts`。

**Interfaces:** `EvidencePanel({projectId, paperId, evidenceId?})`；PDF 使用受控 URL 加 `#page=<physical_page>`，旁边显示原文摘录；`ComparisonMatrix` 保存变化时携带提取 revision，不依赖浏览器 PDF 插件提供高亮 API。

- [ ] Step 1：写引用打开准确 DocumentVersion/物理页、摘要无页码、定位失败、矩阵人工编辑及冲突恢复测试。
  ```ts
  await expect(page.getByText('仅摘要依据')).toBeVisible();
  await expect(page.getByTestId('pdf-viewer')).toHaveAttribute('src', /#page=2$/);
  await expect(page.getByText('人工修改 · 待复核')).toBeVisible();
  ```
- [ ] Step 2：运行 `npm.cmd run test:e2e -- e2e/scholarflow-evidence.spec.ts`，确认失败。
- [ ] Step 3：实现“论文/PDF—原文摘录—矩阵”联动和基本字段编辑。缺少全文显示补充入口；浏览器不支持内嵌 PDF 时保留下载及准确页码说明。保留人工未保存值，报告 outdated 显示原因。
- [ ] Step 4：重跑测试及类型检查；手机切换原文/矩阵标签仍保持当前选中证据；测试新 PDF 不改变旧报告证据 URL。
- [ ] Step 5：Commit：`feat: connect literature comparisons to versioned PDF evidence`。

## Task 12: 报告编辑、逐项核验和运行进度

**Files:** Create `components/scholarflow/ReportPanel.tsx`、`RunPanel.tsx`、`CitationInspector.tsx`、`e2e/scholarflow-report.spec.ts`；Extend工作台、API 及事件客户端。

**Interfaces:** ReportPanel 以 ReportVersion 为编辑基线，显式保存新版本；编辑内容仅临时缓存。RunPanel 从 `/runs/{id}` 和持久化事件展示阶段、问题、取消/重试；CitationInspector 使用 occurrence ID 读取确切论断及证据。

- [ ] Step 1：写段落编辑后待复核、同论文不同引用位置、未保存导出、保存409、草稿确认和事件断线补读测试。
  ```ts
  await expect(page.getByText('存在未保存修改')).toBeVisible();
  await expect(page.getByText('引用待复核')).toBeVisible();
  await expect(page.getByRole('textbox', {name: '报告正文'})).toHaveValue(localDraft);
  ```
- [ ] Step 2：运行 `npm.cmd run test:e2e -- e2e/scholarflow-report.spec.ts`，确认失败。
- [ ] Step 3：实现桌面大纲/Markdown正文/证据三栏，手机标签布局；复用已 sanitize 的 markdown helper，插入的引用控件同样消毒，不执行原始 HTML。保存前不替换服务器版本；导出选择明确版本和检查结果。事件重连按 seq 补读，HTTP 状态作为最终来源。
- [ ] Step 4：重跑测试，检查恶意标题/Markdown不执行脚本；验证完成状态与质量标签分开、冲突不丢本地草稿、取消和失败后按钮与服务器状态一致。
- [ ] Step 5：Commit：`feat: add editable evidence-linked reports and recoverable progress`。

## Task 13: 真实 API 的完整演示、部署与开源交付

**Files:** Create `examples/scholarflow/README.md`、`examples/scholarflow/demo.json`、`scripts/scholarflow_demo.py`、`tests/scholarflow/test_end_to_end.py`、`frontend/nextjs/e2e/scholarflow-live.spec.ts`、`docs/scholarflow/architecture.md`、`docs/scholarflow/evaluation.md`；Modify README.md、README-zh_CN.md、`.env.example`、docker-compose.yml、Dockerfile、CONTRIBUTING.md、相关 CI、pyproject.toml、setup.py、frontend/nextjs/package.json。

**Interfaces:** `PY -m backend.scholarflow.worker` 独立启动；API 兼容 `uvicorn main:app`。demo 模式使用显式选择的固定 provider/model fixtures，不会因缺少密钥静默假装运行真实研究。样例由自建 PDF 生成，不打包不可分发全文。

- [ ] Step 1：写离线真实ASGI全流程，依次创建项目、生成并批准计划、检索、确认纳入、提取、核验、编辑、导出；再测试 worker 中断恢复。完整浏览器测试使用真实新 API 和 fake provider，不能只 mock 前端请求。
- [ ] Step 2：运行 `PY -m pytest tests/scholarflow/test_end_to_end.py -q` 和 `npm.cmd run test:e2e -- e2e/scholarflow-live.spec.ts`，确认缺失行为失败。
- [ ] Step 3：Compose 增加独立 worker、共享持久化目录和必要配置；为公开仓库禁用/移除上游专属部署触发与密钥依赖，保留可复现离线 CI。核对上游 LICENSE 与各包声明后统一准确说明，注明本项目基于 GPT Researcher。README 提供真实启动、无密钥演示、数据边界、限制及贡献方式；评估文档列出三个主题与身份/定位/支持性分项，不填写未测得分。
- [ ] Step 4：运行新后端测试全集、对应旧回归、前端 lint/tsc/build、mock 和真实 API E2E；执行 Compose 配置校验与干净目录启动检查。按设计验收表逐项记录证据；未执行的检查必须明示。检查将公开的工作树、上游触发器及待推送历史，不输出秘密值；有实际敏感内容才处理，不修改无关上游历史。
- [ ] Step 5：Commit：`docs: package the ScholarFlow research workflow for open source`。验证最终目标为用户创建的仓库，并遵循 finishing-a-development-branch 技能完成集成；不 force push，不自动公开含个人论文的运行数据。

## 依赖与执行选择

主要依赖链：1 → 2/3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11/12 → 13。Task 3 的 provider 归一化与 Task 5 的任务算法可在共享契约确定后并行；前端在 Task 9 契约稳定后开始，不与后端同时猜测字段。

推荐 native 执行：由主代理在同一上下文逐任务实现，独立代理负责接入点核查和最终代码审查。原因是版本、引用及任务恢复共享接口较多，集中实现能减少接口漂移；执行方式仍由用户审阅本计划后选择。另一选项为 subagent-driven，每个任务分别委派实现并独立复核。每任务的提交仅包含已验证文件，最终审查覆盖全分支。

## 自检与发布条件

- Spec 1/3/9 的流程及页面覆盖 Task 2、3、10、11、12。
- Spec 4/5 的版本、证据与引用覆盖 Task 1、4、6、7、9。
- Spec 6/7 的智能体、人工阶段、预算和恢复覆盖 Task 5、6、8。
- Spec 8 的完整 API 覆盖 Task 2、3、4、9；路由与类型在 Task 9 形成统一契约。
- Spec 10 的各验收场景覆盖对应任务测试及 Task 13 全流程。
- Spec 11 的来源、许可、样例、密钥边界和目标仓库覆盖 Task 13。
- Review Focus 五项分别有明确测试归属；没有把历史来源固定等同于旧报告自动失效。
- 用户已创建目标仓库 `https://github.com/yibenwulong/ScholarFlow`，远程可访问；origin 与默认推送目标已配置为该仓库，上游保留为 upstream。推送前核对具体分支及认证可用。
- SQLite checkpoint API 参考官方包 README：<https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-sqlite/README.md>；interrupt/resume 参考 <https://docs.langchain.com/oss/python/langgraph/interrupts>。实施时核对安装的兼容版本，不直接复制新版 event streaming v3 到旧代码。
