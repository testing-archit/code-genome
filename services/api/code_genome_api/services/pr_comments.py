"""Pull-request impact comments (``pr-comment@1``).

For an opted-in repository, a pull request's changed files are read from GitHub, checked with
the change-impact engine against the latest published snapshot, and summarised in one comment.
Later pushes edit that comment (found by a hidden marker) instead of adding new ones. The
comment states what it is: inferred impact from history and imports, not a review verdict.
"""

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ..schemas import ChangeImpactResponse
from .provider_evidence import API_BASE, ProviderError

COMMENT_VERSION = "pr-comment@1"
MARKER = "<!-- code-genome:impact -->"
MAX_FILES = 200
_PATH = re.compile(r"^[^\x00]{1,1000}$")

Http = Callable[[str, str, str | None, dict[str, Any] | None], Any]


def github_http(method: str, url: str, token: str | None, body: dict[str, Any] | None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "code-genome",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(  # noqa: S310
            Request(url, data=data, headers=headers, method=method), timeout=15
        ) as response:
            raw = response.read(5_000_001)
            return json.loads(raw) if raw else None
    except HTTPError as error:
        raise ProviderError(f"GitHub returned HTTP {error.code}.") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ProviderError("GitHub could not be reached.") from error


def _base(owner: str, repo: str) -> str:
    return f"{API_BASE}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"


def pull_request_files(
    owner: str, repo: str, number: int, token: str | None, http: Http | None = None
) -> list[str]:
    """Changed paths of a pull request (renames give the new path), at most ``MAX_FILES``."""
    http = http or github_http
    paths: list[str] = []
    for page in range(1, 4):
        items = http(
            "GET",
            f"{_base(owner, repo)}/pulls/{number}/files?per_page=100&page={page}",
            token,
            None,
        )
        if not isinstance(items, list):
            raise ProviderError("GitHub returned pull-request files in an unexpected shape.")
        for item in items:
            filename = item.get("filename") if isinstance(item, dict) else None
            if (
                isinstance(filename, str)
                and _PATH.match(filename)
                and item.get("status") != "removed"
            ):
                paths.append(filename)
        if len(items) < 100 or len(paths) >= MAX_FILES:
            break
    return list(dict.fromkeys(paths))[:MAX_FILES]


def render_comment(impact: ChangeImpactResponse, repository_url: str) -> str:
    """Markdown for the comment; every listed file links to the analysed commit."""
    sha = impact.snapshot_sha
    blob = f"{repository_url}/blob/{sha}"
    risky = sorted(
        (item for item in impact.changed if item.risk_score is not None),
        key=lambda item: -(item.risk_score or 0),
    )[:5]
    lines = [
        MARKER,
        "### Code Genome: change impact",
        "",
        f"Checked {impact.summary.changed_files} changed file(s) against snapshot "
        f"`{sha[:12]}`. **{impact.summary.impacted_files}** other file(s) may be affected"
        + (
            f"; {impact.summary.high_risk_files} changed file(s) have high predicted risk."
            if impact.summary.high_risk_files
            else "."
        ),
        "",
    ]
    if risky:
        lines += ["**Riskiest changed files**", ""]
        lines += [
            f"- [`{item.path}`]({blob}/{item.path}) risk {round((item.risk_score or 0) * 100)}"
            for item in risky
        ]
        lines.append("")
    if impact.impacted:
        lines += ["**Most likely affected**", ""]
        for item in impact.impacted[:8]:
            reason = item.reasons[0] if item.reasons else "related"
            lines.append(f"- [`{item.path}`]({blob}/{item.path}) ({reason})")
        lines.append("")
    if impact.reviewers:
        names = ", ".join(person.name for person in impact.reviewers)
        lines += [f"**Suggested reviewers** (recently changed these files): {names}", ""]
    missing = impact.summary.changed_files - impact.summary.changed_in_snapshot
    if missing:
        lines += [f"{missing} changed file(s) are new or outside the analysed snapshot.", ""]
    lines += [
        "<sub>Inferred from the repository's import graph and commit history "
        f"({COMMENT_VERSION}); not a review verdict and not proof of runtime impact. "
        "Updated on each push.</sub>",
    ]
    return "\n".join(lines)


def upsert_comment(
    owner: str, repo: str, number: int, body: str, token: str, http: Http | None = None
) -> str:
    """Edit Code Genome's existing comment on the pull request, or create one. Returns the URL."""
    http = http or github_http
    base = _base(owner, repo)
    existing = http("GET", f"{base}/issues/{number}/comments?per_page=100", token, None)
    for comment in existing if isinstance(existing, list) else []:
        if isinstance(comment, dict) and MARKER in str(comment.get("body", "")):
            updated = http(
                "PATCH", f"{base}/issues/comments/{int(comment['id'])}", token, {"body": body}
            )
            return str((updated or {}).get("html_url", ""))
    created = http("POST", f"{base}/issues/{number}/comments", token, {"body": body})
    return str((created or {}).get("html_url", ""))
