"""Contract tools for Upwork MCP."""

from pydantic import BaseModel, Field
from ..browser.client import get_browser


class ContractsParams(BaseModel):
    """Parameters for getting contracts."""
    status: str = Field(
        default="active",
        description="Filter by status: active, ended, or all"
    )
    limit: int = Field(default=20, ge=1, le=50, description="Maximum number of results")


async def get_contracts(params: ContractsParams | None = None) -> list[dict]:
    """Get your Upwork contracts.

    Returns a list of contracts with client name, job title, status, and earnings.
    """
    if params is None:
        params = ContractsParams()

    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Navigate to contracts page
    url = "https://www.upwork.com/nx/wm/contracts"
    if params.status == "active":
        url += "?status=active"
    elif params.status == "ended":
        url += "?status=closed"

    await page.goto(url, wait_until="domcontentloaded")

    contracts = []

    # Wait for contracts list
    try:
        await page.wait_for_selector('[data-test="contract-tile"], .contract-row, table tbody tr', timeout=10000)
    except Exception:
        pass

    # Extract contract items
    contract_els = await page.query_selector_all('[data-test="contract-tile"], .contract-row, table tbody tr')

    for el in contract_els[:params.limit]:
        try:
            contract = await _extract_contract(el)
            if contract:
                contracts.append(contract)
        except Exception:
            continue

    return contracts


async def _extract_contract(el) -> dict | None:
    """Extract contract data from element."""
    contract = {}

    # Job title
    title_el = await el.query_selector('[data-test="job-title"], .job-title, a')
    if title_el:
        contract["title"] = (await title_el.text_content() or "").strip()
        href = await title_el.get_attribute("href")
        if href:
            contract["url"] = href if href.startswith("http") else f"https://www.upwork.com{href}"

    if not contract.get("title"):
        return None

    # Client name
    client_el = await el.query_selector('[data-test="client-name"], .client-name')
    if client_el:
        contract["client_name"] = (await client_el.text_content() or "").strip()

    # Status
    status_el = await el.query_selector('[data-test="contract-status"], .status-badge')
    if status_el:
        contract["status"] = (await status_el.text_content() or "").strip()

    # Contract type (hourly/fixed)
    type_el = await el.query_selector('[data-test="contract-type"], .contract-type')
    if type_el:
        contract["type"] = (await type_el.text_content() or "").strip()

    # Rate/Budget
    rate_el = await el.query_selector('[data-test="hourly-rate"], .rate, [data-test="budget"]')
    if rate_el:
        contract["rate"] = (await rate_el.text_content() or "").strip()

    # Total earned
    earned_el = await el.query_selector('[data-test="total-earned"], .earnings')
    if earned_el:
        contract["total_earned"] = (await earned_el.text_content() or "").strip()

    # Start date
    start_el = await el.query_selector('[data-test="start-date"], .start-date')
    if start_el:
        contract["start_date"] = (await start_el.text_content() or "").strip()

    # End date (for ended contracts)
    end_el = await el.query_selector('[data-test="end-date"], .end-date')
    if end_el:
        contract["end_date"] = (await end_el.text_content() or "").strip()

    return contract


async def get_contract_details(contract_url: str) -> dict:
    """Get detailed information about a specific contract.

    Args:
        contract_url: URL to the contract

    Returns full contract details including milestones, hours logged, and feedback.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    await page.goto(contract_url, wait_until="domcontentloaded")

    details = {"url": contract_url}

    # Job title
    title_el = await page.query_selector('[data-test="job-title"], h1, .job-title')
    if title_el:
        details["title"] = (await title_el.text_content() or "").strip()

    # Client name
    client_el = await page.query_selector('[data-test="client-name"], .client-name')
    if client_el:
        details["client_name"] = (await client_el.text_content() or "").strip()

    # Contract status
    status_el = await page.query_selector('[data-test="contract-status"], .status')
    if status_el:
        details["status"] = (await status_el.text_content() or "").strip()

    # Contract type
    type_el = await page.query_selector('[data-test="contract-type"], .type')
    if type_el:
        details["type"] = (await type_el.text_content() or "").strip()

    # Rate
    rate_el = await page.query_selector('[data-test="rate"], .hourly-rate')
    if rate_el:
        details["rate"] = (await rate_el.text_content() or "").strip()

    # Weekly limit (for hourly)
    limit_el = await page.query_selector('[data-test="weekly-limit"], .weekly-limit')
    if limit_el:
        details["weekly_limit"] = (await limit_el.text_content() or "").strip()

    # Total earned
    earned_el = await page.query_selector('[data-test="total-earned"], .total-earned')
    if earned_el:
        details["total_earned"] = (await earned_el.text_content() or "").strip()

    # Hours this week (for hourly)
    hours_el = await page.query_selector('[data-test="hours-this-week"], .hours-week')
    if hours_el:
        details["hours_this_week"] = (await hours_el.text_content() or "").strip()

    # Total hours
    total_hours_el = await page.query_selector('[data-test="total-hours"], .total-hours')
    if total_hours_el:
        details["total_hours"] = (await total_hours_el.text_content() or "").strip()

    # Milestones (for fixed-price)
    milestones = []
    milestone_els = await page.query_selector_all('[data-test="milestone"], .milestone-item')
    for el in milestone_els:
        ms = {}
        ms_title = await el.query_selector('.milestone-title, [data-test="title"]')
        if ms_title:
            ms["title"] = (await ms_title.text_content() or "").strip()
        ms_amount = await el.query_selector('.amount, [data-test="amount"]')
        if ms_amount:
            ms["amount"] = (await ms_amount.text_content() or "").strip()
        ms_status = await el.query_selector('.status, [data-test="status"]')
        if ms_status:
            ms["status"] = (await ms_status.text_content() or "").strip()
        if ms.get("title"):
            milestones.append(ms)
    if milestones:
        details["milestones"] = milestones

    # Feedback (if ended)
    feedback = {}
    feedback_el = await page.query_selector('[data-test="feedback-section"], .feedback')
    if feedback_el:
        rating_el = await feedback_el.query_selector('[data-test="rating"], .rating')
        if rating_el:
            feedback["rating"] = (await rating_el.text_content() or "").strip()
        comment_el = await feedback_el.query_selector('[data-test="comment"], .comment')
        if comment_el:
            feedback["comment"] = (await comment_el.text_content() or "").strip()
    if feedback:
        details["feedback"] = feedback

    # Start/end dates
    start_el = await page.query_selector('[data-test="start-date"], .start-date')
    if start_el:
        details["start_date"] = (await start_el.text_content() or "").strip()

    end_el = await page.query_selector('[data-test="end-date"], .end-date')
    if end_el:
        details["end_date"] = (await end_el.text_content() or "").strip()

    return details


async def get_work_diary(contract_url: str, week_offset: int = 0) -> dict:
    """Get work diary entries for a contract.

    Args:
        contract_url: URL to the contract
        week_offset: 0 for current week, 1 for last week, etc.

    Returns work diary with daily hours and screenshots.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Navigate to work diary
    # The exact URL structure may vary
    diary_url = contract_url.replace("/contracts/", "/work-diary/")
    await page.goto(diary_url, wait_until="domcontentloaded")

    diary = {"contract_url": contract_url, "days": []}

    # Extract daily entries
    day_els = await page.query_selector_all('[data-test="day-entry"], .day-row')

    for el in day_els:
        day = {}

        date_el = await el.query_selector('[data-test="date"], .date')
        if date_el:
            day["date"] = (await date_el.text_content() or "").strip()

        hours_el = await el.query_selector('[data-test="hours"], .hours')
        if hours_el:
            day["hours"] = (await hours_el.text_content() or "").strip()

        earnings_el = await el.query_selector('[data-test="earnings"], .earnings')
        if earnings_el:
            day["earnings"] = (await earnings_el.text_content() or "").strip()

        if day.get("date"):
            diary["days"].append(day)

    # Weekly totals
    total_hours_el = await page.query_selector('[data-test="weekly-hours"], .week-total-hours')
    if total_hours_el:
        diary["weekly_hours"] = (await total_hours_el.text_content() or "").strip()

    total_earnings_el = await page.query_selector('[data-test="weekly-earnings"], .week-total-earnings')
    if total_earnings_el:
        diary["weekly_earnings"] = (await total_earnings_el.text_content() or "").strip()

    return diary
