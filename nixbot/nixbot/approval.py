"""Approval gate for untrusted pull requests.

GitHub only. A PR builds without approval when any exemption in
`PrApprovalConfig` applies: its author is listed in `trusted_authors`,
its head branch is in the base repository and `trust_same_repo` is
set, or the webhook's `author_association` is trusted. Everything else
gets an `action_required` check run with an approve button instead of
a build. The click arrives as `check_run.requested_action` (GitHub
shows the button only to users with write access). The web UI offers
the same to repo writers. Approval unlocks the PR, later pushes build.
Repositories opt out with `require_approval = false` in nixbot.toml.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import TYPE_CHECKING, Protocol

from .db_gen import approvals as q
from .webhooks import APPROVE_ACTION, APPROVE_EXTERNAL_PREFIX, ChangeRequest

if TYPE_CHECKING:
    import asyncpg

    from .config import PrApprovalConfig
    from .forge import GitHubAppClient

logger = logging.getLogger(__name__)


def untrusted(config: PrApprovalConfig, change: ChangeRequest) -> bool:
    if not config.enable or change.forge != "github" or change.pr_number is None:
        return False
    # PR author, not the sender of the latest webhook: a maintainer
    # commenting on an automation PR must not admit it. Webhooks and
    # reconciliation both build this from the PR's `user.login`.
    if change.pr_author is not None and change.pr_author in config.trusted_authors:
        return False
    # Pushing the head branch to the base repo already needed write
    # access. Covers GitHub Apps/bots, which always report NONE.
    if config.trust_same_repo and change.head_in_base_repo:
        return False
    return (change.author_association or "NONE") not in config.trusted_associations


async def gate(
    pool: asyncpg.Pool, project_id: int, pr_number: int, change: ChangeRequest
) -> None:
    await q.gate_pr(
        pool,
        project_id=project_id,
        pr_number=pr_number,
        pending=json.dumps(dataclasses.asdict(change)),
    )


async def approve(
    pool: asyncpg.Pool, project_id: int, pr_number: int, actor: str | None
) -> ChangeRequest | None:
    """Mark approved (creating the row if the PR was never gated).
    Returns the held change, if any."""
    pending = await q.approve_pr(
        pool, project_id=project_id, pr_number=pr_number, approved_by=actor
    )
    return None if pending is None else ChangeRequest(**json.loads(pending))


class GatePoster(Protocol):
    async def post_gate(
        self, owner: str, repo: str, sha: str, pr_number: int, details_url: str
    ) -> None: ...


class GitHubGatePoster:
    def __init__(self, client: GitHubAppClient, context_prefix: str) -> None:
        self.client = client
        self.name = f"{context_prefix}/nix-eval"

    async def post_gate(
        self, owner: str, repo: str, sha: str, pr_number: int, details_url: str
    ) -> None:
        installation_id = await self.client.installation_for_repo(f"{owner}/{repo}")
        if installation_id is None:
            return
        token = await self.client.installation_token(installation_id)
        # Same name as the eval status: the approved build creates a
        # fresh run of that name which supersedes this one. (PATCHing
        # it instead leaves GitHub's PR view showing action_required.)
        body = {
            "name": self.name,
            "head_sha": sha,
            "status": "completed",
            "conclusion": "action_required",
            "external_id": f"{APPROVE_EXTERNAL_PREFIX}{pr_number}",
            "details_url": details_url,
            "output": {
                "title": "Waiting for maintainer approval",
                "summary": (
                    "This pull request does not build automatically. "
                    "A maintainer must approve CI for it."
                ),
            },
            "actions": [
                {
                    "label": "Approve CI",
                    "description": "Build this PR and later pushes to it",
                    "identifier": APPROVE_ACTION,
                }
            ],
        }
        response = await self.client.http.post(
            f"{self.client.api_url}/repos/{owner}/{repo}/check-runs",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json=body,
        )
        if response.status_code >= 400:  # noqa: PLR2004
            logger.error(
                "failed to post approval gate",
                extra={
                    "repo": f"{owner}/{repo}",
                    "status": response.status_code,
                    "body": response.text[:500],
                },
            )
