"""Plot number and location matching helpers, with optional web-wide snippet fallback."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse
import requests
from serper_client import search_once, SerperAPIError

API_URL = "https://google.serper.dev/search"


@dataclass(frozen=True)
class PlotQuery:
    plot_number: str
    city: str
    society: str
    block: str = ""

    def validate(self) -> None:
        if not all((self.plot_number.strip(), self.city.strip(), self.society.strip())):
            raise ValueError("Plot number, city and housing society are required.")
        if len(self.plot_number) > 40 or len(self.city) > 80 or len(self.society) > 120 or len(self.block) > 80:
            raise ValueError("One of the search fields is too long.")


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().strip())


def words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def has_phrase_tokens(needle: str, haystack: str) -> bool:
    """Check all meaningful location words are present in returned text."""
    required = words(needle)
    actual = set(words(haystack))
    return bool(required) and all(word in actual for word in required)


def matches_plot_number(text: str, number: str) -> bool:
    """Require a plot marker near the exact identifier; avoid false number matches."""
    number_tokens = words(number)
    if not number_tokens:
        return False
    # A plot number may contain a letter or slash, e.g. 123-A or 123/1.
    identifier = r"[\s\-/]*".join(re.escape(token) for token in number_tokens)
    plot_label = r"(?:plot|plt)\s*(?:(?:number|no\.?|#)\s*)?[:#\-]?\s*"
    patterns = (
        rf"\b{plot_label}{identifier}(?![a-z0-9])",
        rf"(?<![a-z0-9]){identifier}\s*(?:number\s*)?(?:plot)\b",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def classify(result: dict, query: PlotQuery) -> dict | None:
    title = str(result.get("title", ""))
    snippet = str(result.get("snippet", ""))
    link = str(result.get("link", ""))
    if not link.startswith(("https://", "http://")):
        return None
    host = (urlparse(link).hostname or "").lower()
    if not host:
        return None

    content = f"{title} {snippet}"
    if not matches_plot_number(content, query.plot_number):
        return None
    society_match = has_phrase_tokens(query.society, content)
    block_match = not query.block.strip() or has_phrase_tokens(query.block, content)
    city_match = has_phrase_tokens(query.city, content)
    sale_word = bool(re.search(r"\b(?:for sale|selling|on sale|available|sale)\b", content, re.I))
    # These describe search evidence, NOT verified ownership or live availability.
    if society_match and block_match and sale_word:
        evidence = "Strong text match - availability unverified"
        score = 90 if city_match else 80
    elif society_match and block_match:
        evidence = "Location match - availability unverified"
        score = 70
    else:
        evidence = "Possible plot number match - verify location"
        score = 40 if society_match or city_match else 25
    return {
        "title": title or "Untitled property result",
        "url": link,
        "source": host,
        "snippet": snippet,
        "confidence": evidence,
        "score": score,
    }


def build_searches(query: PlotQuery) -> list[str]:
    """Site-agnostic search strings, used only for legacy snippet checks."""
    suffix = f'{query.society} {query.block} {query.city} Pakistan'
    return [
        f'"plot no {query.plot_number}" {suffix} sale',
        f'"plot {query.plot_number}" {suffix} sale',
        f'"plot number {query.plot_number}" {suffix}',
    ]


def search_serper(query: PlotQuery, api_key: str) -> list[dict]:
    query.validate()
    if not api_key.strip():
        raise ValueError("Missing SERPER_API_KEY. Add it to .env before searching.")
    candidates: dict[str, dict] = {}
    failures: list[str] = []
    successful_requests = 0
    for search_text in build_searches(query):
        try:
            payload = search_once(search_text, api_key)
            successful_requests += 1
            for item in payload.get("organic", []):
                candidate = classify(item, query)
                if candidate:
                    candidates[candidate["url"]] = candidate
        except SerperAPIError as error:
            failures.append(f"Search request failed: {error}")
            break
        except (requests.RequestException, ValueError, TypeError) as error:
            failures.append(f"Search request failed: {error}")
            break
    if not successful_requests:
        raise RuntimeError("All search requests failed. Check your API key or internet connection. " + "; ".join(failures))
    if failures:
        # Do not silently imply a complete search if some portals could not be checked.
        raise RuntimeError("Some property sources could not be searched. Please retry. " + "; ".join(failures))
    return sorted(candidates.values(), key=lambda result: (-result["score"], result["source"]))


def demo_results(query: PlotQuery) -> list[dict]:
    """Explicitly simulated evidence for offline classroom demonstrations."""
    query.validate()
    return [{
        "title": f"DEMO ONLY: Plot {query.plot_number} - {query.society}, {query.city}",
        "url": "",
        "source": "Synthetic sample (not a real listing)",
        "snippet": f"Simulated plot for sale in {query.society}, {query.block or 'unspecified block'}, {query.city}. This does not indicate actual availability.",
        "confidence": "Fictional demo record",
        "score": 0,
    }]
