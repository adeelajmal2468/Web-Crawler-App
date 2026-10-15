"""One-purpose Serper API adapter with safe, human-readable diagnostics.

Serper.dev /search officially only requires `q`. Optional country/size fields are
omitted for compatibility; Pakistan and locality are explicit search keywords.
"""
from __future__ import annotations
import re
import requests

SERPER_API_URL = "https://google.serper.dev/search"

class SerperAPIError(RuntimeError):
    """API rejected a request; don't blindly retry rejected searches."""


def _api_response_detail(response, api_key: str) -> str:
    """Extract provider's message without printing a secret or a full HTML error page."""
    detail = ""
    try:
        obj = response.json()
        if isinstance(obj, dict):
            parts = []
            for k in ("message", "error", "detail", "code"):
                value = obj.get(k)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
            detail = "; ".join(dict.fromkeys(parts))
        elif isinstance(obj, list):
            detail = str(obj[:1])
    except (ValueError, TypeError, AttributeError):
        pass
    if not detail:
        detail = str(getattr(response, "text", "")).strip()
        # Strip HTML and normalize whitespace if provider returned an HTML response.
        detail = re.sub(r"<[^>]+>", " ", detail)
    detail = re.sub(r"\s+", " ", detail).strip()[:230]
    if api_key:
        detail = detail.replace(api_key, "[REDACTED]")
    return detail or "The provider did not include a diagnostic message"


def search_once(query: str, api_key: str, session=None) -> dict:
    """Perform a minimal valid Serper request or raise an actionable exception."""
    api_key = api_key.strip()
    if not api_key:
        raise SerperAPIError("SERPER_API_KEY is empty. Configure .env or the sidebar API key field.")
    if api_key.lower().startswith("bearer "):
        raise SerperAPIError("Paste only the Serper API key, without the 'Bearer ' prefix.")
    if api_key.lower() in {"your_actual_api_key_here", "your_api_key", "...", "change_me"}:
        raise SerperAPIError("The API key is still a placeholder. Obtain a real key at serper.dev.")
    client = session or requests.Session()
    try:
        resp = client.post(
            SERPER_API_URL,
            json={"q": query},
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            timeout=(6, 18),
        )
    except requests.RequestException as exc:
        raise SerperAPIError(f"Serper connection problem ({type(exc).__name__}): {str(exc)[:150]}") from exc
    status = int(getattr(resp, "status_code", 200))
    if status < 200 or status >= 300:
        detail = _api_response_detail(resp, api_key)
        if status in (400, 422):
            help_text = "Check the message above and your Serper dashboard for API key status and remaining credits."
        elif status in (401, 403):
            help_text = "Verify you copied an active Serper.dev API key, not a key from another service."
        elif status == 429:
            help_text = "API rate limit reached. Wait before trying again."
        else:
            help_text = "Check your Serper dashboard and retry only after resolving the error."
        raise SerperAPIError(f"Serper HTTP {status}: {detail}. {help_text}")
    try:
        payload = resp.json()
    except (ValueError, TypeError) as exc:
        raise SerperAPIError("Serper returned non-JSON data despite HTTP 200.") from exc
    if not isinstance(payload, dict):
        raise SerperAPIError("Unexpected Serper result format: response must be an object.")
    if "error" in payload and "organic" not in payload:
        raise SerperAPIError("Serper returned an error: " + str(payload.get("message") or payload["error"])[:200])
    organic = payload.get("organic", [])
    if not isinstance(organic, list):
        raise SerperAPIError("Serper returned an unexpected organic-results format.")
    return payload
