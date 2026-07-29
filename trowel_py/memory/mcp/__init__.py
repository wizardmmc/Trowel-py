"""定义 Memory MCP 请求处理器所在的子包。

具体处理逻辑位于 ``handlers``。调用方从 ``trowel_py.memory.mcp_server``
导入兼容接口，并通过该模块启动 stdio 服务。本模块不导入或重导出任何符号，
导入时也没有其他副作用。
"""
