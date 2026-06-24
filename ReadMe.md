## 初始化项目
uv init - 生成pyproject.toml 和 main.py
安装依赖
uv add fastapi uvicorn pydantic
uv add --dev pytest pytest-asyncio httpx
### 依赖介绍
fastapi -- Web 框架
uvicorn -- ASGI（asynchronous server gateway interface）服务器，fastapi需要这个才能跑起来
pydantic -- 数据校验
pytest -- 测试框架
httpx -- 测视里用于发送http请求的
### 创建目录
 trowel_py/
  ├── __init__.py
  ├── app.py              # FastAPI app factory（纯函数，无副作用）
  ├── server.py           # 入口：启动 uvicorn
  ├── db/
  │   ├── __init__.py     # 告诉 python 该目录是个可导入的包
  │   ├── connection.py   # create_db() 工厂函数
  │   ├── migrate.py      # 迁移运行器
  │   └── migrations/     # 空目录，002 填充
  ├── cards/
  ├── review/
  ├── garden/
  ├── pet/
  ├── events/
  ├── player/
  ├── feynman/
  ├── llm/
  └── schemas/
  web/  # 前端目录
  tests/
  ├── __init__.py
  ├── conftest.py         # pytest 共享 fixture
  ├── test_health.py      # health endpoint 测试
  └── test_db.py          # DB 连接 + 迁移测试
### 运行测试
uv run pytest -v