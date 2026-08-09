# Development — trowel-py 开发流程

> SDD（spec-driven development，按明确规格开发）工作流的公共事实源。
> 配套读：[AGENTS.md](../../AGENTS.md)（开工入口与安全边界）和
> [CONTRIBUTING.md](../../CONTRIBUTING.md)（分支、Pull Request 与提交规则）。

## 职责分工（AI coding 模式）

| 角色 | 干什么 |
|---|---|
| 任务负责人 | 写 spec（钉死正确性、可观测、边界等质量下限）+ review 代码（看可读性、可维护性、AI 是否讲得通） |
| AI | 按 spec 实现 + 跑 typecheck / test 验证 |

查代码质量（跑测试、typecheck、lint）是 AI 的活，不是人的活。人通过 spec 约束质量下限，通过 review 把关上限。这就是「产品为主、训练为方法」——产品靠 spec + review 把关，训练是写 spec 和读代码的过程本身。

## SDD 闭环

每个 slice 走四步：

1. **任务负责人给出 spec 草稿**：公共协作使用 Issue 或 Pull Request；维护者也可使用
   不进入 Git 的本地规划文件；
2. **逐个决策点审查 spec**：消除歧义。spec 只钉死输入输出形态、不变量和通过标准，
   **不写实现细节**；
3. **AI coding（TDD）**：按 spec 实现 → typecheck + test 全绿 → sub-agent CR → 修 CRITICAL / WARNING。
4. **人 review + commit**：任务负责人确认行为、可维护性和验证证据后再授权提交。

## Git 分支与合并

- `main` 是稳定分支，只接受通过 review 和 CI 的 Pull Request，不直接提交；
- 跨多个 slice 的 milestone 从最新 `main` 创建集成分支，例如 `milestone-8`。
  milestone 完成前，代码只集成到该分支，不提前合入 `main`；
- 每个 slice 从所属 milestone 分支创建短分支，例如 `m8/l03-cognitive-signals`。
  分支内可按工作需要提交多次；人审通过后，通过 Pull Request squash 成一个
  commit 合入 milestone 分支，并删除 slice 分支；
- milestone 的全部 slice 和综合 Gate 通过后，创建 `milestone-8 -> main` Pull
  Request；通过 Squash and merge 在 `main` 上形成一个 milestone commit，随后删除
  milestone 分支；
- 不属于 milestone 的独立修复或工单从最新 `main` 创建短分支，通过 Pull Request
  squash 为一个 commit 后合入 `main`；
- 未经人确认不提交、合并或推送。提交前只暂存当前 slice 文件，不带入其他会话或
  本机未跟踪产物。

## Spec 的 8 个要素

| 要素 | 是什么 | 谁写 |
|---|---|---|
| 目标（goal） | 为什么做这个 spec 对应的工作 | 人 |
| 用户场景（Scenario） | 具体到用户的操作，什么样的场景 | 人 |
| 接口契约 | 输入输出和调用关系，让实现没歧义即可 | 人给初版，AI 完善 |
| 数据流 | 从数据视角写流程，每个阶段数据在哪、长什么样 | 人给初版，AI 完善 |
| 设计约束 / 不变量 | 约束 AI 的工作范围 | 人 |
| 通过标准 | 即测试内容 | 人给初版，AI 完善 |
| 测试方法 | 测试约束，防止 AI 用有问题的测试标准 | 人给初版，AI 完善 |
| 边界 | 约束工作范围 | 人 |

核心原则：spec 只钉死「输入输出形态 + 不变量 + 通过标准」，不写实现细节。

## 需求载体

| 场景 | 权威载体 | 约束 |
|---|---|---|
| 外部协作 | Issue、Pull Request 与当前用户消息 | 必须让 fresh clone 的开发者独立取得目标、边界和验收标准 |
| 维护者本地规划 | Git ignored 的 milestone、slice 或实验记录 | 只服务当前开发者，公共入口和 CI 不依赖其存在 |
| 长期项目事实 | 根入口、`docs/foundation/` 与 `docs/reference/` | 必须随代码版本演进，并通过公共链接和隐私检查 |

## 开发铁律

全仓安全边界见根 [AGENTS.md](../../AGENTS.md)。注释的详细判断原则见下。

生产前端还必须遵守 [Trowel 前端设计语言](./front-end-design-language.md)。共享控件不能在
业务页面重新绘制近似版本；`cd web && bun run check:ui-contracts` 检查原生下拉框和
可见文件控件是否重新进入产品界面。

## 项目上下文分层与更新

根 `AGENTS.md` 保存全仓规则、开工入口，以及各领域的路径和一至两句摘要。模块入口或
摘要发生变化时才更新根索引，不把模块正文复制到根文件。`CLAUDE.md` 只用
`@AGENTS.md` 导入同目录的共同事实，不拥有 Claude 专属的第二份知识正文。
仓库内不使用 `AGENTS.override.md`：Codex 会用它替代同目录的公共 `AGENTS.md`，从而
形成未被两种 runtime 共用的第二份事实源。仓库也不使用 `.claude/CLAUDE.md` 或
`.claude/rules/*.md` 保存项目事实。长期私人偏好放在 runtime 连接级配置，不进入仓库；
只有仓库根目录 ignored 的 `CLAUDE.local.md` 可保存当前开发者的临时工作树补充，模块或
其他子目录不得另建 `CLAUDE.local.md`，避免形成 Claude 专属的第二事实源。

模块 `AGENTS.md` 就近保存该领域短而稳定的可复用知识，包括职责和进出边界、主要 owner、
无法从单个签名直接看出的不变量、三方边界、按风险场景选择的权威测试，以及有证据范围
的运行事实。测试入口列到测试文件和窄命令，不绑定测试函数名。需要长篇解释的协议、
架构和证据放在 `docs/reference/`，模块文件只保留摘要和继续阅读入口。

模块文件不设固定行数或 token 上限。内容 review 按类别删除函数和字段清单、API 复制、
长篇实验过程、临时行号、slice 进度、与根文件重复的命令，以及没有证据支持的“死代码”
结论。`unknown` 只记录会造成高风险误判的证据缺口；`removal_candidate` 只表示可以进入
独立删除评审，不是删除授权。

当前改动使稳定事实变化时，必须在同一个工作单元同步模块 AGENTS 或 reference。同领域
顺带发现且已经由代码、测试、Git 或真实运行核实的知识，应尽量一并落盘；属于其他领域、
尚未验证或需要产品决定的内容只形成修改候选。后台任务、daily review 和模型自述不能
静默改写权威项目文档。

## Slice 收尾与项目知识更新

每个 slice 的交接记录必须明确回答“本轮是否产生新的稳定项目结论”。稳定结论是会影响
后续 slice 导航或判断、有代码、测试、Git 或真实运行证据，并且不能从单个函数签名直接
读出的长期事实，包括领域职责、不变量、权威命令、已核实运行事实、兼容要求和会阻止
高风险误判的 `unknown`。临时调试步骤、一次性错误、本机故障、API 清单和个人偏好不算。

没有稳定结论时记录一行 `none + 原因`。存在结论时直接形成目标文档 diff，不建立候选
文件或 proposal 数据库；交接记录同时写明 `target`（目标文档）、`claim`（拟修改结论）、
`evidence`（证据）、`scope`（适用范围）、`reason`（代码或 docstring 不足以表达的原因）
和 `review`（接纳、修改、延后或拒绝状态）。接纳或修改后的目标文档
是唯一长期事实源。延后项只记录尚缺证据或人工决定；普通拒绝项直接丢弃，只有可能反复
提出，或拒绝本身属于产品、安全和公开契约决定时才保留一句理由。

有稳定结论的 handoff 使用以下字段结构：

```yaml
target: 目标共享文档
claim: 拟新增、修正或删除的稳定结论
evidence: 代码、测试、Git 或真实运行证据
scope: 结论适用的版本、平台、配置和观测限制
reason: 代码或 docstring 本身不足以表达的原因
review: 接纳 | 修改 | 延后 | 拒绝
```

没有稳定结论时只记录：

```yaml
none: 原因
```

独立 AIRC（AI 架构代码审查）在普通代码审查之外检查项目上下文是否与最终 diff、测试和
运行证据一致。普通语义候选由独立 AIRC 核对后交人通过 Git diff 接纳；产品边界、安全
规则和公开契约必须明确人工决定。更适合代码、docstring 或确定性 Gate 表达的事实不得
复制进 AGENTS。

未提交工作树在收尾时运行：

```bash
.venv/bin/python -m scripts.shared_context_check --allow-untracked
```

该命令只读报告断链、ignored 目标、权威命令路径和运行事实格式等确定性问题。失败会阻止
slice 标记完成或通过 review，但不能阻止会话关闭。CI 和 clean checkout 使用不带
`--allow-untracked` 的严格模式。它是仓库源码树内的开发工具，不属于安装后的
`trowel-py` CLI。daily review 继续只生成 Memory 日记和笔记，不读取、生成或修改项目
文档候选。

## 注释与 docstring

代码是实现的事实源，docstring 是大型项目的阅读索引。命名和类型可以说明局部
实现，却不能代替文件开头、代码浏览和 IDE 悬浮提示中的作用说明。

`trowel_py/` 下每个 Python 文件、类、`def` 和 `async def` 都要有 docstring。
这里的函数包括方法、property、私有辅助函数和嵌套函数。测试代码不按数量强制
补齐，仍优先通过测试名、fixture 名和断言表达场景。

`web/src/` 下每个生产 `.ts`、`.tsx` 文件都要在文件开头用 `/** ... */` 写一句
中文模块说明，直接交代这个文件负责什么。`index.ts` 说明公开哪一层边界；组件、
store、reducer、hook 和 runtime adapter 说明各自展示、保存、转换或协调什么。
测试文件、生成文件、类型声明文件和纯样式文件不强制补齐。

前端不照搬 Python 的逐函数数量要求。导出的组件、hook、store 操作、adapter、
类型或字段只有在名称和类型不能完整表达业务语义时才补 JSDoc；局部辅助函数同样
只说明隐藏的契约、生命周期、降级规则或第三方协议，不复述实现步骤。
`cd web && bun run check:module-comments` 检查生产模块是否都有文件级说明；覆盖
数量只证明说明存在，语义质量仍由人审判断。

### 先写可快速浏览的语义索引

不同位置首先回答不同问题：

- 模块 docstring：这个文件负责什么；
- 类 docstring：这个对象表示、保存或协调什么；数据对象还要说明调用方会读取
  或传入的字段；
- 函数 docstring：这个函数完成什么操作或产出什么结果；有参数时还要让调用方
  不打开函数体也能理解每个参数的业务含义。

第一句是索引摘要。只读这一句和签名，阅读者应能说出对象的职责。继续阅读完整
docstring，调用方应能理解如何使用它，以及哪些字段或输入不能只靠名称和类型
判断。

字段和参数说明的是业务语义，不是重复类型标注。根据实际情况写清：

- 这个值在系统中代表什么；
- 单位、范围、允许值或对应的外部概念；
- `None`、空字符串、`0`、默认值分别表示什么；
- 值在何时生成、何时变化，以及是否用于持久化、去重、排序或并发校验。

dataclass、Pydantic 模型、配置、状态、命令和结果对象的字段构成使用接口，在类
docstring 的 `Attributes:` 中逐项说明。普通服务类只列调用方会直接使用的公开
属性，不列 `_lock`、`_cache` 等内部实现状态。函数内部的局部变量不写进
docstring；名称仍不清楚时先改名，只有无法从代码直接表达的原因或约束才写邻近
行注释。

只有参数的业务含义已经由名称、类型和摘要共同说清时，函数才可以只写一句。
其余函数使用 `Args:` 逐项说明参数；一旦写 `Args:`，就列全除 `self`、`cls`
之外的参数，避免半份接口说明。返回值、产出值或异常有签名之外的语义时，再写
`Returns:`、`Yields:` 或 `Raises:`。

### 避免拗口

- 用具体主语、动作和对象写一句完整的话，例如“标识会话由 Claude Code 还是
  Codex 运行”，不写“会话使用的原生运行时种类”；
- 优先描述可观察的事实和用途，例如“重启或重连后变化的运行标识”，不把
  “代际”“投影”“facade”等生硬术语换成另一个生硬术语；
- 第一次出现的领域词要么换成大白话，要么当场解释。协议名、代码符号、字段值、
  命令和必须精确匹配的外部文本保留原文；
- 避免连续堆叠“……的……的……”、省略关键主语，以及逐字翻译英文原句；
- 如果一个字段无法用一句大白话解释，先检查命名或职责是否有问题。受持久化、
  API 或第三方协议约束而不能改名时，docstring 必须补足其真实含义。

### 统一格式

采用 PEP 257 的摘要结构和 Google 风格的字段分节：

- 单行说明写成 `"""一句完整的摘要。"""`；
- 多行说明的第一行仍是完整摘要，下一行留空，再写补充说明或分节；
- 分节统一使用 `Attributes:`、`Args:`、`Returns:`、`Yields:` 和 `Raises:`；
- 分节内容缩进四个空格，续行再缩进四个空格；
- 类型已有标注时不在 docstring 重复；契约、生命周期和调用限制写在摘要后的
  自然段，不另造“契约之类的”临时标题；
- 多行 docstring 的结束引号独占一行。

数据对象示例：

```python
@dataclass(frozen=True)
class RuntimeIdentity:
    """记录 Agent 会话当前关联的原生会话和运行进程。

    Attributes:
        agent_session_id: Trowel 分配的 Agent 会话 ID。
        runtime: 当前会话由 Claude Code 还是 Codex 运行，分别记录为
            "claude_code" 或 "codex"。
        native_session_id: Claude Code 会话 ID 或 Codex thread ID；尚未报告时
            为 None。
        runtime_generation: 当前 Claude Code 进程或 Codex 连接的序号；每次重启
            或重连都会变化。
        runtime_pid: 当前运行进程的 ID。
        runtime_pgid: 当前运行进程组的 ID。
    """
```

函数示例：

```python
def write_state(rendered_hash: str, *, force: bool = False) -> Path:
    """记录索引笔记目录的当前状态。

    ``rendered_hash`` 相同时默认不重复写入。

    Args:
        rendered_hash: 本次渲染内容的哈希，用于判断目录内容是否变化。
        force: 是否忽略已有哈希并强制写入。

    Returns:
        写入后的状态文件路径。
    """
```

复杂入口还要按需要补充签名无法表达的副作用、异常、生命周期和调用限制。
反直觉的幂等、顺序、并发、缓存、事务、`None` 与 `0` 区别，以及第三方协议
边界，可以继续用 docstring 或邻近的行注释说明。

不写或应删除的内容：

- 只复述参数名、类型、返回语句、循环、条件和相邻代码步骤，没有补充业务含义；
- 只把函数名拆成中文，仍没有说明实际作用；
- `slice-XXX`、review 轮次、验收编号等开发过程；这些信息留在提交、spec 或
  归档文档；
- 与同一位置已有说明重复的系统背景；
- 用章节横线和长篇导航掩盖混合职责的大文件。

前端模块说明示例：

```ts
/** 根据会话 capability 生成 Claude Code 的展示配置。 */
```

写法：

- 自然语言用简明中文，不造词，不省略关键主语。协议名、代码符号、字段值、
  命令、机器指令和必须精确匹配的外部文本保留原文；
- 模块、类和函数说明的是当前作用，不记录它从哪个历史版本演变而来；
- 注释声明的契约应有代码或测试支撑。事实变化时与代码一起更新；
- docstring 可能进入 `__doc__`、CLI help 或 OpenAPI，修改公开入口时必须审查
  对应契约差异。

覆盖审计只能证明 docstring 存在，不能证明摘要自然、字段齐全或语义准确。
`missing=0` 是数量下限，不是质量验收；最终仍要结合签名、实现和调用点做人审。

格式依据：

- [PEP 257](https://peps.python.org/pep-0257/)：摘要行、多行 docstring 和公开
  接口说明；
- [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)：
  `Args:`、`Returns:`、`Raises:` 与类的 `Attributes:` 格式。

## cc-host / cc 套壳开发铁律

cc 是黑盒第三方 CLI（本机 2.1.197），它推什么事件、何时落盘、字段什么含义，**不许靠猜**。slice-036 workflow 修复踩过坑：6 轮基于猜测的修复都没命中根因，逆向 + 真实录制后一次锁定。铁律：

1. **实现前必须先真实录制 cc 行为**。用 trowel 自己的端口起代理跑 cc（`.venv/bin/python -m trowel_py.cli --port 8001 --no-open`），在 service.py 的 cc stdout 读取处加 env-gated 录制器（`TROWEL_RECORD_CC=<path>`），dump 每条 raw 事件带 timestamp。绕过交互会话里 `claude -p` 嵌套卡死（issue #46416）。
2. **TDD 测试 fixture 必须来自真实录制，不许手写合成数据**。合成数据反映你的想象，全绿也无用（slice-036 前 6 轮测试全绿，用户一跑就崩）。
3. **建立在 cc 提供的信号上，不过度设计**。cc 给了 `result`（turn 边界）、`journal.jsonl` + `wf.json`（workflow 进度）、agent transcript 首行（prompt / 实时 label）、`tool_use_result.structuredPatch`（diff）。直接读，别自己发明 `all_done` 判断 / 自己算 diff。
4. **同一个 bug 第二次被反馈，停下重新逆向，不许改第三次**。第二次是红线，不是「再试一次」。slice-036 修到第 6 轮才逆向，太晚。

逆向结论以实际代码、真实录制与 Git 历史为溯源依据。

## 文档分工

- 公共开工入口、安全边界和领域导航 → `AGENTS.md`；
- Claude Code 公共桥接 → `CLAUDE.md`，正文只导入 `AGENTS.md`；
- 产品目标和开发流程 → `docs/foundation/` 中已审核的公共文件；
- 当前运行约束 → `docs/reference/` 中已审核的公共文件；
- 每个开发者自己的当前工作、milestone、slice、实验和归档 → Git ignored 的本地
  `docs/` 文件，不得成为公共入口或 CI 的依赖；
- 已完成行为的公共事实 → 代码、测试、Git 历史、Issue 和 Pull Request。
