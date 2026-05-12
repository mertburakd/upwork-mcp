"""Profile and connects tools for Upwork MCP."""

import asyncio
import re

from ..browser.client import get_browser


async def get_my_profile() -> dict:
    """Fetch your Upwork freelancer profile.

    Navigates to your *public* profile page (`/freelancers/~01...`) — not
    the editor settings page — because that's where Upwork renders the
    structured identity block we can scrape reliably. We resolve the
    public-profile URL by first hitting `/freelancers/settings/profile`
    and following the avatar link in the global navbar.

    Returns whichever of these fields are present in the rendered DOM:
      name, professional_title, hourly_rate, city, country, connects,
      page_title.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Step 1 — load the settings page so we can grab the avatar's link to
    # the public profile. The settings page itself doesn't render the
    # structured identity block.
    await page.goto(
        "https://www.upwork.com/freelancers/settings/profile",
        wait_until="commit",
    )
    try:
        await page.wait_for_selector('a[href^="/freelancers/~"]', timeout=20000)
    except Exception:
        await asyncio.sleep(2)

    public_href = None
    for a in await page.query_selector_all('a[href^="/freelancers/~"]'):
        href = await a.get_attribute("href")
        if not href:
            continue
        # Skip portfolio modal links (those carry `?p=...`) and settings links.
        if "settings" in href or "?p=" in href:
            continue
        public_href = href.split("?")[0]
        break

    if public_href:
        public_url = f"https://www.upwork.com{public_href}"
        await page.goto(public_url, wait_until="commit")
        try:
            await page.wait_for_selector('h2[itemprop="name"]', timeout=25000)
        except Exception:
            await asyncio.sleep(3)
    # If we couldn't find a public-profile link, fall through and try to
    # extract whatever we can from the settings page DOM.

    profile: dict = {}

    # Document title — on the public profile page, this is the headline
    # ("Mert Burak D. - Senior Full Stack Developer | … - Upwork Freelancer
    # from Aydin, Turkey").
    doc_title = (await page.title() or "").strip()
    if doc_title:
        profile["page_title"] = doc_title
    if public_href:
        profile["profile_url"] = f"https://www.upwork.com{public_href}"

    # Name (schema.org itemprop)
    name_el = await page.query_selector('h2[itemprop="name"]')
    if name_el:
        name = re.sub(r"\s+", " ", (await name_el.text_content() or "").strip())
        if name:
            profile["name"] = name

    # Location
    city_el = await page.query_selector('span[itemprop="locality"]')
    if city_el:
        profile["city"] = re.sub(r"\s+", " ", (await city_el.text_content() or "").strip())
    country_el = await page.query_selector('span[itemprop="country-name"]')
    if country_el:
        profile["country"] = re.sub(r"\s+", " ", (await country_el.text_content() or "").strip())

    # Professional title — sits in the same identity card as the hourly
    # rate. The first `h3.h4` inside the identity card is the title.
    title_el = await page.query_selector(".identity-content h3.h4, h3.h4")
    if title_el:
        title = re.sub(r"\s+", " ", (await title_el.text_content() or "").strip())
        # Strip the "Edit title" sr-only fragment if the button leaked into
        # text_content.
        title = re.sub(r"\s*Edit title$", "", title, flags=re.I)
        if title:
            profile["professional_title"] = title

    # Hourly rate — search the whole document for the first `$N/hr` token.
    # The profile page has at most one rate; ranges live on job tiles.
    body_text = await page.content()
    rate_match = re.search(r"\$\d+(?:\.\d{1,2})?/hr", body_text)
    if rate_match:
        profile["hourly_rate"] = rate_match.group(0)

    # Connects from the sidebar widget — saves a separate page load.
    connects_el = await page.query_selector(
        '[data-test="sidebar-connects-card"] h3, [data-test="sidebar-connects-card"] h5'
    )
    if connects_el:
        connects_text = re.sub(r"\s+", " ", (await connects_el.text_content() or "").strip())
        if connects_text:
            profile["connects_label"] = connects_text  # "Connects: 48"
            m = re.search(r"(\d+)", connects_text)
            if m:
                profile["connects"] = int(m.group(1))

    return profile


async def get_connects_balance() -> dict:
    """Get current Upwork Connects balance.

    Returns a dict with the integer balance under `available` and the raw
    label text under `available_label` (e.g. "48 Connects").
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Connects page. The old `/balance` endpoint 404s; the live one is
    # `/history/`. The page is heavy and `domcontentloaded` regularly
    # doesn't fire within 30s on a CDP-attached tab — use `commit` and
    # wait for the balance card explicitly.
    await page.goto(
        "https://www.upwork.com/nx/plans/connects/history/",
        wait_until="commit",
    )

    try:
        await page.wait_for_selector(".connects-history h2.h3", timeout=25000)
    except Exception:
        await asyncio.sleep(2)

    connects: dict = {}

    balance_el = await page.query_selector(".connects-history h2.h3")
    if balance_el:
        text = re.sub(r"\s+", " ", (await balance_el.text_content() or "").strip())
        if text:
            connects["available_label"] = text  # "48 Connects"
            m = re.search(r"(\d+)", text)
            if m:
                connects["available"] = int(m.group(1))

    # "Buy Connects" button is a sanity check that we're on the right page.
    if not connects:
        # Page changed shape — surface the page title so the caller can debug.
        connects["error"] = f"Balance not found on page (title: {await page.title()!r})"

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
