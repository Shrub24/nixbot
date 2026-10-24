"""Admission decisions of the pull-request approval gate.

`nixbot.approval.untrusted` is the single predicate webhooks and
reconciliation both go through, so the exemption matrix is pinned here
rather than through the service.
"""

from __future__ import annotations

from typing import Any

import pytest

from nixbot.approval import untrusted
from nixbot.config import PrApprovalConfig
from nixbot.webhooks import ChangeRequest

BOT = "github:our-updater[bot]"


def pr(**overrides: Any) -> ChangeRequest:
    fields: dict[str, Any] = {
        "forge": "github",
        "forge_repo_id": "1",
        "branch": "main",
        "commit_sha": "c0",
        "pr_number": 1,
        "pr_author": "github:eve",
        "author_association": "NONE",
    }
    fields.update(overrides)
    return ChangeRequest(**fields)


def test_defaults_keep_existing_admission() -> None:
    config = PrApprovalConfig()
    assert config.enable is False
    assert config.trust_same_repo is True
    assert config.trusted_authors == []
    assert config.trusted_associations == [
        "OWNER",
        "MEMBER",
        "COLLABORATOR",
        "CONTRIBUTOR",
    ]


ONLY_LISTED_AUTHORS = PrApprovalConfig(
    enable=True,
    trust_same_repo=False,
    trusted_associations=[],
    trusted_authors=[BOT],
)


@pytest.mark.parametrize(
    ("config", "change", "held"),
    [
        pytest.param(PrApprovalConfig(), pr(), False, id="gate-disabled"),
        pytest.param(PrApprovalConfig(enable=True), pr(), True, id="fork-none"),
        pytest.param(
            PrApprovalConfig(enable=True),
            pr(author_association="CONTRIBUTOR"),
            False,
            id="trusted-association",
        ),
        pytest.param(
            PrApprovalConfig(enable=True),
            pr(head_in_base_repo=True),
            False,
            id="same-repo-trusted-by-default",
        ),
        pytest.param(
            PrApprovalConfig(enable=True, trust_same_repo=False),
            pr(head_in_base_repo=True),
            True,
            id="same-repo-held",
        ),
        pytest.param(
            PrApprovalConfig(enable=True, trust_same_repo=False),
            pr(head_in_base_repo=True, author_association="OWNER"),
            False,
            id="association-independent-of-same-repo",
        ),
        pytest.param(
            ONLY_LISTED_AUTHORS,
            pr(pr_author=BOT, head_in_base_repo=True),
            False,
            id="listed-author-bypasses",
        ),
        pytest.param(
            ONLY_LISTED_AUTHORS,
            pr(pr_author="github:renovate[bot]", head_in_base_repo=True),
            True,
            id="unlisted-author-held",
        ),
        pytest.param(
            ONLY_LISTED_AUTHORS,
            pr(head_in_base_repo=True, author_association="OWNER"),
            True,
            id="only-listed-authors",
        ),
        pytest.param(
            PrApprovalConfig(enable=True, trusted_authors=[BOT]),
            pr(pr_author=BOT, author_association="FIRST_TIME_CONTRIBUTOR"),
            False,
            id="listed-author-any-association",
        ),
        pytest.param(
            PrApprovalConfig(enable=True),
            pr(forge="gitea"),
            False,
            id="other-forge",
        ),
        pytest.param(
            PrApprovalConfig(enable=True),
            pr(pr_number=None),
            False,
            id="push-not-pr",
        ),
    ],
)
def test_admission(config: PrApprovalConfig, change: ChangeRequest, held: bool) -> None:
    assert untrusted(config, change) is held
