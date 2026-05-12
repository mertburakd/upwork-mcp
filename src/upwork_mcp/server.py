"""Upwork MCP Server - Main entry point."""

import argparse
import asyncio
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .browser.auth import check_session, login_interactive, logout
from .browser.client import close_browser, get_browser
from .tools.jobs import (
    JobDetailsParams,
    JobSearchParams,
    get_job_details,
    search_jobs,
)
from .tools.portfolio import PortfolioItemParams, get_portfolio_item
from .tools.profile import get_connects_balance, get_my_profile
from .tools.proposals import (
    ProposalsParams,
    get_proposal_details,
    get_proposals,
)

# Initialize FastMCP server
mcp = FastMCP(
    name="upwork-mcp",
    instructions=(
        "Upwork MCP — job search, job details, proposal list/detail, portfolio "
        "items, profile, and connects balance. Submit/withdraw proposals and "
        "all messaging/contract tools are intentionally NOT exposed."
    ),
)


# ============================================================================
# Job Tools
# ============================================================================


@mcp.tool()
async def upwork_search_jobs(
    query: Annotated[str, Field(description="Search keywords")],
    category: Annotated[str | None, Field(description="Job category filter")] = None,
    budget_min: Annotated[int | None, Field(description="Minimum fixed-price budget in USD")] = None,
    budget_max: Annotated[int | None, Field(description="Maximum fixed-price budget in USD")] = None,
    hourly_rate_min: Annotated[int | None, Field(description="Minimum hourly rate in USD")] = None,
    hourly_rate_max: Annotated[int | None, Field(description="Maximum hourly rate in USD")] = None,
    experience_level: Annotated[
        list[str] | str | None,
        Field(description="Experience filter — one of or list of: entry, intermediate, expert"),
    ] = None,
    job_type: Annotated[
        str | None,
        Field(description="Job type: 'hourly' or 'fixed'. Omit to include both."),
    ] = None,
    workload: Annotated[
        list[str] | str | None,
        Field(
            description=(
                "Workload filter — one of or list of: 'as_needed' (<30h/wk), "
                "'part_time', 'full_time' (30+ h/wk)."
            )
        ),
    ] = None,
    duration: Annotated[
        list[str] | str | None,
        Field(
            description=(
                "Project duration — one of or list of: 'week' (<1mo), 'month' (1-3mo), "
                "'semester' (3-6mo), 'ongoing' (6+mo)."
            )
        ),
    ] = None,
    location: Annotated[
        list[str] | str | None,
        Field(
            description=(
                "Client location filter, e.g. 'United States', 'Europe', "
                "'United Kingdom'. Accepts a list."
            )
        ),
    ] = None,
    proposals_max: Annotated[
        int | None,
        Field(
            description=(
                "Max proposals tier. Accepts 4, 9, 14, 19 or 49 (maps to "
                "Upwork's bucketed filter)."
            )
        ),
    ] = None,
    payment_verified: Annotated[
        bool | None,
        Field(description="If true, restrict to clients with verified payment method."),
    ] = None,
    contract_to_hire: Annotated[
        bool | None,
        Field(description="If true, include contract-to-hire jobs."),
    ] = None,
    client_hires: Annotated[
        str | None,
        Field(description="Client's prior hire bucket, e.g. '1-9,10-' for 1+ hires."),
    ] = None,
    sort: Annotated[
        str | None,
        Field(description="Sort order, e.g. 'recency' or 'relevance+desc'."),
    ] = None,
    feed: Annotated[
        bool,
        Field(
            description=(
                "If true, use the personalised best-matches feed (largely ignores the query). "
                "If false (default) perform a real keyword search at /nx/search/jobs/."
            )
        ),
    ] = False,
    limit: Annotated[int, Field(description="Maximum number of results", ge=1, le=50)] = 20,
) -> list[dict]:
    """Search for jobs on Upwork with rich filters.

    Returns lightweight previews. Per-tile fields:
      - title, url, posted, description, skills
      - job_type ('Fixed price' or 'Hourly: $X - $Y'),
        hourly_rate_range (if hourly), budget (if fixed),
        experience_level
      - payment_verified, client_rating, total_spent, client_country
      - proposals_tier (e.g. 'Less than 5', '5 to 10', '10 to 15') —
        USE THIS to skip saturated jobs before calling get_job_details

    The richer 'Activity on this job' block — interviewing count, hires,
    last_viewed_by_client, invites_sent, unanswered_invites — is NOT on
    the tile; only get_job_details returns it. Before recommending or
    applying to a job, call upwork_get_job_details and reject jobs where:
      - activity.hires (or 'already_hired') > 0  → someone is already on
        the contract; new applicants will waste connects
      - activity.interviewing > 0 AND activity.last_viewed_by_client is
        recent  → client has shortlisted, not actively reviewing new bids
      - proposals_tier is 50+ on a small fixed-price job

    Do NOT make a recommendation off the search payload alone.
    """
    params = JobSearchParams(
        query=query,
        category=category,
        budget_min=budget_min,
        budget_max=budget_max,
        hourly_rate_min=hourly_rate_min,
        hourly_rate_max=hourly_rate_max,
        experience_level=experience_level,
        job_type=job_type,
        workload=workload,
        duration=duration,
        location=location,
        proposals_max=proposals_max,
        payment_verified=payment_verified,
        contract_to_hire=contract_to_hire,
        client_hires=client_hires,
        sort=sort,
        feed=feed,
        limit=limit,
    )
    return await search_jobs(params)


@mcp.tool()
async def upwork_get_job_details(
    job_url: Annotated[
        str,
        Field(
            description=(
                "Full Upwork job URL, raw job id (e.g. ~022...), or the "
                "search-modal URL (/nx/search/jobs/details/~ID?...). "
                "Modal URLs are auto-canonicalised."
            )
        ),
    ]
) -> dict:
    """Get detailed information about a specific Upwork job posting.

    Returns:
      - title, description, posted, job_type, workload, duration,
        experience_level, project_type, skills
      - bid_range: {high, avg, low} — actual freelancer bids on this job
      - activity: proposals, interviewing, invites_sent, unanswered_invites,
        hires, last_viewed_by_client — USE THESE before recommending
      - client: country, city, payment_verified, phone_verified, rating,
        total_spent, hires (total + active), avg_hourly_rate_paid (what
        this client actually pays per hour), total_hours_billed,
        member_since, job_posting_stats (jobs_posted + hire_rate_pct)
      - connects_required, available_connects

    Quote returned values VERBATIM when presenting them to the user — do
    not round bid_range averages, do not invent total_spent, do not
    paraphrase client.member_since dates.
    """
    params = JobDetailsParams(job_url=job_url)
    return await get_job_details(params)


# ============================================================================
# Profile Tools
# ============================================================================


@mcp.tool()
async def upwork_get_my_profile() -> dict:
    """Fetch your Upwork freelancer profile.

    Returns name, professional_title, hourly_rate, city, country, connects,
    and the page document title. Fields that aren't rendered on the page
    are omitted; don't infer missing values.
    """
    return await get_my_profile()


@mcp.tool()
async def upwork_get_connects_balance() -> dict:
    """Get current Upwork Connects balance.

    Returns {available: int, available_label: str}.
    """
    return await get_connects_balance()


# ============================================================================
# Portfolio Tools
# ============================================================================


@mcp.tool()
async def upwork_get_portfolio_item(
    url: Annotated[
        str,
        Field(
            description=(
                "Full portfolio modal URL "
                "(https://www.upwork.com/freelancers/~01...?p=<project_id>) "
                "or a raw numeric project id (paired with freelancer_url)."
            )
        ),
    ],
    freelancer_url: Annotated[
        str | None,
        Field(
            description=(
                "Optional freelancer profile URL to combine with a raw project id."
            )
        ),
    ] = None,
) -> dict:
    """Read one Upwork portfolio item (case study) from the freelancer
    profile modal at `?p=<id>`.

    Returns:
      title, role, description, skills (list), published (date),
      images (list of URLs), links (list of URLs).
    """
    params = PortfolioItemParams(url=url, freelancer_url=freelancer_url)
    return await get_portfolio_item(params)


# ============================================================================
# Proposal Tools (read-only — submit/withdraw are intentionally NOT exposed)
# ============================================================================


@mcp.tool()
async def upwork_get_proposals(
    tab: Annotated[
        str,
        Field(description="One of 'active' (default), 'archived', 'referrals'."),
    ] = "active",
    limit: Annotated[
        int, Field(description="Maximum rows per section", ge=1, le=100)
    ] = 20,
) -> dict:
    """List your proposals from /nx/proposals/.

    Returns a dict shaped:
      {
        "tab": "active",
        "sections": {
          "offers": {"count": int, "items": [...]},
          "invites_from_clients": {"count": int, "items": [...]},
          "active_proposals": {"count": int, "items": [...]},
          "submitted_proposals": {"count": int, "items": [...]},
        }
      }

    Each item has job_title, url (link to /nx/proposals/<id>), initiated,
    initiated_relative, profile. Use the url with upwork_get_proposal_details.
    """
    params = ProposalsParams(tab=tab, limit=limit)
    return await get_proposals(params)


@mcp.tool()
async def upwork_get_proposal_details(
    proposal_url: Annotated[
        str,
        Field(
            description=(
                "Full /nx/proposals/<id> URL or just the numeric id."
            )
        ),
    ]
) -> dict:
    """Get details of a single submitted proposal.

    Returns the proposal record with the original job's title/description/
    posted/category/features/skills, the freelancer's proposed terms
    (hourly rate or fixed bid, you-receive amount, rate-increase),
    the cover letter, the profile highlights attached to the proposal,
    a snapshot of the client block, and is_editable (true while the
    proposal is still live in /nx/proposals/<id>).
    """
    return await get_proposal_details(proposal_url)


# ============================================================================
# Session Tools
# ============================================================================


@mcp.tool()
async def upwork_check_session() -> dict:
    """Check if the current Upwork session is valid."""
    browser = get_browser()
    try:
        await browser.start()
        logged_in = await browser.is_logged_in()
        return {
            "logged_in": logged_in,
            "message": (
                "Session is valid"
                if logged_in
                else "Session expired. Run 'uv run upwork-mcp --login' to authenticate."
            ),
        }
    except Exception as e:
        return {"logged_in": False, "error": str(e)}


@mcp.tool()
async def upwork_close_session() -> dict:
    """Close browser session and cleanup resources."""
    await close_browser()
    return {"status": "closed", "message": "Browser session closed successfully"}


# ============================================================================
# CLI Entry Point
# ============================================================================


def main():
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="Upwork MCP Server - Browser automation for Upwork",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  upwork-mcp --login        Open browser for manual login
  upwork-mcp --check        Check if session is valid
  upwork-mcp --logout       Clear saved session
  upwork-mcp                Start MCP server (default)
        """,
    )

    parser.add_argument(
        "--login",
        action="store_true",
        help="Open browser for manual login to Upwork",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if current session is valid",
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="Clear saved session",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Show browser window (for debugging)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30000,
        help="Page timeout in milliseconds (default: 30000)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio"],
        default="stdio",
        help="MCP transport type (default: stdio)",
    )

    args = parser.parse_args()

    if args.login:
        asyncio.run(login_interactive())
        return

    if args.check:
        async def check():
            result = await check_session()
            if result:
                print("✓ Session is valid")
            else:
                print("✗ Session expired or invalid")
                print("  Run 'uv run upwork-mcp --login' to authenticate")

        asyncio.run(check())
        return

    if args.logout:
        asyncio.run(logout())
        return

    # Initialize browser with settings
    get_browser(
        headless=not args.no_headless,
        timeout=args.timeout,
    )

    # Run MCP server
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
