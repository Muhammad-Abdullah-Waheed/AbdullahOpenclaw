"""HTTP fetch tool (Stage 7+) — network access with caching, ETag, optional Crawl4AI.

Mirrors OpenClaw ``webread`` + optional richer rendering. See ``build-your-own-openclaw/06-web-tools``.

- **urllib** (default): light, no extra deps; uses ``WEBFETCH_CACHE_*`` + conditional GET.
- **crawl4ai**: JS rendering + markdown (install ``[crawl]`` extra).
"""

from __future__ import annotations

import html as html_module
import ipaddress
import logging
import os
import re
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from abdullah_openclaw.integrations.web_fetch_cache import (
    cache_enabled,
    load_entry_for_revalidate,
    normalize_cache_key,
    store_success,
    try_return_fresh_memory,
)

logger = logging.getLogger("abdullah_openclaw.web")

_TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_SCRIPT_RE = re.compile(r"(?is)<script[^>]*>.*?</script>")
_STYLE_RE = re.compile(r"(?is)<style[^>]*>.*?</style>")
_TAG_RE = re.compile(r"<[^>]+>")


def fetch_tool_enabled() -> bool:
    raw = (os.environ.get("WEBFETCH_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _renderer() -> str:
    return (os.environ.get("WEBFETCH_RENDERER") or "urllib").strip().lower()


def _allow_host_patterns() -> list[str] | None:
    raw = (os.environ.get("WEBFETCH_ALLOW_HOSTS") or "*").strip()
    if not raw or raw == "*":
        return None
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _host_matches_allowlist(host: str, patterns: list[str] | None) -> bool:
    if patterns is None:
        return True
    h = host.lower().rstrip(".")
    for p in patterns:
        p = p.lower().rstrip(".")
        if h == p or h.endswith("." + p):
            return True
    return False


def _is_blocked_ssrf(hostname: str) -> bool:
    h = hostname.lower().strip()
    if h in {"localhost", "0.0.0.0"} or h.endswith(".localhost"):
        return True
    if h.startswith("127.") or h.startswith("10.") or h.startswith("192.168."):
        return True
    if h.startswith("172."):
        parts = h.split(".")
        if len(parts) >= 2 and parts[0] == "172":
            try:
                second = int(parts[1])
                if 16 <= second <= 31:
                    return True
            except ValueError:
                pass
    if h in {"::1", "[::1]"}:
        return True
    try:
        ip = ipaddress.ip_address(hostname.strip("[]"))
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast:
            return True
    except ValueError:
        pass
    return False


def _resolve_first_a_record(hostname: str) -> str | None:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        for fam, _, _, _, sockaddr in infos:
            if fam == socket.AF_INET:
                return sockaddr[0]
            if fam == socket.AF_INET6:
                return sockaddr[0]
    except OSError as e:
        logger.info("web_tools: DNS lookup failed for %s: %s", hostname, e)
        return None
    return None


def _resolved_ip_blocked(hostname: str) -> bool:
    ip_s = _resolve_first_a_record(hostname)
    if not ip_s:
        return False
    try:
        ip = ipaddress.ip_address(ip_s)
        return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast
    except ValueError:
        return False


def _html_to_text(raw: bytes, charset_hint: str | None) -> tuple[str, str]:
    enc = (charset_hint or "utf-8").strip() or "utf-8"
    try:
        html = raw.decode(enc)
    except UnicodeDecodeError:
        html = raw.decode("latin-1", errors="replace")

    m = _TITLE_RE.search(html)
    title = m.group(1).strip() if m else ""
    title = _TAG_RE.sub(" ", title)
    title = re.sub(r"\s+", " ", html_module.unescape(title)).strip()

    body = _SCRIPT_RE.sub(" ", html)
    body = _STYLE_RE.sub(" ", body)
    body = _TAG_RE.sub(" ", body)
    text = re.sub(r"\s+", " ", html_module.unescape(body)).strip()
    return title, text


def _clip_text(text: str) -> str:
    clip = _env_int("WEBFETCH_MAX_TEXT_CHARS", 24_000)
    if len(text) > clip:
        return text[: clip // 2] + "\n\n[... truncated for tool output ...]\n\n" + text[-clip // 2 :]
    return text


def _format_display(url: str, title: str, text: str, *, via: str) -> str:
    head = f"URL: {url}\n"
    if title:
        head += f"Title: {title}\n"
    head += f"\n(via {via})\n\n"
    return head + _clip_text(text)


def _validate_url(url: str) -> tuple[str | None, str | None]:
    """Return (error_message, host) or (None, host)."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return "ERROR: only http and https URLs are allowed", None
    if not parsed.netloc:
        return "ERROR: missing host in URL", None
    host = parsed.hostname
    if not host:
        return "ERROR: could not parse hostname", None
    if _is_blocked_ssrf(host):
        return "ERROR: host blocked (SSRF policy)", None
    patterns = _allow_host_patterns()
    if not _host_matches_allowlist(host, patterns):
        return f"ERROR: host {host!r} not in WEBFETCH_ALLOW_HOSTS", None
    if _resolved_ip_blocked(host):
        return "ERROR: host resolves to a blocked address (SSRF policy)", None
    return None, host


def fetch_url_text(url: str) -> str:
    """GET ``url`` and return text for the model (or ERROR: line)."""
    if not fetch_tool_enabled():
        return "ERROR: fetch_url is disabled (WEBFETCH_ENABLED=0)"

    err, _host = _validate_url(url)
    if err:
        return err

    url_key = normalize_cache_key(url)
    cached_fast = try_return_fresh_memory(url_key)
    if cached_fast is not None:
        return cached_fast

    timeout = float(_env_int("WEBFETCH_TIMEOUT_SECONDS", 15))
    max_bytes = _env_int("WEBFETCH_MAX_BYTES", 500_000)

    if _renderer() == "crawl4ai":
        return _fetch_via_crawl4ai(url, url_key, timeout)

    return _fetch_via_urllib(url, url_key, timeout, max_bytes)


def _fetch_via_crawl4ai(url: str, url_key: str, timeout: float) -> str:
    try:
        from abdullah_openclaw.integrations.crawl_read import fetch_markdown
    except ImportError as e:
        return f"ERROR: crawl4ai support missing ({e})"

    try:
        display = fetch_markdown(url, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: Crawl4AI {type(e).__name__}: {e}"

    display = _clip_text(display) if len(display) > _env_int("WEBFETCH_MAX_TEXT_CHARS", 24_000) else display
    if cache_enabled():
        store_success(url_key, display=display, etag=None, last_modified=None)
    return display


def _fetch_via_urllib(url: str, url_key: str, timeout: float, max_bytes: int) -> str:
    prev = load_entry_for_revalidate(url_key) if cache_enabled() else None

    req = Request(
        url,
        headers={
            "User-Agent": (os.environ.get("WEBFETCH_USER_AGENT") or "AbdullahOpenclaw/0.1 (+learning)").strip(),
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        },
        method="GET",
    )
    if prev:
        et = prev.get("etag")
        if isinstance(et, str) and et:
            req.add_header("If-None-Match", et)
        lm = prev.get("last_modified")
        if isinstance(lm, str) and lm:
            req.add_header("If-Modified-Since", lm)

    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status = getattr(resp, "status", None) or resp.getcode()
            if status == 304 and prev:
                disp = str(prev.get("display", ""))
                store_success(
                    url_key,
                    display=disp,
                    etag=prev.get("etag") if isinstance(prev.get("etag"), str) else None,
                    last_modified=prev.get("last_modified") if isinstance(prev.get("last_modified"), str) else None,
                )
                return disp

            content_type = resp.headers.get("Content-Type") or ""
            ctype: str | None = None
            if hasattr(resp.headers, "get_content_charset"):
                try:
                    ctype = resp.headers.get_content_charset()
                except Exception:
                    ctype = None
            raw = resp.read(max_bytes + 1)
            etag = resp.headers.get("ETag")
            last_mod = resp.headers.get("Last-Modified")
    except HTTPError as e:
        if e.code == 304 and prev:
            logger.info("web_cache: 304 via HTTPError %s", url_key[:80])
            disp = str(prev.get("display", ""))
            store_success(
                url_key,
                display=disp,
                etag=prev.get("etag") if isinstance(prev.get("etag"), str) else None,
                last_modified=prev.get("last_modified") if isinstance(prev.get("last_modified"), str) else None,
            )
            return disp
        return f"ERROR: HTTP {e.code} for {url}"
    except URLError as e:
        return f"ERROR: network {type(e).__name__}: {e.reason}"
    except OSError as e:
        return f"ERROR: {type(e).__name__}: {e}"

    if len(raw) > max_bytes:
        return f"ERROR: response larger than WEBFETCH_MAX_BYTES={max_bytes}"

    title, text = _html_to_text(raw, ctype)
    if "text/plain" in content_type.lower():
        try:
            text = raw.decode(ctype or "utf-8", errors="replace").strip()
        except Exception:
            text = raw.decode("utf-8", errors="replace").strip()
        title = title or "(plain text)"

    display = _format_display(url, title, text, via="urllib")
    if cache_enabled():
        store_success(
            url_key,
            display=display,
            etag=etag if isinstance(etag, str) else None,
            last_modified=last_mod if isinstance(last_mod, str) else None,
        )
    return display


def handle_fetch_url(args: dict[str, Any]) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        return "ERROR: missing url"
    logger.info("fetch_url: %s renderer=%s", url, _renderer())
    return fetch_url_text(url)


FETCH_URL_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": (
            "Fetch a public HTTP/HTTPS URL and return page text. "
            "Uses WEBFETCH_RENDERER: urllib (fast) or crawl4ai (JS sites; requires [crawl] extra). "
            "Use web_search to discover URLs, then fetch_url to read one."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to fetch (http or https only).",
                }
            },
            "required": ["url"],
        },
    },
}
