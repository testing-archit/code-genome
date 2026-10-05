"""CI and deployment evidence from GitHub (``provider-evidence@1``).

For the commits in a delivery report's scope we read GitHub Actions check runs and GitHub
Deployments (with their latest status). Raw provider payloads are kept, so every derived
"tests passed" or "deployed" conclusion links back to what GitHub returned. A merge or a code
change never implies either; only these records do.

Requests are bounded (``MAX_COMMITS`` commits per sync), use the repository's stored read token
when present (else ``CODE_GENOME_GITHUB_API_TOKEN``, else anonymous), and every field is
validated before it is stored. Network failures become limitations, not errors.
"""

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PROVIDER_VERSION = "provider-evidence@1"
API_BASE = "https://api.github.com"
MAX_COMMITS = 20
MAX_ITEMS = 50
_SHA = re.compile(r"[0-9a-f]{40}")
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}")
CI_PASSED = {"success"}
CI_NEUTRAL = {"neutral", "skipped"}
DEPLOY_SUCCEEDED = {"success"}

Fetch = Callable[[str, str | None], Any]


class ProviderError(RuntimeError):
    """GitHub could not be reached or returned something unusable."""


@dataclass(frozen=True)
class Signal:
    kind: str  # "ci_run" | "deployment"
    external_id: str
    commit_sha: str
    name: str
    outcome: str  # check-run conclusion or latest deployment state
    environment: str | None
    url: str | None
    observed_at: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)


def github_fetch(url: str, token: str | None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "code-genome",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(url, headers=headers), timeout=10) as response:  # noqa: S310
            return json.loads(response.read(2_000_001))
    except HTTPError as error:
        raise ProviderError(f"GitHub returned HTTP {error.code}.") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ProviderError("GitHub could not be reached.") from error


def repository_slug(external_id: str) -> tuple[str, str] | None:
    parts = external_id.split("/")
    if len(parts) != 2 or not all(_NAME.fullmatch(part) for part in parts):
        return None
    return parts[0], parts[1]


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(value: object, limit: int = 200) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _https(value: object) -> str | None:
    text = _text(value, 500)
    return text if text and text.startswith("https://") else None


def collect_signals(
    owner: str, repo: str, shas: Iterable[str], token: str | None, fetch: Fetch = github_fetch
) -> tuple[list[Signal], list[str]]:
    """Check runs and deployments for up to ``MAX_COMMITS`` commits, plus limitations."""
    base = f"{API_BASE}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
    wanted = [sha for sha in dict.fromkeys(shas) if _SHA.fullmatch(sha)]
    limitations: list[str] = []
    if len(wanted) > MAX_COMMITS:
        limitations.append(
            f"CI and deployment evidence was read for the newest {MAX_COMMITS} of "
            f"{len(wanted)} scoped commits."
        )
    signals: list[Signal] = []
    try:
        for sha in wanted[:MAX_COMMITS]:
            payload = fetch(f"{base}/commits/{sha}/check-runs?per_page={MAX_ITEMS}", token)
            runs = payload.get("check_runs") if isinstance(payload, dict) else None
            for run in runs if isinstance(runs, list) else []:
                if not isinstance(run, dict) or run.get("status") != "completed":
                    continue
                conclusion = _text(run.get("conclusion"), 30)
                if conclusion is None or run.get("id") is None:
                    continue
                signals.append(
                    Signal(
                        "ci_run",
                        f"check_run:{run['id']}",
                        sha,
                        _text(run.get("name")) or "check",
                        conclusion,
                        None,
                        _https(run.get("html_url")),
                        _time(run.get("completed_at")),
                        {key: run.get(key) for key in ("id", "name", "conclusion", "html_url")},
                    )
                )
            deployments = fetch(f"{base}/deployments?sha={sha}&per_page={MAX_ITEMS}", token)
            for deployment in deployments if isinstance(deployments, list) else []:
                if not isinstance(deployment, dict) or deployment.get("id") is None:
                    continue
                statuses = fetch(
                    f"{base}/deployments/{int(deployment['id'])}/statuses?per_page=1", token
                )
                latest = statuses[0] if isinstance(statuses, list) and statuses else {}
                state = _text(latest.get("state"), 30) if isinstance(latest, dict) else None
                environment = _text(deployment.get("environment"), 100)
                signals.append(
                    Signal(
                        "deployment",
                        f"deployment:{deployment['id']}",
                        sha,
                        f"deploy to {environment or 'unknown environment'}",
                        state or "unknown",
                        environment,
                        _https(latest.get("environment_url")) if isinstance(latest, dict) else None,
                        _time(latest.get("created_at") if isinstance(latest, dict) else None)
                        or _time(deployment.get("created_at")),
                        {
                            "id": deployment.get("id"),
                            "environment": environment,
                            "state": state,
                        },
                    )
                )
    except ProviderError as error:
        limitations.append(f"CI and deployment evidence is incomplete: {error}")
    except (TypeError, ValueError, AttributeError):
        limitations.append("GitHub returned CI or deployment data in an unexpected shape.")
    return signals, limitations


def describe(signal: Signal) -> str:
    """Evidence text the auditor matches claim terms against."""
    if signal.kind == "ci_run":
        verdict = "passed" if signal.outcome in CI_PASSED else f"ended {signal.outcome}"
        return f"CI check {signal.name} {verdict} on commit {signal.commit_sha[:12]} tests ci"
    verdict = "succeeded" if signal.outcome in DEPLOY_SUCCEEDED else f"is {signal.outcome}"
    return (
        f"Deployment to {signal.environment or 'an unnamed environment'} {verdict} for commit "
        f"{signal.commit_sha[:12]} deploy deployed release"
    )
