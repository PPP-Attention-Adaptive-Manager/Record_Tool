"""Shared context-node resolution for behavioral graphs and features."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from urllib.parse import urlparse

import pandas as pd

INTERNAL_PROCESS_NAMES = frozenset(
    {
        "dual_task.exe",
        "behavior_collector.exe",
    }
)
PYTHON_PROCESS_NAME = "python.exe"
DUAL_TASK_TITLE_TOKENS = ("dual task", "dual_task")

BROWSER_PROCESS_NAMES = frozenset(
    {
        # Windows
        "chrome.exe",
        "msedge.exe",
        "edge.exe",
        "firefox.exe",
        "brave.exe",
        "bravebrowser.exe",
        "opera.exe",
        "vivaldi.exe",
        # Linux
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "ungoogled-chromium",
        "firefox",
        "firefox-esr",
        "microsoft-edge",
        "microsoft-edge-stable",
        "brave-browser",
        "brave",
        "opera",
        "opera-stable",
        "vivaldi",
        "vivaldi-snapshot",
    }
)

BROWSER_TITLE_SUFFIXES = (
    " - Google Chrome",
    " - Microsoft Edge",
    " - Mozilla Firefox",
    " - Brave",
    " - Opera",
    " - Vivaldi",
)


def normalize_process_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return os.path.basename(text)


def is_internal_app(app_name: Any) -> bool:
    return normalize_process_name(app_name) in INTERNAL_PROCESS_NAMES


def is_internal_context(app_name: Any, window_title: Any = "", title: Any = "") -> bool:
    process_name = normalize_process_name(app_name)
    if process_name in INTERNAL_PROCESS_NAMES:
        return True
    if process_name != PYTHON_PROCESS_NAME:
        return False
    title_text = f"{window_title or ''} {title or ''}".strip().lower()
    return any(token in title_text for token in DUAL_TASK_TITLE_TOKENS)


def is_browser_app(app_name: Any) -> bool:
    return normalize_process_name(app_name) in BROWSER_PROCESS_NAMES


def safe_context_load(value: Any) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def context_from_json_series(series: pd.Series) -> pd.DataFrame:
    if series is None or series.empty:
        return pd.DataFrame()
    return pd.json_normalize(series.fillna("").astype(str).map(safe_context_load))


def resolve_context_node(
    app_name: Any = "",
    url: Any = "",
    title: Any = "",
    window_title: Any = "",
    tab_id: Any = "",
) -> dict[str, Any]:
    """Resolve a raw context row to the graph node model.

    Label priority is URL path, page title, window title, then app name.
    Browser contexts are hashed by URL or title so one browser process can
    produce distinct page/tab nodes.
    """

    app_text = _clean_text(app_name)
    url_text = _clean_text(url)
    title_text = _clean_text(title)
    window_title_text = _clean_text(window_title)
    tab_text = _clean_text(tab_id)

    domain, path, path_depth = split_url(url_text)
    browser = is_browser_app(app_text) or bool(tab_text) or bool(url_text)
    cleaned_window_title = strip_browser_title_suffix(window_title_text) if browser else window_title_text
    page_title = title_text or cleaned_window_title

    url_label = format_url_label(domain, path) if url_text else ""
    if browser:
        label = url_label or page_title or "unknown browser page"
        identity = url_text or page_title or window_title_text or f"{app_text}:unknown-browser-page"
    else:
        label = page_title or app_text or "unknown"
        identity = page_title or window_title_text or app_text or "unknown"

    if browser and url_text:
        node_kind = "tab"
        node_hash_source = url_text
    elif browser and page_title:
        node_kind = "page"
        node_hash_source = page_title
    elif browser:
        node_kind = "page"
        node_hash_source = identity
    else:
        node_kind = "app"
        node_hash_source = identity

    node_id = f"{node_kind}:{stable_hash(node_hash_source)}"
    return {
        "node_id": node_id,
        "node_kind": node_kind,
        "node_type": node_kind,
        "label": label,
        "title": page_title,
        "url": url_text,
        "domain": domain,
        "path": path,
        "path_depth": path_depth,
        "app_name": app_text or "unknown",
        "window_title": window_title_text,
    }


def resolve_context_nodes_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "node_id",
                "node_kind",
                "node_type",
                "label",
                "title",
                "url",
                "domain",
                "path",
                "path_depth",
                "app_name",
                "window_title",
            ]
        )

    records = [
        resolve_context_node(
            app_name=row.get("app_name", ""),
            url=row.get("url", ""),
            title=row.get("title", ""),
            window_title=row.get("window_title", ""),
            tab_id=row.get("tab_id", ""),
        )
        for row in df.to_dict("records")
    ]
    return pd.DataFrame.from_records(records, index=df.index)


def resolve_context_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return resolve_context_node(
        app_name=payload.get("active_app") or payload.get("app_name") or "",
        url=payload.get("url") or "",
        title=payload.get("title") or "",
        window_title=payload.get("window_title") or "",
        tab_id=payload.get("tab_id") or "",
    )


def stable_hash(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def strip_browser_title_suffix(title: Any) -> str:
    text = _clean_text(title)
    lowered = text.lower()
    for suffix in BROWSER_TITLE_SUFFIXES:
        if lowered.endswith(suffix.lower()):
            return text[: -len(suffix)].strip()
    return text


def split_url(url: Any) -> tuple[str, str, int]:
    text = _clean_text(url)
    if not text:
        return "", "", 0
    try:
        parsed = urlparse(text if "://" in text else f"//{text}")
    except Exception:
        return "", "", 0

    domain = (parsed.netloc or parsed.path.split("/", 1)[0]).strip().lower()
    domain = domain.split(":", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    path = parsed.path or ""
    if not parsed.netloc and parsed.path.startswith(domain):
        path = parsed.path[len(domain) :]
    path = path if path.startswith("/") or not path else f"/{path}"
    path_depth = len([part for part in path.split("/") if part])
    return domain, path, path_depth


def format_url_label(domain: str, path: str) -> str:
    domain_text = _clean_text(domain).lower()
    path_text = _clean_text(path)
    if not domain_text:
        return path_text
    if not path_text or path_text == "/":
        return domain_text
    return f"{domain_text}{path_text.rstrip('/')}"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text
