from __future__ import annotations

import html
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Iterable

USER_AGENT = "MyLocalAI/13.0 (local personal assistant; live-web-research)"
MAX_BYTES = 2_000_000
TIMEOUT = 15
SEARCH_TIMEOUT = 12


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects to private, local, or otherwise non-public hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        validate_url(target)
        return super().redirect_request(req, fp, code, msg, headers, target)


def _is_public_host(host: str) -> bool:
    host = (host or "").strip().lower().rstrip(".")
    if not host or host in {"localhost", "localhost.localdomain"}:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        # Block loopback/private/link-local/reserved/multicast/unspecified.
        if not ip.is_global:
            return False
    return True


def validate_url(url: str) -> str:
    url = str(url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urllib.parse.urlparse(url)
    if p.scheme.lower() not in {"http", "https"} or not p.hostname:
        raise ValueError("Only HTTP/HTTPS URLs are allowed.")
    if not _is_public_host(p.hostname):
        raise ValueError("That host is not a public internet address, so MyLocalAI blocked it.")
    return urllib.parse.urlunparse(p._replace(fragment=""))


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg", "canvas", "template", "nav", "footer"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg", "canvas", "template", "nav", "footer"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            s = " ".join(str(data).split())
            if s:
                self.parts.append(s)

    def text(self):
        return "\n".join(self.parts)


def _decode(data: bytes, content_type: str) -> str:
    enc = "utf-8"
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        enc = m.group(1)
    return data.decode(enc, errors="replace")


def fetch(url: str, max_chars: int = 50000) -> dict:
    safe = validate_url(url)
    req = urllib.request.Request(
        safe,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.7",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    with opener.open(req, timeout=TIMEOUT) as resp:
        data = resp.read(MAX_BYTES + 1)
        content_type = resp.headers.get("Content-Type", "")
        final_url = resp.geturl()
    if len(data) > MAX_BYTES:
        data = data[:MAX_BYTES]
    raw = _decode(data, content_type)
    if "html" in content_type.lower() or "<html" in raw[:2000].lower():
        parser = _TextExtractor()
        parser.feed(raw)
        text = parser.text()
    else:
        text = raw
    return {
        "url": final_url,
        "content_type": content_type,
        "text": re.sub(r"\n{3,}", "\n\n", text).strip()[:max_chars],
    }


def _clean_result_title(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", "", value or ""))
    return " ".join(value.split())


def _search_duckduckgo(query: str, limit: int) -> list[dict]:
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
        raw = resp.read(MAX_BYTES).decode("utf-8", "replace")
    results = []
    for m in re.finditer(
        r'<a[^>]+class=["\']result__a["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        raw,
        re.I | re.S,
    ):
        href = html.unescape(m.group(1))
        title = _clean_result_title(m.group(2))
        p = urllib.parse.urlparse(href)
        if "uddg=" in p.query:
            href = urllib.parse.parse_qs(p.query).get("uddg", [href])[0]
        try:
            href = validate_url(href)
        except Exception:
            continue
        results.append({"title": title or href, "url": href, "provider": "DuckDuckGo"})
        if len(results) >= limit:
            break
    return results


def _search_bing(query: str, limit: int) -> list[dict]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "count": limit})
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
        raw = resp.read(MAX_BYTES).decode("utf-8", "replace")
    results = []
    # Bing currently uses li.b_algo h2 a for the normal result list. Keep the
    # parser intentionally tolerant because search markup changes regularly.
    pattern = re.compile(
        r'<li[^>]+class=["\'][^"\']*b_algo[^"\']*["\'][\s\S]*?<h2[^>]*>\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.I,
    )
    for m in pattern.finditer(raw):
        href = html.unescape(m.group(1))
        title = _clean_result_title(m.group(2))
        try:
            href = validate_url(href)
        except Exception:
            continue
        results.append({"title": title or href, "url": href, "provider": "Bing"})
        if len(results) >= limit:
            break
    return results


def search(query: str, limit: int = 6) -> list[dict]:
    """Search public web in real time using multiple public search engines.

    No API key is required. The first working provider wins, then a fallback
    provider is attempted when the first provider returns no results.
    """
    q = " ".join(str(query or "").split()).strip()
    if not q:
        return []
    limit = max(1, min(int(limit or 6), 12))
    errors: list[str] = []
    for provider in (_search_duckduckgo, _search_bing):
        try:
            rows = provider(q, limit)
            if rows:
                # De-duplicate by canonical URL.
                unique = []
                seen = set()
                for row in rows:
                    key = row.get("url", "")
                    if key and key not in seen:
                        seen.add(key)
                        unique.append(row)
                return unique[:limit]
        except Exception as exc:
            errors.append(type(exc).__name__)
    return []


def search_with_status(query: str, limit: int = 6) -> tuple[list[dict], str]:
    rows = search(query, limit)
    if rows:
        providers = sorted({r.get("provider", "web") for r in rows})
        return rows, ", ".join(providers)
    return [], "no-results-or-search-engine-unavailable"




def network_self_test() -> dict:
    """Small connectivity check used by MyLocalAI diagnostics."""
    result = {"ok": False, "search_ok": False, "fetch_ok": False}
    try:
        rows = search("OpenAI AI research August 2026", 2)
        result["search_ok"] = bool(rows)
        result["search_provider"] = sorted({r.get("provider") for r in rows if r.get("provider")})
        if rows:
            try:
                doc = fetch(rows[0]["url"], max_chars=500)
                result["fetch_ok"] = bool(doc.get("text"))
            except Exception as exc:
                result["fetch_error"] = type(exc).__name__
    except Exception as exc:
        result["search_error"] = type(exc).__name__
    result["ok"] = bool(result["search_ok"] and result["fetch_ok"])
    return result
