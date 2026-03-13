---
name: web_fetch
description: "抓取并读取指定网页或 URL 的完整内容。当你已有目标 URL 且需要详细阅读页面内容时使用此技能——例如阅读文档、文章、GitHub 文件，或 web_search 返回的摘要不够详尽时进一步抓取完整页面。"
---

# Web Fetch

Use this skill when you need to retrieve the full content of a known URL. Unlike `web_search` (which finds URLs), `web_fetch` reads a specific page.

## When to Use

- Reading full documentation or API reference pages
- Following up on a `web_search` result where the snippet was too short
- Downloading raw files (e.g., GitHub raw content, JSON APIs, plain-text configs)
- Checking the live content of a page the user has explicitly linked
- Scraping structured data from a known URL

## How to Fetch

Use the `web_fetch` tool with the target URL:

```python
content = web_fetch(url="https://docs.python.org/3/library/pathlib.html")
```

### Options

- **format**: `"markdown"` (default, best for prose), `"text"` (plain text), or `"html"` (raw HTML)
- Use `"markdown"` for human-readable articles and docs
- Use `"text"` when you need clean text without any formatting
- Use `"html"` only when you need to parse the DOM structure

## Processing the Result

1. Read the returned content and extract the relevant sections.
2. Summarize or quote the parts that answer the user's question.
3. Cite the source URL in your reply.
4. If the page is very long, focus on the most relevant sections rather than dumping everything.

## Pagination and Long Pages

- If a page is paginated or has "next page" links, fetch subsequent pages as needed.
- For very large pages, summarize key sections rather than returning the full content verbatim.

## Error Handling

- If the fetch fails (404, timeout, redirect loop), report the error clearly.
- For redirects to a different host, retry with the redirect URL.
- If a page requires authentication or is blocked, say so and suggest alternatives.

## Limits and Caution

- Do not fabricate page content — only report what was actually returned.
- Respect robots.txt and site terms where applicable; avoid aggressive crawling.
- Do not follow or execute JavaScript-only content (the tool fetches static HTML).
