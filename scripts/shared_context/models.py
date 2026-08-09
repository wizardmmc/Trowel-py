"""定义公共项目上下文检查器之间共享的结果对象。"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ContextFinding:
    """记录一项会阻止公共上下文发布的问题。

    Attributes:
        code: 供测试和排障稳定识别的问题类别。
        path: 相对仓库根目录的问题文件或目标路径。
        detail: 面向维护者的具体失败原因。
    """

    code: str
    path: Path
    detail: str


@dataclass(frozen=True)
class ModuleContextPolicy:
    """集中声明一个模块上下文的结构和 freshness 入口。

    Attributes:
        root: 模块相对仓库根目录的路径。
        required_headings: 模块 AGENTS 必须拥有的二级标题。
        required_status_groups: 每组至少需要一条正向记录的运行事实状态。
        authoritative_command_sections: 需要检查仓库路径的权威命令章节。
    """

    root: Path
    required_headings: tuple[str, ...]
    required_status_groups: tuple[tuple[str, ...], ...]
    authoritative_command_sections: tuple[str, ...]
