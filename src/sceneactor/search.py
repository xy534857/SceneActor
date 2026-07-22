"""Dependency-free web search for chat grounding.

Bing RSS is the primary backend because it is reachable from mainland-China
deployments; DuckDuckGo lite is the fallback for elsewhere. Results are
research briefings for the actor — never verbatim lines to speak.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import json
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SearchHit:
    title: str
    snippet: str
    url: str

    def to_dict(self) -> dict:
        return {"title": self.title, "snippet": self.snippet, "url": self.url}


def _fetch(url: str, timeout: float) -> str:
    request = Request(url, headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _clean(fragment: str) -> str:
    return unescape(_TAG.sub("", fragment)).strip()


def _google_news(query: str, count: int, timeout: float) -> list[SearchHit]:
    """Current news results with publication date; available without an API key."""
    body = _fetch(
        f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en",
        timeout,
    )
    hits = []
    for item in re.findall(r"<item>(.*?)</item>", body, re.S)[:count]:
        title = re.search(r"<title>(.*?)</title>", item, re.S)
        link = re.search(r"<link>(.*?)</link>", item, re.S)
        source = re.search(r"<source[^>]*>(.*?)</source>", item, re.S)
        published = re.search(r"<pubDate>(.*?)</pubDate>", item, re.S)
        if title:
            detail = " · ".join(_clean(m.group(1)) for m in (source, published) if m)
            hits.append(SearchHit(_clean(title.group(1)), detail, _clean(link.group(1)) if link else ""))
    return hits


def _bing(query: str, count: int, timeout: float) -> list[SearchHit]:
    body = _fetch(f"https://www.bing.com/search?q={quote(query)}&format=rss&count={count}", timeout)
    hits = []
    for item in re.findall(r"<item>(.*?)</item>", body, re.S)[:count]:
        title = re.search(r"<title>(.*?)</title>", item, re.S)
        link = re.search(r"<link>(.*?)</link>", item, re.S)
        desc = re.search(r"<description>(.*?)</description>", item, re.S)
        if title and desc:
            hits.append(SearchHit(_clean(title.group(1)), _clean(desc.group(1)), _clean(link.group(1)) if link else ""))
    return hits


def _ddg(query: str, count: int, timeout: float) -> list[SearchHit]:
    body = _fetch(f"https://html.duckduckgo.com/html/?q={quote(query)}", timeout)
    hits = []
    blocks = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'class="result__snippet"[^>]*>(.*?)</a>',
        body,
        re.S,
    )
    for url, title, snippet in blocks[:count]:
        hits.append(SearchHit(_clean(title), _clean(snippet), unescape(url)))
    return hits


def web_search(query: str, count: int = 5, timeout: float = 12.0) -> list[SearchHit]:
    """Current-news RSS first; broad web search as fallback."""
    for backend in (_google_news, _bing, _ddg):
        try:
            hits = backend(query, count, timeout)
        except Exception:
            continue
        if hits:
            return hits
    return []


def briefing(query: str, hits: list[SearchHit], lang: str = "zh-CN") -> str:
    """Format hits as an in-world research note the actor absorbed before answering."""
    if not hits:
        return ""
    head = f"你刚了解到的最新资讯（检索: {query}）：" if lang == "zh-CN" else f"Fresh information you just learned (query: {query}):"
    lines = [head]
    for i, hit in enumerate(hits, 1):
        lines.append(f"{i}. {hit.title} — {hit.snippet}")
    tail = (
        "用自己的话消化这些信息，像早就知道一样自然提及；不要念标题、不要报来源清单。"
        if lang == "zh-CN"
        else "Digest this in your own words as if you already knew it; never read out titles or list sources."
    )
    lines.append(tail)
    return "\n".join(lines)


def triage_prompt(user_line: str, cutoff_hint: str, lang: str) -> list[dict[str, str]]:
    """One aux model call: does this message need post-cutoff facts? If so, emit a query."""
    system = (
        "你是检索分诊器。判断回答用户这句话是否需要新近的、可能超出模型训练截止的事实"
        "（新闻、价格、发布、人事变动、比分、日期敏感信息）。"
        "只输出JSON: {\"need\": true/false, \"query\": \"检索词\"}。"
        "不需要检索时 need=false 且 query 为空。检索词要具体，带人名和主题。"
        if lang == "zh-CN"
        else "You are a retrieval triage. Decide whether answering the user's line needs recent, "
        "possibly post-training-cutoff facts (news, prices, releases, personnel, scores, dates). "
        'Output only JSON: {"need": true/false, "query": "search terms"}. '
        "If no retrieval is needed, need=false and query empty. Queries must be specific."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"{cutoff_hint}\n用户/user: {user_line}"},
    ]


def parse_triage(raw: str) -> tuple[bool, str]:
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return False, ""
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return False, ""
    query = str(data.get("query", "")).strip()
    return bool(data.get("need")) and bool(query), query
