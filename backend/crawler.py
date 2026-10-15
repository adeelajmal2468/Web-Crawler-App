"""Open-web URL discovery and bounded, robots-aware crawling of public property pages.

Works with user-supplied seed URLs (no API needed) or optional Serper URL discovery.
Does not bypass bot challenges, restricted endpoints, paywalls or logins.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import re
import time
from collections import deque
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from engine import PlotQuery, build_searches, has_phrase_tokens, matches_plot_number
from serper_client import search_once, SerperAPIError

USER_AGENT = "PakistanPlotWatchAcademicCrawler/4.0 (robots-aware; academic prototype)"
# Search engine is used ONLY for discovering candidate URLs, NOT as matching evidence.
SERPER_API_URL = "https://google.serper.dev/search"
DISCOVERY_DOMAINS: tuple[str, ...] = ()  # Empty means all publicly indexed websites.
MAX_HTML_BYTES = 2_000_000


def safe_url(raw_url: str) -> str | None:
    """Allow only ordinary public http(s) addresses; no embedded creds or custom ports."""
    try:
        url, _ = urldefrag(raw_url.strip())
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").casefold()
        if parsed.scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
            return None
        if parsed.port not in (None, 80, 443):
            return None
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".local", ".internal", ".test", ".invalid")):
            return None
        if "." not in hostname:
            return None
        try:
            if not ipaddress.ip_address(hostname).is_global:
                return None
            # Even public IP literals are disallowed, to keep this an HTTP website crawler.
            return None
        except ValueError:
            pass
        return url
    except (ValueError, AttributeError):
        return None


def host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def normalize_seeds(text: str) -> list[str]:
    seeds = []
    for part in re.split(r"[\r\n]+", text):
        part = part.strip()
        if not part:
            continue
        url = safe_url(part)
        if not url:
            raise ValueError(f"Invalid public website URL: {part[:100]}")
        if url not in seeds:
            seeds.append(url)
    if len(seeds) > 15:
        raise ValueError("Use up to 15 starting URLs per plot watch.")
    return seeds


@dataclass
class CrawlStats:
    fetched: int = 0
    blocked: int = 0
    candidates: int = 0
    budget_limited: bool = False
    errors: list[str] = field(default_factory=list)
    urls_checked: list[str] = field(default_factory=list)
    discovered_urls: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.blocked and not self.errors and self.fetched > 0

    def report(self) -> str:
        base = f"Discovered {len(self.discovered_urls)} URL(s); fetched {self.fetched} page(s); blocked {self.blocked}; candidate URLs {self.candidates}."
        if self.budget_limited:
            base += " Page budget reached: some discovered URLs were not checked."
        if self.errors:
            base += " Issues: " + "; ".join(self.errors[:4])
        return base


@dataclass
class CrawlOutput:
    results: list[dict]
    stats: CrawlStats


class RobotsAwareFetcher:
    def __init__(self, session: requests.Session | None = None, min_delay: float = 2.0):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
        self.min_delay = max(0.0, min_delay)
        self.robots: dict[str, RobotFileParser | None] = {}
        self.next_time: dict[str, float] = {}
        self.dns_checked: set[str] = set()

    def _wait(self, host: str, extra_delay: float = 0.0) -> None:
        next_time = self.next_time.get(host, 0.0)
        wait = max(0.0, next_time - time.monotonic())
        if wait:
            time.sleep(wait)
        self.next_time[host] = time.monotonic() + max(self.min_delay, extra_delay)

    def _get(self, url: str, *, headers: dict | None = None) -> requests.Response:
        """No redirects; reject private, loopback and reserved DNS destinations."""
        host = urlparse(url).hostname or ""
        if isinstance(self.session, requests.Session) and host not in self.dns_checked:
            # Network destinations should not be local machines or cloud metadata.
            # For injected offline test sessions, the fixture doesn't use real DNS.
            try:
                resolved = {entry[4][0] for entry in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
            except socket.gaierror as exc:
                raise requests.ConnectionError("Host DNS lookup failed") from exc
            if not resolved or any(not ipaddress.ip_address(addr).is_global for addr in resolved):
                raise requests.ConnectionError("Hostname resolves to private or reserved address")
            self.dns_checked.add(host)
        self._wait(host)
        return self.session.get(url, timeout=(6, 14), allow_redirects=False,
                                headers=headers or {}, stream=True)

    def permitted(self, url: str) -> tuple[bool, str]:
        host = urlparse(url).hostname or ""
        scheme = urlparse(url).scheme
        origin = f"{scheme}://{urlparse(url).netloc}"
        if origin not in self.robots:
            robots_url = origin + "/robots.txt"
            try:
                r = self._get(robots_url)
                if r.status_code == 404 or r.status_code == 410:
                    rp = RobotFileParser()
                    rp.parse([])  # robots resource missing: standard permissive behavior
                elif r.status_code == 200:
                    # limit the size of robots documents too
                    content = r.content[:250_000].decode("utf-8", "replace")
                    rp = RobotFileParser()
                    rp.parse(content.splitlines())
                else:
                    self.robots[origin] = None
                    return False, f"robots.txt unavailable ({r.status_code})"
                self.robots[origin] = rp
            except requests.RequestException as exc:
                self.robots[origin] = None
                return False, f"robots.txt request failed ({type(exc).__name__})"
        rp = self.robots[origin]
        if rp is None:
            return False, "Cannot verify robots.txt permissions"
        if not rp.can_fetch(USER_AGENT, url):
            return False, "Disallowed by robots.txt"
        delay = rp.crawl_delay(USER_AGENT) or rp.crawl_delay("*") or 0
        if delay:
            self.next_time[host] = max(self.next_time.get(host, 0), time.monotonic() + float(delay))
        return True, "Allowed by robots.txt"

    def fetch(self, url: str, allowed_hosts: set[str]) -> tuple[str | None, str | None, str]:
        current = url
        for _ in range(4):
            current = safe_url(current)
            if not current:
                return None, None, "Unsafe URL or redirect"
            host = urlparse(current).hostname or ""
            if host not in allowed_hosts:
                return None, None, "Redirect outside discovered website set"
            allowed, reason = self.permitted(current)
            if not allowed:
                return None, None, reason
            try:
                r = self._get(current)
                if r.status_code in (301, 302, 303, 307, 308):
                    location = r.headers.get("Location", "")
                    r.close()
                    current = urljoin(current, location)
                    continue
                if r.status_code in (401, 403, 429):
                    r.close()
                    return None, None, f"Access restricted or rate limited (HTTP {r.status_code})"
                r.raise_for_status()
                ctype = r.headers.get("content-type", "").lower()
                if "html" not in ctype:
                    r.close()
                    return None, None, f"Not HTML ({ctype or 'missing Content-Type'})"
                limit = MAX_HTML_BYTES + 1
                chunks: list[bytes] = []
                seen = 0
                for chunk in r.iter_content(65536):
                    seen += len(chunk)
                    if seen > limit:
                        r.close()
                        return None, None, "Page exceeds 2 MB HTML limit"
                    chunks.append(chunk)
                r.close()
                return b"".join(chunks).decode(r.encoding or "utf-8", "replace"), current, "OK"
            except requests.RequestException as exc:
                return None, None, f"Fetch failed ({type(exc).__name__}: {str(exc)[:110]})"
        return None, None, "Too many redirects"


def _flatten_schema(doc: object) -> list[dict]:
    """Include listing-focused schema objects, not sitewide breadcrumbs or recommendations."""
    found = []
    if isinstance(doc, list):
        for item in doc:
            found.extend(_flatten_schema(item))
    elif isinstance(doc, dict):
        types = doc.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if any(t.casefold() in {"product", "realestatelisting", "singlefamilyresidence", "residence", "house", "apartment", "offer"} for t in types if isinstance(t, str)):
            found.append(doc)
        if "@graph" in doc:
            found.extend(_flatten_schema(doc["@graph"]))
    return found


def parse_page(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    meta_title = soup.find("meta", attrs={"property": "og:title"})
    title = (h1.get_text(" ", strip=True) if h1 else "") or (meta_title.get("content", "") if meta_title else "")
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    description = ""
    for attrs in ({"name": "description"}, {"property": "og:description"}):
        node = soup.find("meta", attrs=attrs)
        if node and node.get("content"):
            description += " " + str(node["content"])
    json_parts = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            obj = json.loads(script.string or script.get_text())
            for entry in _flatten_schema(obj):
                for k in ("name", "description", "category", "address"):
                    v = entry.get(k)
                    if isinstance(v, str):
                        json_parts.append(v)
                    elif isinstance(v, dict):
                        json_parts.extend(str(x) for x in v.values() if isinstance(x, str))
                offers = entry.get("offers")
                if isinstance(offers, dict):
                    for field in ("price", "priceCurrency", "availability"):
                        if offers.get(field):
                            json_parts.append(str(offers[field]))
        except (ValueError, TypeError):
            continue
    for tag in soup.select("script, style, nav, footer, header, aside, form, noscript, svg"):
        tag.decompose()
    # Prefer core article for property text. Avoid other listings' titles where possible.
    main = soup.find("main") or soup.find("article") or soup.body or soup
    outgoing = []
    for link in main.find_all("a", href=True):
        target = safe_url(urljoin(url, str(link["href"])))
        if target:
            outgoing.append((target, link.get_text(" ", strip=True)))
    # Important accuracy safeguard: links in a search/category page describe OTHER
    # properties. Their anchor texts cannot count as evidence for this page.
    for link in main.find_all("a"):
        link.decompose()
    main_text = " ".join(main.stripped_strings)[:9000]
    content = " ".join([title, description, *json_parts, main_text])
    content = re.sub(r"\s+", " ", unescape(content)).strip()
    return {"title": title[:240] or "Untitled listing", "text": content, "links": outgoing}


def _location_match(needle: str, body: str) -> bool:
    if not needle.strip():
        return True
    if has_phrase_tokens(needle, body):
        return True
    # "Block F" and "F Block" are common alternatives.
    tokens = re.findall(r"[a-z0-9]+", needle.casefold())
    if len(tokens) == 2 and tokens[0] in {"block", "sector"}:
        return bool(re.search(rf"\b{re.escape(tokens[1])}\s+{tokens[0]}\b", body, re.I))
    return False


def evaluate_page(parsed: dict, url: str, query: PlotQuery) -> dict | None:
    text = parsed["text"]
    title = parsed["title"]
    # Plot number must be explicitly labeled; numbers for sizes, prices, streets or
    # listing IDs must NOT become plot identifiers.
    if not matches_plot_number(text, query.plot_number):
        return None
    location_text = text[:7500] + " " + urlparse(url).path.replace("_", "-").replace("-", " ")
    society = _location_match(query.society, location_text)
    block = _location_match(query.block, location_text)
    city = _location_match(query.city, location_text)
    if not society or not city:
        return None
    sale = bool(re.search(r"\b(for\s+sale|sale\s+price|selling|purpose\s*:?\s*for\s+sale|on\s+sale)\b", text, re.I))
    sold = bool(re.search(r"\b(sold\s+out|already\s+sold|no\s+longer\s+(?:available|for\s+sale)|listing\s+expired)\b", text, re.I))
    if sold:
        status, score = "Possible sold/expired advertisement; verify", 45
    elif sale and block:
        status, score = "Matching sale advertisement; availability unverified", 95
    elif block:
        status, score = "Matching plot details; sale status unclear", 72
    else:
        status, score = "Possible listing; block not confirmed", 55
    number = re.escape(query.plot_number.strip()).replace(r"\-", "[\\s/-]*")
    matcher = re.search(rf"\b(?:plot|plt)\s*(?:(?:number|no\.?)\s*)?[:#-]?\s*{number}\b", text, re.I)
    begin = max(0, (matcher.start() if matcher else 0) - 95)
    excerpt = text[begin:begin + 450].strip()
    if begin:
        excerpt = "..." + excerpt
    return {"title": title, "url": url, "source": urlparse(url).hostname or "",
            "snippet": excerpt, "confidence": status, "score": score}


def likely_detail_page(url: str) -> bool:
    path = urlparse(url).path.lower()
    return bool(re.search(r"/(?:property|properties|listing|listings|item)/[^/]+", path)
                or re.search(r"-\d{6,}(?:-|\.|$)", path))


def relevant_link(url: str, anchor: str, query: PlotQuery) -> bool:
    # Only property/listing paths. The seed itself is always fetched regardless.
    text = (urlparse(url).path + " " + anchor).casefold()
    return bool(re.search(r"\b(plot|plots|property|properties|land|residential|commercial|for-sale)\b", text)
                or "/item/" in text or "_for_sale" in text or "-for-sale" in text)


def build_discovery_queries(query: PlotQuery, domains: tuple[str, ...] = ()) -> list[str]:
    """Search the whole indexed web by default; optional domains narrow discovery.

    A domain restriction is a user's opt-in filter, never an implicit portal whitelist.
    """
    query.validate()
    number = query.plot_number.strip()
    society = query.society.strip()
    city = query.city.strip()
    block = query.block.strip()
    location = f'"{society}" {block} "{city}"'
    variants = [
        f'"plot no {number}" {location} Pakistan sale',
        f'"plot {number}" {location} Pakistan for sale',
        f'"plot number {number}" "{society}" {city} Pakistan',
        f'"{number}" {society} {block} {city} Pakistan residential plot sale',
    ]
    if domains:
        valid_domains = []
        for value in domains:
            domain = value.strip().casefold()
            if not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", domain):
                raise ValueError(f"Invalid optional website domain: {value}")
            valid_domains.append(domain)
        # Preserve the ability to deliberately limit results to specific sites.
        return [f"site:{domain} {q}" for domain in valid_domains for q in variants[:2]]
    return variants


def discovery_relevance(item: dict, query: PlotQuery) -> int:
    """Prioritize likely property pages without calling snippets verified evidence."""
    title = str(item.get("title", ""))
    snippet = str(item.get("snippet", ""))
    content = f"{title} {snippet}"
    path = urlparse(str(item.get("link", ""))).path.replace("-", " ").replace("_", " ")
    number = query.plot_number.strip()
    score = 0
    if matches_plot_number(content, number):
        score += 46
    elif re.search(rf"(?<![0-9A-Za-z]){re.escape(number)}(?![0-9A-Za-z])", content + ' ' + path, re.I):
        score += 15
    if has_phrase_tokens(query.society, content + ' ' + path):
        score += 25
    if has_phrase_tokens(query.city, content + ' ' + path):
        score += 9
    if query.block and has_phrase_tokens(query.block, content + ' ' + path):
        score += 12
    if re.search(r"\b(plot|plots|property|properties|real estate|residential|land|housing|for sale)\b", content + ' ' + path, re.I):
        score += 9
    return score


def discover_urls(query: PlotQuery, api_key: str, domains: tuple[str, ...] = (),
                  limit_each: int = 20, session: requests.Session | None = None,
                  max_urls: int = 50) -> tuple[list[str], list[str]]:
    """Find candidate URLs across the web (not just three portals).

    Search titles and snippets are used to prioritize crawling only; never to
    claim a plot is listed for sale. A retrieved site's page HTML is the evidence.
    """
    query.validate()
    if not api_key or not api_key.strip():
        return [], ["Serper API key missing: URL discovery skipped"]
    client = session or requests.Session()
    candidates: dict[str, tuple[int, int]] = {}
    issues: list[str] = []
    searches = build_discovery_queries(query, domains)
    requests_ok = 0
    rank = 0
    for search_text in searches:
        try:
            organic = search_once(search_text, api_key, session=client).get("organic", [])
            if not isinstance(organic, list):
                raise ValueError("Unexpected organic search response")
            requests_ok += 1
            for item in organic:
                if not isinstance(item, dict):
                    continue
                url = safe_url(str(item.get("link", "")))
                if not url:
                    continue
                host = urlparse(url).hostname or ""
                if domains and not any(host_matches(host, d) for d in domains):
                    continue
                relevance = discovery_relevance(item, query)
                if url not in candidates or relevance > candidates[url][0]:
                    candidates[url] = (relevance, rank)
                rank += 1
        except SerperAPIError as exc:
            # A rejected request is not a 'no listing found' result. Show the
            # provider's reason and avoid burning time on three more identical calls.
            issues.append(str(exc))
            break
        except (requests.RequestException, ValueError, TypeError) as exc:
            issues.append(f"Search discovery request failed ({type(exc).__name__}): {str(exc)[:150]}")
            break
    if requests_ok == 0:
        return [], issues or ["No search requests succeeded"]
    # Results can originate from any public domain. Spread requests across
    # different hosts to avoid a single portal dominating a small crawl budget.
    ordered = sorted(candidates, key=lambda u: (-candidates[u][0], candidates[u][1]))
    selected: list[str] = []
    per_host: dict[str, int] = {}
    site_soft_cap = max(2, min(4, max_urls // 5))
    for url in ordered:
        host = urlparse(url).hostname or ""
        if per_host.get(host, 0) >= site_soft_cap:
            continue
        selected.append(url)
        per_host[host] = per_host.get(host, 0) + 1
        if len(selected) >= max_urls:
            break
    if len(selected) < max_urls:
        selected_set = set(selected)
        for url in ordered:
            if url not in selected_set:
                selected.append(url)
                if len(selected) >= max_urls:
                    break
    return selected, issues


def crawl(query: PlotQuery, seeds: list[str], max_pages: int = 20,
          max_depth: int = 1, fetcher: RobotsAwareFetcher | None = None) -> CrawlOutput:
    query.validate()
    if not seeds:
        raise ValueError("No candidate URLs found. Check your API key and try different plot/location terms.")
    max_pages = max(1, min(int(max_pages), 50))
    max_depth = max(0, min(int(max_depth), 2))
    normalized = [safe_url(u) for u in seeds]
    if any(u is None for u in normalized):
        raise ValueError("Starting URLs must be safe public http(s) webpages")
    hosts = {urlparse(u).hostname or "" for u in normalized if u}
    worker = fetcher or RobotsAwareFetcher()
    queue = deque((u, 0) for u in normalized if u)
    queued = {u for u in normalized if u}
    seen: set[str] = set()
    output: dict[str, dict] = {}
    stats = CrawlStats(candidates=len(queued))
    while queue and len(seen) < max_pages:
        url, depth = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        html, final_url, reason = worker.fetch(url, hosts)
        if html is None or not final_url:
            if "robots" in reason.casefold() or "restricted" in reason.casefold() or "rate limited" in reason.casefold():
                stats.blocked += 1
            # Some HTTP errors are normal when ads are removed, but don't hide them.
            stats.errors.append(f"{urlparse(url).hostname}: {reason}")
            continue
        stats.fetched += 1
        stats.urls_checked.append(final_url)
        parsed = parse_page(html, final_url)
        result = evaluate_page(parsed, final_url, query)
        if result:
            output[final_url] = result
        if depth >= max_depth or likely_detail_page(final_url):
            continue
        for link, anchor in parsed["links"]:
            host = urlparse(link).hostname or ""
            if host not in hosts or not relevant_link(link, anchor, query) or link in queued:
                continue
            queue.append((link, depth + 1))
            queued.add(link)
            if len(queued) >= max_pages * 6:
                break
    stats.candidates = len(queued)
    if queue:
        # Expected bounded coverage is not a network failure. Retain the fact
        # that some pages were not visited, but schedule a routine 15-day check.
        stats.budget_limited = True
    return CrawlOutput(sorted(output.values(), key=lambda row: -row["score"]), stats)


def scan(query: PlotQuery, mode: str, seed_text: str, api_key: str = "",
         max_pages: int = 20, domains: tuple[str, ...] = DISCOVERY_DOMAINS,
         fetcher: RobotsAwareFetcher | None = None, discovery_session: requests.Session | None = None) -> CrawlOutput:
    seeds = normalize_seeds(seed_text)
    urls: list[str] = []
    issues: list[str] = []
    if mode == "hybrid":
        urls, issues = discover_urls(query, api_key, domains=domains, session=discovery_session,
                                     max_urls=min(100, max(20, max_pages * 3)))
        seeds += [u for u in urls if u not in seeds]
    elif mode != "direct":
        raise ValueError("Unknown crawler mode")
    if not seeds:
        if issues:
            raise RuntimeError("No candidate URLs were discovered. " + "; ".join(issues))
        raise ValueError("No candidate webpages were discovered. Try a broader society or block name.")
    result = crawl(query, seeds, max_pages=max_pages, max_depth=1, fetcher=fetcher)
    result.stats.discovered_urls = urls if mode == "hybrid" else []
    result.stats.errors.extend(issues)
    return result
