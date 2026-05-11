"""Profile and connects tools for Upwork MCP."""

from ..browser.client import get_browser


async def get_my_profile() -> dict:
    """Get your Upwork freelancer profile information.

    Returns profile data including name, title, hourly rate, JSS score,
    availability status, and skill tags.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    await page.goto("https://www.upwork.com/freelancers/settings/profile", wait_until="domcontentloaded")

    profile = {}

    # Name
    name_el = await page.query_selector('[data-test="profile-name"], h1, .profile-name')
    if name_el:
        profile["name"] = (await name_el.text_content() or "").strip()

    # Professional title
    title_el = await page.query_selector('[data-test="profile-title"], .profile-title, [data-cy="title"]')
    if title_el:
        profile["title"] = (await title_el.text_content() or "").strip()

    # Hourly rate
    rate_el = await page.query_selector('[data-test="hourly-rate"], .hourly-rate, [data-cy="rate"]')
    if rate_el:
        profile["hourly_rate"] = (await rate_el.text_content() or "").strip()

    # Profile overview/bio
    overview_el = await page.query_selector('[data-test="profile-overview"], .profile-overview, [data-cy="overview"]')
    if overview_el:
        profile["overview"] = (await overview_el.text_content() or "").strip()

    # Skills
    skill_els = await page.query_selector_all('[data-test="skill"], .skill-badge, .air3-token')
    profile["skills"] = []
    for el in skill_els:
        text = await el.text_content()
        if text:
            profile["skills"].append(text.strip())

    # Now get stats from a different page
    await page.goto("https://www.upwork.com/nx/find-work/best-matches", wait_until="domcontentloaded")

    # Try to get JSS from sidebar or header
    jss_el = await page.query_selector('[data-test="jss"], .jss-score, [data-cy="jss"]')
    if jss_el:
        profile["job_success_score"] = (await jss_el.text_content() or "").strip()

    # Availability badge
    avail_el = await page.query_selector('[data-test="availability"], .availability-badge')
    if avail_el:
        profile["availability"] = (await avail_el.text_content() or "").strip()

    # Profile completeness
    complete_el = await page.query_selector('[data-test="profile-completeness"], .profile-complete')
    if complete_el:
        profile["profile_completeness"] = (await complete_el.text_content() or "").strip()

    # Get connects balance
    connects = await get_connects_balance()
    profile["connects"] = connects

    return profile


async def get_connects_balance() -> dict:
    """Get current Upwork Connects balance and usage.

    Returns the number of available connects, pending connects,
    and connects balance details.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Navigate to connects page
    await page.goto("https://www.upwork.com/nx/plans/connects/balance", wait_until="domcontentloaded")

    connects = {}

    # Available connects
    available_el = await page.query_selector('[data-test="connects-available"], .connects-balance, [data-cy="available-connects"]')
    if available_el:
        text = (await available_el.text_content() or "").strip()
        # Extract number
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            connects["available"] = int(numbers[0])

    # If we couldn't find it, try the header/sidebar on main page
    if "available" not in connects:
        await page.goto("https://www.upwork.com/nx/find-work/", wait_until="domcontentloaded")
        connects_el = await page.query_selector('[data-test="connects-count"], .connects-count')
        if connects_el:
            text = (await connects_el.text_content() or "").strip()
            import re
            numbers = re.findall(r'\d+', text)
            if numbers:
                connects["available"] = int(numbers[0])

    # Try to get additional connects info
    pending_el = await page.query_selector('[data-test="pending-connects"]')
    if pending_el:
        text = (await pending_el.text_content() or "").strip()
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            connects["pending"] = int(numbers[0])

    return connects


async def get_profile_stats() -> dict:
    """Get profile statistics including earnings and work history.

    Returns stats like total earnings, hours worked, jobs completed.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Navigate to work diary or stats page
    await page.goto("https://www.upwork.com/nx/wm/contracts", wait_until="domcontentloaded")

    stats = {}

    # Total earnings
    earnings_el = await page.query_selector('[data-test="total-earnings"], .earnings-total')
    if earnings_el:
        stats["total_earnings"] = (await earnings_el.text_content() or "").strip()

    # Active contracts count
    active_el = await page.query_selector('[data-test="active-contracts"], .active-count')
    if active_el:
        stats["active_contracts"] = (await active_el.text_content() or "").strip()

    # Total hours
    hours_el = await page.query_selector('[data-test="total-hours"], .hours-total')
    if hours_el:
        stats["total_hours"] = (await hours_el.text_content() or "").strip()

    # Jobs completed
    jobs_el = await page.query_selector('[data-test="jobs-completed"], .jobs-count')
    if jobs_el:
        stats["jobs_completed"] = (await jobs_el.text_content() or "").strip()

    return stats
