# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
AP Bot â€” Contributor Recognition Module.

Welcomes contributors on their first merged PR and celebrates
milestones (e.g. 10, 25, 50, 100 PRs) with congratulatory comments.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import config
from ..logger import logger

if TYPE_CHECKING:
    from ..gemini import GeminiClient
    from ..github_api import GitHubAPI

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_FIRST_PR_CONGRATS: str = (
    "ðŸŽ‰ **Welcome to the Team, @{author}!** ðŸŽ‰\n\n"
    "Congratulations on having your first pull request merged into **Agent-2**! "
    "We are super excited to have you as part of our developer community.\n\n"
    "Thank you for helping us make this project better! ðŸŒŸ"
)

_MILESTONE_CONGRATS: str = (
    "ðŸ† **Amazing Milestone!** ðŸ†\n\n"
    "Congratulations @{author}! You have just had your **{count}th pull request** "
    "merged into **Agent-2**!\n\n"
    "Your continuous dedication and contribution are invaluable. Keep up the "
    "fantastic work! ðŸš€ðŸ”¥"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(github_api: "GitHubAPI", gemini_client: "GeminiClient" | None = None) -> None:
    """Identify contributor milestones and post celebratory messages.

    Args:
        github_api: Authenticated GitHub API wrapper instance.
        gemini_client: Unused (kept for interface consistency).
    """
    author: str | None = config.PR_AUTHOR
    pr_number_str: str | None = config.PR_NUMBER
    
    if not author:
        logger.error("No PR_AUTHOR found in config. Aborting contributor manager.")
        return
    if not pr_number_str:
        logger.error("No PR_NUMBER found in config. Aborting contributor manager.")
        return

    try:
        pr_number = int(pr_number_str)
        # Fetch PR counts for the user
        logger.info(f"Checking contributor milestones for @{author}...")
        merged_prs = github_api.get_user_pull_requests(author)
        
        pr_count = len(merged_prs)
        logger.info(f"User @{author} has {pr_count} merged PRs in this repo.")

        comment_body: str | None = None
        
        if pr_count == 1:
            comment_body = _FIRST_PR_CONGRATS.format(author=author)
        elif pr_count in {10, 25, 50, 100, 250, 500}:
            comment_body = _MILESTONE_CONGRATS.format(author=author, count=pr_count)
        else:
            logger.info(f"No specific milestone for @{author} at {pr_count} PRs.")
            return

        # Post the comment
        body = f"{comment_body}\n\n{config.BOT_FOOTER}"
        github_api.add_comment(pr_number, body)
        logger.info(f"Posted contributor recognition comment on PR #{pr_number}.")

    except Exception:
        logger.exception(f"Failed to run contributor milestone check for @{author}.")
        raise
