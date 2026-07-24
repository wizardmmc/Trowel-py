import re


def filter_secrets(text: str) -> str:
    aws_access_key_pattern = r"AKIA[A-Z0-9]{16}"
    github_token_pattern = r"ghp_[A-Za-z0-9]+"
    api_key_pattern = r"sk-[A-Za-z0-9]+"
    pattern = re.compile(r"(password|api_key|secret|token)=\S+", flags=re.IGNORECASE)
    patterns = [aws_access_key_pattern, github_token_pattern, api_key_pattern]
    for p in patterns:
        text = re.sub(p, "[REDACTED]", text)
    text = pattern.sub("[REDACTED]", text)
    return text
