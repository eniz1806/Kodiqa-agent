"""Kodiqa web tools - DuckDuckGo + Google search and page fetching."""

import re
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Current search engine: "duckduckgo" or "google"
_search_engine = "duckduckgo"


def set_search_engine(engine):
    """Switch search engine: 'duckduckgo' or 'google'."""
    global _search_engine
    _search_engine = engine.lower()


def get_search_engine():
    return _search_engine


def web_search(query, max_results=8):
    """Search using the currently selected engine."""
    if _search_engine == "google":
        return search_google(query, max_results)
    return search_duckduckgo(query, max_results)


def search_duckduckgo(query, max_results=8):
    """Search DuckDuckGo and return results as list of dicts."""
    try:
        url = "https://html.duckduckgo.com/html/"
        resp = requests.post(
            url,
            data={"q": query},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for item in soup.select(".result"):
            title_el = item.select_one(".result__a")
            snippet_el = item.select_one(".result__snippet")
            if not title_el:
                continue
            href = title_el.get("href", "")
            # DuckDuckGo wraps URLs in a redirect
            if "uddg=" in href:
                match = re.search(r"uddg=([^&]+)", href)
                if match:
                    from urllib.parse import unquote
                    href = unquote(match.group(1))
            results.append({
                "title": title_el.get_text(strip=True),
                "url": href,
                "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
            })
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        return [{"title": "Search Error", "url": "", "snippet": str(e)}]


def search_google(query, max_results=8):
    """Search Google by scraping (no API key needed). Falls back to DuckDuckGo on failure."""
    try:
        url = "https://www.google.com/search"
        resp = requests.get(
            url,
            params={"q": query, "num": max_results},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        # Google search result containers
        for div in soup.select("div.g"):
            title_el = div.select_one("h3")
            link_el = div.select_one("a[href]")
            snippet_el = div.select_one("div.VwiC3b") or div.select_one("span.aCOpRe")
            if not title_el or not link_el:
                continue
            href = link_el.get("href", "")
            if not href.startswith("http"):
                continue
            results.append({
                "title": title_el.get_text(strip=True),
                "url": href,
                "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
            })
            if len(results) >= max_results:
                break
        if results:
            return results
        # If Google blocked/failed, fall back to DuckDuckGo
        return search_duckduckgo(query, max_results)
    except Exception:
        # Fallback to DuckDuckGo
        return search_duckduckgo(query, max_results)


def fetch_page(url, max_chars=6000):
    """Fetch a URL and extract readable text."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove scripts, styles, navs
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        # Collapse multiple blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... (truncated)"
        return text if text.strip() else "No readable text found on page."
    except Exception as e:
        return f"Fetch error: {e}"


def format_results(results):
    """Format search results for model context."""
    if not results:
        return "No results found."
    lines = []
    engine_tag = f"[{_search_engine.title()}]"
    lines.append(f"Search results {engine_tag}:")
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**")
        if r["url"]:
            lines.append(f"   {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet']}")
        lines.append("")
    return "\n".join(lines)
