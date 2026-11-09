"""Optional update check (no GUI). Off by default.

When the owner switches it on (or presses "Check now" in About) GhostLock asks GitHub for the latest
release of ``APP_REPO``. That is the only network request the app ever makes. Nothing is sent about you.
"""
import json
import re
import urllib.request

from gl_core import APP_NAME, APP_REPO, APP_VERSION


def parse_version(text):
    numbers = re.findall(r"\d+", str(text).split("-")[0])
    return tuple(int(n) for n in numbers[:4]) or (0,)


def is_newer(latest, current=APP_VERSION):
    a, b = parse_version(latest), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _fetch(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310 (fixed https URL)
        return response.read()


def check_latest(repo=APP_REPO, current=APP_VERSION, fetch=_fetch):
    """Returns {'update': bool, 'version': str, 'url': str}. Raises on network or format errors."""
    if not repo or "/" not in repo:
        raise ValueError("No update source is configured")
    data = json.loads(fetch(f"https://api.github.com/repos/{repo}/releases/latest").decode("utf-8"))
    tag = str(data.get("tag_name") or "").lstrip("vV")
    if not tag:
        raise ValueError("The release has no version tag")
    return {
        "update": is_newer(tag, current),
        "version": tag,
        "url": str(data.get("html_url") or f"https://github.com/{repo}/releases/latest"),
    }
