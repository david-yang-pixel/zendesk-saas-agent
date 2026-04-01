"""工具输出 token 预算裁剪。"""

from __future__ import annotations

import tiktoken


def apply_result_budget(output: str, max_tokens: int, model: str = "gpt-4o") -> str:
    """对工具输出做 token 预算裁剪。

    max_tokens == 0 表示不限制。
    超出预算时保留头部 + 尾部，中间截断。
    """
    if max_tokens <= 0:
        return output

    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")

    tokens = encoding.encode(output)
    if len(tokens) <= max_tokens:
        return output

    head_budget = int(max_tokens * 0.4)
    tail_budget = int(max_tokens * 0.4)

    head_text = encoding.decode(tokens[:head_budget])
    tail_text = encoding.decode(tokens[-tail_budget:])
    trimmed = len(tokens) - max_tokens

    return f"{head_text}\n\n... [输出过长，已截断约 {trimmed} tokens] ...\n\n{tail_text}"
