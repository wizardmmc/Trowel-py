# Training Log

## 进度总览

| Slice | 名称 | 状态 | 教学模式 | Review 结果 |
|-------|------|------|---------|------------|
| 001 | 项目骨架 + SQLite 连接 | completed | full-demo | pass |
| 002 | 数据库 schema + 类型 + 校验 | completed | mixed | pass |
| 003 | LLM 统一客户端 | completed | full-demo | pass |
| 004 | 卡片提取管道 | completed | mixed | pass |
| 005 | 卡片审核 UI | completed | ai-coding | pass |
| 006 | FSRS 复习引擎 | pending | - | - |
| 007 | 复习会话 UI | pending | - | - |
| 008 | 花园视图 | pending | - | - |
| 009 | M1 E2E 链路验证 | pending | - | - |

## 已掌握知识点

> 每个知识点标注掌握程度：示范(看过) / 仿写(能模仿) / 独立(能自己写)

| 知识点 | 首次出现 Slice | 掌握程度 |
|--------|---------------|---------|
| uv 项目初始化与依赖管理 | 001 | 示范 |
| Python 包结构（__init__.py, import 路径） | 001 | 示范 |
| sqlite3 标准库（connect, row_factory, PRAGMA） | 001 | 示范 |
| pathlib（Path, glob, read_text, unlink） | 001 | 示范 |
| FastAPI（app factory, 路由装饰器, exception_handler） | 001 | 示范 |
| uvicorn 启动（--factory, host, port） | 001 | 示范 |
| pytest（fixture, conftest, assert, yield setup/teardown） | 001 | 示范 |
| tempfile（NamedTemporaryFile, TemporaryDirectory） | 001 | 示范 |
| 类型注解（函数签名 -> type hint） | 001 | 示范 |
| 工厂模式（create_db, create_app） | 001 | 仿写 |
| SQL 参数化查询（? 占位符, 防注入） | 001 | 仿写 |
| 单元素元组语法 (value,) | 001 | 仿写 |
| SQL CREATE TABLE / INDEX / CHECK 约束 | 002 | 仿写 |
| FTS5 全文搜索虚拟表 + 触发器同步 | 002 | 示范 |
| 外键约束 + ON DELETE CASCADE | 002 | 仿写 |
| Pydantic v2（BaseModel, Field, model_dump, default_factory） | 002 | 仿写 |
| Pydantic Optional（str \| None = None） | 002 | 仿写 |
| Pydantic Literal 枚举约束 | 002 | 仿写 |
| Repository 模式（create_repository, CRUD 方法） | 002 | 仿写 |
| json.dumps / json.loads（SQLite JSON 字段转换） | 002 | 仿写 |
| datetime.isoformat / fromisoformat（SQLite 时间转换） | 002 | 仿写 |
| list[str] 类型（Pydantic 列表字段） | 002 | 仿写 |
| **row_dict 解包（Card(\*\*row_dict)） | 002 | 仿写 |
| SQL UPDATE SET WHERE | 002 | 仿写 |
| SQL 子查询（WHERE rowid IN SELECT） | 002 | 仿写 |
| re 正则表达式（re.sub, re.compile, \S+, IGNORECASE） | 003 | 仿写 |
| Protocol（Python 的鸭子类型接口） | 003 | 示范 |
| unittest.mock（MagicMock, return_value, side_effect） | 003 | 示范 |
| 指数退避重试（exponential backoff） | 003 | 示范 |
| LLM SDK 集成（anthropic / openai Python 包） | 003 | 示范 |
| 延迟导入（lazy import, 放在 __init__ 内） | 003 | 示范 |
| 成本追踪（环形缓冲区 + 按类型分组统计） | 003 | 示范 |
| Pydantic model_validate（从 dict 校验生成模型实例） | 003 | 仿写 |
| type[BaseModel] 类型注解（类作为参数传入） | 003 | 示范 |
| os.environ 环境变量读取 | 003 | 未实践 |
| Pydantic 继承（子类继承父类字段） | 004 | 仿写 |
| uuid（uuid4, hex, 切片生成唯一 ID） | 004 | 示范 |
| Service 层编排（函数式，组合 Repository + LLM） | 004 | 示范 |
| FastAPI APIRouter（模块级路由分组） | 004 | 示范 |
| FastAPI include_router（prefix 挂载） | 004 | 仿写 |
| FastAPI 请求体（Pydantic 自动解析 + 422 校验） | 004 | 示范 |
| FastAPI 路径参数（{id} → 函数参数） | 004 | 仿写 |
| FastAPI Depends 依赖注入（工厂函数 + override） | 004 | 示范 |
| dict.update() 合并用户编辑 | 004 | 仿写 |
| list[start:end] 分页切片 | 004 | 仿写 |
| set 去重（seen_ids 模式） | 004 | 仿写 |
| dependency_overrides（测试时替换依赖） | 004 | 示范 |
| check_same_thread=False（SQLite 跨线程） | 004 | 示范 |

> **Slice 005 决策记录**：前端（web/）采用 AI coding 模式完成，不作为学员知识点追踪。后端（trowel_py/）继续手撕训练模式。学员在 Slice 005 中实际练习的内容：CORS 中间件配置（后端 app.py 修改）。

## Slice 教学记录

### Slice 001: 项目骨架 + SQLite 连接

**教学模式**: full-demo（全部知识点首次出现）

**导师示范内容**:
- 项目结构设计（trowel_py 包 vs 根目录的区别）
- connection.py（sqlite3 标准库, WAL, foreign_keys, row_factory）
- migrate.py（Path.glob, 排序执行, 幂等性, _migrations 表）
- app.py（FastAPI 工厂, 路由装饰器, 全局错误处理, 统一响应格式）
- server.py（bootstrap 组装, __name__ 入口判断）
- conftest.py（pytest fixture, yield setup/teardown, 内存数据库）
- test_health.py（TestClient, 模拟请求, assert 验证）
- test_db.py（PRAGMA 验证, 临时文件测试 WAL, 幂等性测试）

**Review 发现的问题**:
- 目录结构：最初放在根目录而非 trowel_py/ 包目录，已纠正
- __init__.py：cards/ 目录遗漏，已补上
- connection.py：注释过多（说 WHAT 而非 WHY），不强制改
- migrate.py：Path vs string 类型比较 bug（i vs i.name）、缺少 INSERT 记录、f-string 多余
- app.py：类型注解 `-> {}` 语法错误，应为 `-> dict`
- test_db.py：测 sqlite3.connect 而非 create_db()、conn.close 漏括号、字符串 "tmp" vs 变量 tmp、内存数据库 WAL 返回 "memory" 而非 "wal"

**学员表现**: 理解力强，英文注释主动练习，代码逻辑基本正确。主要问题集中在 Python 语法细节（类型注解、方法调用括号、变量 vs 字符串），随着练习会改善。

### Slice 002: 数据库 schema + 类型 + 校验

**教学模式**: mixed（新知识点 full-demo，已见知识点 guided/independent）

**内容**:
- 3 个 migration SQL 文件（cards + FTS5, fsrs_state + review_logs, gamification）
- Pydantic v2 模型（Card, FSRSState, ReviewLog）
- CardRepository（create, find_by_id, find_all, update, search_by_fts5）
- ReviewRepository（find_due, save_review_log）
- 全量测试（migration, repository CRUD, Pydantic 校验）

**导师示范部分**:
- 003 migration（gamification 表：复合索引、DEFAULT 'default'、1:1 外键）
- Review Pydantic schema + repository
- 全量测试补齐

**学员自主部分**:
- 001 migration（对照示范写，一次基本正确）
- 002 migration（独立写，外键语法需提示）
- Card Pydantic 模型（首次接触 Pydantic，Optional/Literal/default_factory 需讲解）
- CardRepository create + find_by_id（首次写 Repository，create 误写成 CREATE TABLE）
- find_all, update, search_by_fts5（理解方向正确，SQL 语法细节需纠正）

**Review 发现的问题**:
- 001 migration：idx_cards_status 建在 category 上（copy-paste 漏改）、datetime 拼写错误
- 002 migration：索引建错表（copy-paste）、CHECK IN 漏括号、state 的 DEFAULT not null 语法错误
- 003 migration：双引号 datetime("now") 导致非常量错误、多处漏分号
- Card Pydantic：tags 类型写成 Field|None=None 语法错误、time 而非 datetime
- CardRepository：create 误写成 CREATE TABLE、fetchone 返回 Row 非 Card、res==None 应为 is None、json.load 应为 json.loads
- 测试：assert 逻辑反了（is not None 应为 is None）、find_by_id 返回值用 is Card 比较类而非实例
- 常见拼写：bussiness→business, fogert→forget, reivew→review, covert→convert, referenced→REFERENCES

**学员表现**: 明显进步——Python 语法错误比 Slice 001 少很多，独立写的部分逻辑方向基本正确。核心概念（Repository 模式、Pydantic 校验、FTS5）理解到位。剩余问题集中在 SQL 语法细节（分号遗漏、引号类型）和 copy-paste 漏改，属于细心问题。

### Slice 003: LLM 统一客户端

**教学模式**: full-demo（LLM SDK、Protocol、mock、重试等大量新概念首次出现）

**导师示范部分**:
- Protocol 定义 + OpenAI/Anthropic Provider 实现
- 指数退避重试 `_call_with_retry`
- structured_call 完整流水线（filter → retry → validate → cost）
- 成本追踪 get_cost_report
- unittest.mock 测试（MagicMock, return_value, side_effect）
- 补齐重复性测试代码

**学员自主部分**:
- filter.py 正则秘密过滤（首次写 re 模块，正则语法多次纠正）
- Pydantic 模型 ExtractedCard + ExtractOutput（仿写级别，基本正确）
- 卡片提取 prompt（独立写，质量高）
- 测试文件结构搭建（部分独立，部分照示范）

**Review 发现的问题**:
- filter.py: 正则 `*` 误解为通配符（实际是"前一个字符重复 0+ 次"）、方括号转义 `\[`、password pattern 未扩展覆盖 api_key/secret/token
- extracted_card.py: prompt 字段名和 Pydantic 字段名不匹配（sourceType vs source_type）、文件命名用 camelCase（应为 snake_case）
- client.py: import json 缩进错误、Protocol 方法体用 pass 而非 ...、类型注解不熟悉（type[BaseModel]）、dict return 模式未学过→改用已掌握的类模式
- 测试: import 路径选错（unicodedata→unittest）、断言逻辑反了（过滤后应该找不到 secret）
- 常见拼写: desensilization→desensitization, EXREACT→EXTRACT, STSTEM→SYSTEM

**学员表现**: 面对大量新概念没有畏难，能独立搭建整体结构。正则表达式是全新领域，需要多次纠正语法。Protocol 和 mock 是抽象概念，照示范能写但理解还需后续练习。prompt 工程展现出良好直觉（比示范更详细的 confidence 评估体系）。

### Slice 004: 卡片提取管道

**教学模式**: mixed（新概念导师示范 service/routes，学员写 schema + 部分 route，测试策略口述）

**导师示范部分**:
- Service 层设计（extract_cards, review_card, find_duplicates 三个无状态函数）
- FastAPI 路由 + 依赖注入（APIRouter, Depends, dependency_overrides）
- 完整测试代码（schema 校验、service 单元、route 集成、错误路径、去重、分页）

**学员自主部分**:
- API schema（ExtractRequest, CardDraft, ReviewRequest, CardListResponse）— 仿写级，基本正确
- save_fsrs_state 方法（照 save_review_log 模式仿写，SQL 尾逗号 bug）
- routes.py 框架（理解方向正确，路径参数/草稿存储/返回值逻辑需多次纠正）
- 测试策略口述（数据层→单元→路由→端到端，层次递进，缺少错误路径补充后完善）

**Review 发现的问题**:
- api.py: ExtractRequest min_length=4→1, docstring 描述不准
- service.py: source 字段语义混淆（学员删除，合理）, "bussiness"/"reivew" 拼写
- save_fsrs_state: SQL 末尾多逗号、last_review 为 None 时 .isoformat() 崩溃
- routes.py: _draft_store 用 title 而非 id 当 key、model_dump 漏括号、draft_id 路径参数未声明、草稿从 DB 找而非 _draft_store、reject 判断重复、dedup 传 id 而非 title、page/limit 类型为 str、CardListResponse 位置参数构造
- 测试时发现: SQLite check_same_thread 限制、LLM API key min_length=16 导致测试 fixture 崩溃

**学员表现**: 从"写零件"到"造机器"的转变。Service 层的编排逻辑（先做什么后做什么）理解到位。Route 层新概念多（路径参数、依赖注入、草稿生命周期），需要多次 review 纠正细节。测试策略思路清晰，主动提出"口述策略+导师写代码"的学习模式。

**调试中修复的 bug**:
- review/repository.py: save_fsrs_state 未处理 last_review=None
- cards/service.py: source 字段语义问题（学员决定删除）
- cards/routes.py: "reject" → "rejected" 字段名

### Slice 005: 卡片审核 UI

**教学模式**: ai-coding（前端全部 AI 生成，非学员手撕训练范围）

**决策**: 从 Slice 005 起，前端（web/）采用 AI coding，后端（trowel_py/）继续手撕训练。前端代码不计入学员知识点追踪。

**学员练习部分**:
- 后端 CORS 中间件添加（app.py，学员手写）

**AI 交付内容**:
- Vite + React 19 + TypeScript 前端项目
- API Client + SSE handler + Zustand stores + 4 个 UI 组件
- vitest 测试 30 个用例全部通过
- TypeScript 类型检查 + 生产构建通过
