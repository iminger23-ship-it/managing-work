# MyLocalAI Live Web Research

When **Internet access** is ON, MyLocalAI now performs live public-web retrieval **before normal model answers** instead of relying on the local model to decide whether it can browse.

## Behavior
- Live search is enabled by default when the app starts.
- Normal chat messages receive fresh web search context automatically.
- Up to 5 search results are collected and up to 4 result pages are fetched for context.
- DuckDuckGo HTML is the primary search provider with Bing as a fallback.
- Public HTTP/HTTPS hosts are allowed; localhost/private/link-local/reserved addresses are blocked.
- Web pages are treated as untrusted reference material. Text found on websites is never treated as a MyLocalAI command.
- The local model is explicitly instructed that a `LIVE WEB RESEARCH` block means the host application already retrieved fresh web information.

## Commands
- `internet on|off|status`
- `auto web on|off|status`
- `internet test`
- `search web <query>`
- `research ai` (the dedicated AI research refresh remains scoped to August 2026)

## Important
The build has been syntax/AST checked here, but this environment cannot make outbound HTTP requests from the container. Run `internet test` on the Windows PC running MyLocalAI. It will report whether search and page fetching actually work there.
