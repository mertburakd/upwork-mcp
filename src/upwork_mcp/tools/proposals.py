"""Proposal tools for Upwork MCP."""

from pydantic import BaseModel, Field
from ..browser.client import get_browser


class ProposalsParams(BaseModel):
    """Parameters for getting proposals."""
    status: str = Field(
        default="active",
        description="Filter by status: active, submitted, archived, or all"
    )
    limit: int = Field(default=20, ge=1, le=50, description="Maximum number of results")


class SubmitProposalParams(BaseModel):
    """Parameters for submitting a proposal."""
    job_url: str = Field(description="Full Upwork job URL")
    cover_letter: str = Field(description="Cover letter content")
    rate: float | None = Field(default=None, description="Proposed hourly rate (for hourly jobs)")
    bid: float | None = Field(default=None, description="Bid amount (for fixed-price jobs)")
    answers: list[str] | None = Field(default=None, description="Answers to screening questions")


async def get_proposals(params: ProposalsParams) -> list[dict]:
    """Get your submitted proposals on Upwork.

    Returns a list of proposals with job title, status, bid amount, and dates.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Navigate to proposals page
    status_path = {
        "active": "active",
        "submitted": "submitted",
        "archived": "archived",
        "all": ""
    }.get(params.status.lower(), "active")

    url = f"https://www.upwork.com/nx/proposals/{'?status=' + status_path if status_path else ''}"
    await page.goto(url, wait_until="domcontentloaded")

    proposals = []

    # Wait for proposals to load
    try:
        await page.wait_for_selector('[data-test="proposal-tile"], .proposal-row', timeout=10000)
    except Exception:
        # No proposals or different structure
        pass

    # Extract proposal cards
    proposal_els = await page.query_selector_all('[data-test="proposal-tile"], .proposal-row, article')

    for el in proposal_els[:params.limit]:
        try:
            proposal = await _extract_proposal(el)
            if proposal:
                proposals.append(proposal)
        except Exception:
            continue

    return proposals


async def _extract_proposal(el) -> dict | None:
    """Extract proposal data from element."""
    proposal = {}

    # Job title
    title_el = await el.query_selector('[data-test="job-title"], .job-title, a h3, h4')
    if title_el:
        proposal["job_title"] = (await title_el.text_content() or "").strip()
        href = await title_el.get_attribute("href")
        if href:
            proposal["job_url"] = href if href.startswith("http") else f"https://www.upwork.com{href}"

    if not proposal.get("job_title"):
        return None

    # Status
    status_el = await el.query_selector('[data-test="proposal-status"], .status-badge, .proposal-status')
    if status_el:
        proposal["status"] = (await status_el.text_content() or "").strip()

    # Bid/rate
    bid_el = await el.query_selector('[data-test="bid-amount"], .bid, .rate')
    if bid_el:
        proposal["bid"] = (await bid_el.text_content() or "").strip()

    # Submitted date
    date_el = await el.query_selector('[data-test="submitted-date"], .date, time')
    if date_el:
        proposal["submitted"] = (await date_el.text_content() or "").strip()

    # Client viewed
    viewed_el = await el.query_selector('[data-test="client-viewed"], .viewed')
    proposal["client_viewed"] = viewed_el is not None

    # Interview status
    interview_el = await el.query_selector('[data-test="interview-status"], .interview')
    if interview_el:
        proposal["interview_status"] = (await interview_el.text_content() or "").strip()

    # Connects used
    connects_el = await el.query_selector('[data-test="connects-used"], .connects')
    if connects_el:
        text = (await connects_el.text_content() or "").strip()
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            proposal["connects_used"] = int(numbers[0])

    return proposal


async def get_proposal_details(proposal_url: str) -> dict:
    """Get detailed information about a specific proposal.

    Args:
        proposal_url: URL to the proposal

    Returns details including cover letter, bid, and any messages.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    await page.goto(proposal_url, wait_until="domcontentloaded")

    details = {"url": proposal_url}

    # Job title
    title_el = await page.query_selector('[data-test="job-title"], h1, .job-title')
    if title_el:
        details["job_title"] = (await title_el.text_content() or "").strip()

    # Cover letter
    cover_el = await page.query_selector('[data-test="cover-letter"], .cover-letter')
    if cover_el:
        details["cover_letter"] = (await cover_el.text_content() or "").strip()

    # Bid/Rate
    bid_el = await page.query_selector('[data-test="bid-amount"], .bid-amount')
    if bid_el:
        details["bid"] = (await bid_el.text_content() or "").strip()

    # Status
    status_el = await page.query_selector('[data-test="proposal-status"], .status')
    if status_el:
        details["status"] = (await status_el.text_content() or "").strip()

    # Client response/messages
    messages = []
    message_els = await page.query_selector_all('[data-test="message"], .message-item')
    for el in message_els:
        msg_text = await el.text_content()
        if msg_text:
            messages.append(msg_text.strip())
    details["messages"] = messages

    return details


async def submit_proposal(params: SubmitProposalParams) -> dict:
    """Submit a proposal to an Upwork job.

    IMPORTANT: This is a sensitive action that will spend Connects.
    Make sure the cover letter and rate/bid are correct before submitting.

    Returns submission status and connects used.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Navigate to job page first
    await page.goto(params.job_url, wait_until="domcontentloaded")

    # Click apply button
    apply_btn = await page.query_selector('[data-test="apply-button"], button:has-text("Apply Now")')
    if not apply_btn:
        return {"status": "error", "message": "Apply button not found. Job may be closed or unavailable."}

    await apply_btn.click()
    await page.wait_for_load_state("domcontentloaded")

    # Fill in rate/bid
    if params.rate:
        rate_input = await page.query_selector('[data-test="hourly-rate-input"], input[name*="rate"]')
        if rate_input:
            await rate_input.fill(str(params.rate))

    if params.bid:
        bid_input = await page.query_selector('[data-test="bid-input"], input[name*="bid"], input[name*="amount"]')
        if bid_input:
            await bid_input.fill(str(params.bid))

    # Fill cover letter
    cover_textarea = await page.query_selector('[data-test="cover-letter-input"], textarea[name*="cover"], textarea')
    if cover_textarea:
        await cover_textarea.fill(params.cover_letter)

    # Answer screening questions if provided
    if params.answers:
        question_inputs = await page.query_selector_all('[data-test="question-input"], .question-answer textarea, .screening-question textarea')
        for i, answer in enumerate(params.answers):
            if i < len(question_inputs):
                await question_inputs[i].fill(answer)

    # Check for connects required
    connects_el = await page.query_selector('[data-test="connects-required"], .connects-info')
    connects_required = 0
    if connects_el:
        text = (await connects_el.text_content() or "")
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            connects_required = int(numbers[0])

    # Submit the proposal
    submit_btn = await page.query_selector('[data-test="submit-proposal"], button[type="submit"]:has-text("Submit"), button:has-text("Send")')
    if not submit_btn:
        return {"status": "error", "message": "Submit button not found"}

    await submit_btn.click()

    # Wait for confirmation
    try:
        await page.wait_for_selector('[data-test="proposal-submitted"], .success-message', timeout=15000)
        return {
            "status": "submitted",
            "connects_used": connects_required,
            "message": "Proposal submitted successfully"
        }
    except Exception:
        # Check for error message
        error_el = await page.query_selector('[data-test="error-message"], .error, .alert-danger')
        if error_el:
            error_text = (await error_el.text_content() or "").strip()
            return {"status": "error", "message": error_text}

        return {"status": "unknown", "message": "Could not confirm submission status"}


async def withdraw_proposal(proposal_url: str) -> dict:
    """Withdraw a submitted proposal.

    Args:
        proposal_url: URL to the proposal to withdraw

    Returns withdrawal status.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    await page.goto(proposal_url, wait_until="domcontentloaded")

    # Find withdraw button
    withdraw_btn = await page.query_selector('[data-test="withdraw-button"], button:has-text("Withdraw")')
    if not withdraw_btn:
        return {"status": "error", "message": "Withdraw button not found. Proposal may already be closed."}

    await withdraw_btn.click()

    # Confirm withdrawal in modal
    confirm_btn = await page.query_selector('[data-test="confirm-withdraw"], button:has-text("Yes"), button:has-text("Confirm")')
    if confirm_btn:
        await confirm_btn.click()

    try:
        await page.wait_for_selector('[data-test="withdrawal-confirmed"], .success', timeout=10000)
        return {"status": "withdrawn", "message": "Proposal withdrawn successfully"}
    except Exception:
        return {"status": "unknown", "message": "Could not confirm withdrawal"}
