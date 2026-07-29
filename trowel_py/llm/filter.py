"""在模型调用前替换有限几种凭据文本模式。"""

import re


def filter_secrets(text: str) -> str:
    """将内置正则命中的文本片段替换为 ``[REDACTED]``。

    规则仅覆盖 ``AKIA`` 后接 16 位大写字母或数字、``ghp_`` 或 ``sk-``
    后接连续字母或数字，以及 ``password``、``api_key``、``secret`` 或
    ``token`` 后直接接 ``=`` 和非空白值的片段；键名匹配不区分大小写。
    该函数只做有限的模式替换，不能作为完整的敏感信息检测。
    """
    aws_access_key_pattern = r"AKIA[A-Z0-9]{16}"
    github_token_pattern = r"ghp_[A-Za-z0-9]+"
    api_key_pattern = r"sk-[A-Za-z0-9]+"
    pattern = re.compile(r"(password|api_key|secret|token)=\S+", flags=re.IGNORECASE)
    patterns = [aws_access_key_pattern, github_token_pattern, api_key_pattern]
    for p in patterns:
        text = re.sub(p, "[REDACTED]", text)
    text = pattern.sub("[REDACTED]", text)
    return text
