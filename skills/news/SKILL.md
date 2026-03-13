---
name: news
description: "为用户从指定新闻网站获取最新资讯。提供涵盖政治、财经、社会、国际、科技、体育、娱乐等分类的权威 URL。使用 web_fetch 打开对应 URL 并获取内容，然后为用户进行摘要汇总。"
---

# News Reference

When the user asks for "latest news", "what's in the news today", or "news in category X", use the **web_fetch** tool with the categories and URLs below: fetch the page, then extract headlines and key points from the page content and reply to the user.

## Categories and Sources

| Category          | Source                       | URL                                    |
|-------------------|------------------------------|----------------------------------------|
| **Politics**      | People's Daily · CPC News    | https://cpc.people.com.cn/             |
| **Finance**       | China Economic Net           | http://www.ce.cn/                      |
| **Society**       | China News · Society         | https://www.chinanews.com/society/     |
| **World**         | CGTN                         | https://www.cgtn.com/                  |
| **Tech**          | Science and Technology Daily | https://www.stdaily.com/               |
| **Sports**        | CCTV Sports                  | https://sports.cctv.com/               |
| **Entertainment** | Sina Entertainment           | https://ent.sina.com.cn/               |

## How to Use

1. **Clarify the user's need**: Determine which category or categories (politics / finance / society / world / tech / sports / entertainment), or pick 1–2 if unspecified.
2. **Fetch the URL**: Use `web_fetch` with the corresponding URL from the table above.
3. **Extract content**: Parse headlines, dates, and summaries from the returned page content.
4. **Summarize the reply**: Organize a short list (headline + one or two sentences + source URL) by time or importance. If a site is unreachable or times out, say so and suggest another source.

## Notes

- Page structure may change when sites are updated; if extraction fails, say so and suggest the user open the link directly.
- When visiting multiple categories, fetch each URL separately to avoid mixing content.
- Include the original link in the reply so the user can open it directly.
