---
name: web_search
description: "在网络上搜索最新信息、新闻、文档、答案，或任何可能超出模型训练数据范围的内容。当用户询问时事、近期发布、实时数据，或任何需要从互联网获取最新信息的问题时，使用此技能。"
---

# Web Search

Use this skill whenever the user needs information that may be outdated in the model's training data, or explicitly asks to search the web.

## When to Use

- Current events, news, or live data
- Recent software releases, changelogs, or documentation
- Prices, stock quotes, weather, or other time-sensitive data
- Verifying facts that may have changed recently
- Finding URLs, official docs, or authoritative sources

## How to Search

Use the `web_search` tool with a concise, specific query:

```python
results = web_search(query="Python 3.13 new features")
```

### Query Tips

- **Be specific**: Prefer `"FastAPI 0.110 release notes"` over `"FastAPI news"`
- **Use keywords**: Drop filler words; focus on nouns and technical terms
- **Scope by date when needed**: Append `"2025"` or `"site:docs.python.org"` to narrow results
- **Iterate**: If the first query returns poor results, rephrase and retry with different keywords

## Processing Results

1. Review the returned snippets and URLs.
2. If a snippet contains the answer, cite the source URL and summarize.
3. If a snippet is insufficient, use `web_fetch` to open the full page for deeper reading.
4. Always cite the source URL so the user can verify.

## Output Format

- Lead with the direct answer or summary.
- List supporting sources as bullet points with title + URL.
- Note when information may still be incomplete and offer to fetch additional pages.

## Limits and Caution

- Do not fabricate search results — only report what was actually returned.
- If search fails or returns no relevant results, say so clearly and suggest an alternative approach.
- Sensitive queries (legal, medical, financial advice) should be answered with appropriate disclaimers.
