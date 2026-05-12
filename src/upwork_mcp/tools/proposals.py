"""Proposal tools for Upwork MCP."""

import asyncio
import re

from pydantic import BaseModel, Field

from ..browser.client import get_browser


class ProposalsParams(BaseModel):
    """Parameters for listing proposals."""

    tab: str = Field(
        default="active",
        description=(
            "Which tab on /nx/proposals/ to scrape. One of "
            "'active' (default — submitted proposals + active proposals + "
            "invitations + offers), 'archived', or 'referrals'."
        ),
    )
    limit: int = Field(
        default=20, ge=1, le=100, description="Maximum number of rows per section."
    )


async def get_proposals(params: ProposalsParams) -> dict:
    """Get your proposals from /nx/proposals/.

    Returns a dict shaped as:
        {
            "tab": "active",
            "sections": {
                "offers": {"count": 0, "items": [...]},
                "invites_from_clients": {"count": 0, "items": [...]},
                "active_proposals": {"count": 0, "items": [...]},
                "submitted_proposals": {"count": 1, "items": [
                    {"job_title": "...", "url": ".../nx/proposals/<id>",
                     "initiated": "May 11, 2026", "initiated_relative": "yesterday",
                     "profile": "General Profile"}
                ]},
            }
        }

    Empty sections come back with count=0 and items=[].
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    await page.goto("https://www.upwork.com/nx/proposals/", wait_until="domcontentloaded")

    # Switch tabs if requested. The page defaults to "Active".
    tab = (params.tab or "active").lower()
    if tab in {"archived", "referrals"}:
        try:
            tab_btn = await page.query_selector(f'button.air3-tab-btn[data-ev-tab="{tab}"]')
            if tab_btn:
                await tab_btn.click()
                await asyncio.sleep(1)
        except Exception:
            pass

    # The page renders four section cards by data-qa. We wait for any of them
    # to appear before scraping.
    try:
        await page.wait_for_selector(
            '[data-qa="card-offers"], [data-qa="card-invitations"], '
            '[data-qa="card-active-proposals"], [data-qa="card-submitted-proposals"]',
            timeout=15000,
        )
    except Exception:
        await asyncio.sleep(2)

    sections: dict = {}
    section_specs = [
        ("offers", '[data-qa="card-offers"]'),
        ("invites_from_clients", '[data-qa="card-invitations"]'),
        ("active_proposals", '[data-qa="card-active-proposals"]'),
        ("submitted_proposals", '[data-qa="card-submitted-proposals"]'),
    ]
    for key, sel in section_specs:
        card = await page.query_selector(sel)
        sections[key] = await _extract_section(card, limit=params.limit)

    return {"tab": tab, "sections": sections}


async def _text_long_form(el) -> str:
    """Read an element's text, preferring the `d-lg-inline` long-form span
    when Upwork uses paired responsive twin spans (long form for desktop,
    short for mobile) — both are in the DOM and a naive `text_content()`
    would concatenate them into "Project length Duration" / "Less than 1
    month < 1 month".
    """
    if el is None:
        return ""
    long_span = await el.query_selector("span.d-none.d-lg-inline")
    if long_span:
        return re.sub(r"\s+", " ", (await long_span.text_content() or "").strip())
    return re.sub(r"\s+", " ", (await el.text_content() or "").strip())


async def _extract_section(card, limit: int) -> dict:
    """Pull `{count, items}` out of one /nx/proposals/ card."""
    if not card:
        return {"count": 0, "items": []}

    # Count — Upwork puts it in `<span data-qa="count">(N)</span>`.
    count = None
    count_el = await card.query_selector('[data-qa="count"]')
    if count_el:
        count_text = re.sub(r"\D", "", (await count_el.text_content() or ""))
        if count_text:
            count = int(count_text)
    # Some headers ("Invites from clients") inline the count in the h2 text,
    # e.g. "Invites from clients (0)". Fall back to that if needed.
    if count is None:
        header = await card.query_selector("h2")
        if header:
            text = (await header.text_content() or "").strip()
            m = re.search(r"\((\d+)\)", text)
            if m:
                count = int(m.group(1))
    if count is None:
        count = 0

    items: list[dict] = []
    # Submitted / active / offers / invites all use the same row pattern:
    # <tr class="details-row" data-qa="item0"> ... </tr>
    rows = await card.query_selector_all("tr.details-row, tr[data-qa^='item']")
    for row in rows[:limit]:
        try:
            item = await _extract_proposal_row(row)
            if item:
                items.append(item)
        except Exception:
            continue
    return {"count": count, "items": items}


async def _extract_proposal_row(row) -> dict | None:
    """Pull the fields out of one `<tr>` row on /nx/proposals/."""
    item: dict = {}

    # Job title + URL — the canonical link to /nx/proposals/<id>.
    job_link = await row.query_selector('td[data-cy="job-info"] a')
    if job_link:
        href = await job_link.get_attribute("href")
        title = re.sub(r"\s+", " ", (await job_link.text_content() or "").strip())
        # aria-label sometimes holds the unmangled title.
        aria = await job_link.get_attribute("aria-label")
        if aria:
            title = aria.strip() or title
        if title:
            item["job_title"] = title
        if href:
            item["url"] = f"https://www.upwork.com{href}" if href.startswith("/") else href

    # Initiated date + relative ("yesterday")
    time_cell = await row.query_selector('td[data-cy="time-slot"]')
    if time_cell:
        full = re.sub(r"\s+", " ", (await time_cell.text_content() or "").strip())
        # "Initiated May 11, 2026 yesterday" — pull the date and the trailing
        # relative phrase separately.
        date_match = re.search(
            r"(?:Initiated|Sent|Submitted)\s+([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})",
            full,
        )
        if date_match:
            item["initiated"] = date_match.group(1)
        rel_match = re.search(
            r"\b(yesterday|today|\d+\s+(?:minute|hour|day|week|month|year)s?\s+ago)\b",
            full,
            re.I,
        )
        if rel_match:
            item["initiated_relative"] = rel_match.group(1)

    # Profile used (General Profile / Specialized / Agency name)
    profile_cell = await row.query_selector('td[data-cy="default-slot"]')
    if profile_cell:
        text = re.sub(r"\s+", " ", (await profile_cell.text_content() or "").strip())
        if text:
            item["profile"] = text

    # Status (only present on submitted/archived rows when the client has acted)
    reason_cell = await row.query_selector('td[data-qa="reason-slot"]')
    if reason_cell:
        text = re.sub(r"\s+", " ", (await reason_cell.text_content() or "").strip())
        if text:
            item["status_note"] = text

    return item if item else None


async def get_proposal_details(proposal_url: str) -> dict:
    """Get details of a single submitted proposal.

    Accepts a full `https://www.upwork.com/nx/proposals/<id>` URL or just
    the numeric `<id>`. Returns the structured proposal data scraped from
    the proposal-details page.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    url = (proposal_url or "").strip()
    if not url.startswith("http"):
        if url.isdigit():
            url = f"https://www.upwork.com/nx/proposals/{url}"
        elif url.startswith("/"):
            url = f"https://www.upwork.com{url}"
        else:
            url = f"https://www.upwork.com/nx/proposals/{url}"

    await page.goto(url, wait_until="domcontentloaded")
    try:
        await page.wait_for_selector('[data-test="proposal-details"]', timeout=15000)
    except Exception:
        await asyncio.sleep(3)

    result: dict = {"url": url}

    # --- Original job posting ----------------------------------------------
    original_link = await page.query_selector('[data-test="open-original-posting"]')
    if original_link:
        href = await original_link.get_attribute("href")
        if href:
            result["job_url"] = f"https://www.upwork.com{href}" if href.startswith("/") else href

    # Job details block (inside .fe-job-details)
    job_card = await page.query_selector(".fe-job-details")
    if job_card:
        job: dict = {}
        title_el = await job_card.query_selector("h3.h5, h3")
        if title_el:
            title = re.sub(r"\s+", " ", (await title_el.text_content() or "").strip())
            if title:
                job["title"] = title
        # Category — first .air3-token inside the header list
        cat_el = await job_card.query_selector("ul.list-inline .air3-token")
        if cat_el:
            cat = re.sub(r"\s+", " ", (await cat_el.text_content() or "").strip())
            if cat:
                job["category"] = cat
        # Posted date
        posted_el = await job_card.query_selector('span[itemprop="datePosted"]')
        if posted_el:
            posted = re.sub(r"\s+", " ", (await posted_el.text_content() or "").strip())
            if posted:
                job["posted"] = posted
        # Description (full text, even when truncated visually). Read the
        # inner `<span id="air3-truncation-2">` if present — that's the
        # actual body text without the "more" button / sr-only labels.
        desc_text = ""
        truncation_inner = await job_card.query_selector(
            '.description .air3-truncation [id^="air3-truncation-"]'
        )
        if truncation_inner:
            desc_text = (await truncation_inner.text_content() or "").strip()
        else:
            desc_el = await job_card.query_selector(".description")
            if desc_el:
                desc_text = (await desc_el.text_content() or "").strip()
        desc_text = re.sub(r"\s+", " ", desc_text)
        # Drop trailing " more More/Less about" toggle text that leaks in.
        desc_text = re.sub(
            r"\s*more\s*(More/Less about\s*)?$", "", desc_text, flags=re.I
        ).strip()
        if desc_text:
            job["description"] = desc_text

        # Sidebar features (experience, hourly/fixed, duration). Each <li>
        # has a `.header` block with a `<strong>` value and a `<small>`
        # label. Inside those, Upwork uses responsive twin spans
        # (`.d-none.d-lg-inline` long form + `.d-lg-none` short form);
        # both are in the DOM so `text_content()` concatenates them
        # ("Project length Duration"). We prefer the long form when
        # available.
        features: list[dict] = []
        for li in await job_card.query_selector_all(".fe-ui-job-features > li"):
            strong = await li.query_selector("strong")
            small = await li.query_selector("small")
            if not strong or not small:
                continue
            value = await _text_long_form(strong)
            label = await _text_long_form(small)
            if value and label:
                features.append({"label": label, "value": value})
        if features:
            job["features"] = features

        # Skills inside the job-details card
        skills: list[str] = []
        for el in await job_card.query_selector_all("ul.list-inline li[data-qa-skill-key] .air3-token"):
            text = re.sub(r"\s+", " ", (await el.text_content() or "").strip())
            if text and text not in skills:
                skills.append(text)
        if skills:
            job["skills"] = skills

        if job:
            result["job"] = job

    # --- Your proposed terms -----------------------------------------------
    terms_block = await page.query_selector('[data-test="terms-review-hourly"]')
    if terms_block:
        terms: dict = {"type": "hourly"}
        # Each row is shaped:
        #   <strong>Hourly rate</strong>
        #   <div class="mb-3x text-body text-light"><span>description</span></div>
        #   <div class="text-body">$40.00/hr</div>
        # The first `div.text-body` is the muted description; the SECOND
        # (without the `text-light` modifier) is the actual amount. We grab
        # only the latter.
        for block in await terms_block.query_selector_all(":scope > div"):
            label_el = await block.query_selector("strong")
            if not label_el:
                continue
            label = re.sub(
                r"\s+", " ", (await label_el.text_content() or "").strip()
            ).lower()
            # Pick the non-muted `div.text-body` — that's the rate value.
            value = None
            for vd in await block.query_selector_all("div.text-body"):
                cls = (await vd.get_attribute("class")) or ""
                if "text-light" in cls:
                    continue
                t = re.sub(r"\s+", " ", (await vd.text_content() or "").strip())
                if "$" in t:
                    value = t
                    break
            if value is None:
                continue
            if "hourly rate" in label:
                terms["hourly_rate"] = value
            elif "you'll receive" in label or "youll receive" in label or "receive" in label:
                terms["you_receive"] = value
        result["proposed_terms"] = terms
    else:
        # Try generic terms-review block (e.g. fixed-price).
        fixed_block = await page.query_selector('[data-test="terms-review"]')
        if fixed_block:
            terms = {"type": "fixed"}
            full_text = re.sub(
                r"\s+", " ", (await fixed_block.text_content() or "").strip()
            )
            bid_match = re.search(r"Bid[^$]*?(\$[\d.,]+)", full_text)
            if bid_match:
                terms["bid"] = bid_match.group(1)
            receive_match = re.search(r"receive[^$]*?(\$[\d.,]+)", full_text, re.I)
            if receive_match:
                terms["you_receive"] = receive_match.group(1)
            result["proposed_terms"] = terms

    # Rate-increase (SRI) — sits inside .sri-review under the terms block.
    sri = await page.query_selector(".sri-review .rate")
    if sri:
        sri_text = re.sub(r"\s+", " ", (await sri.text_content() or "").strip())
        if sri_text:
            result.setdefault("proposed_terms", {})["rate_increase"] = sri_text

    # --- Cover letter ------------------------------------------------------
    cover_el = await page.query_selector('[data-cy="cover-letter-section"] p.break.text-pre-line')
    if cover_el:
        # `text_content` preserves visible whitespace; we keep it as-is so the
        # user sees the same cover letter they submitted.
        cover = (await cover_el.text_content() or "").strip()
        if cover:
            result["cover_letter"] = cover

    # --- Profile highlights ------------------------------------------------
    highlights: list[dict] = []
    for li in await page.query_selector_all(
        '[data-test="highlights-list"] [data-test="highlights-item"]'
    ):
        item: dict = {}
        kind_el = await li.query_selector(".secondary-text")
        if kind_el:
            kind = re.sub(r"\s+", " ", (await kind_el.text_content() or "").strip())
            if kind:
                item["kind"] = kind
        title_el = await li.query_selector(".item-title")
        if title_el:
            title = re.sub(r"\s+", " ", (await title_el.text_content() or "").strip())
            if title:
                item["title"] = title
        skill_el = await li.query_selector_all(".secondary-text")
        if len(skill_el) > 1:
            sk = re.sub(r"\s+", " ", (await skill_el[1].text_content() or "").strip())
            if sk:
                item["skills_text"] = sk
        img = await li.query_selector("img")
        if img:
            src = await img.get_attribute("src")
            if src:
                item["image"] = f"https://www.upwork.com{src}" if src.startswith("/") else src
        if item:
            highlights.append(item)
    if highlights:
        result["profile_highlights"] = highlights

    # --- About the client (re-used from job details page) ------------------
    client_el = await page.query_selector('[data-test="about-client-container"]')
    if client_el:
        client: dict = {}
        # Location
        loc_el = await client_el.query_selector('[data-qa="client-location"] strong')
        if loc_el:
            client["country"] = re.sub(r"\s+", " ", (await loc_el.text_content() or "").strip())
        stats_el = await client_el.query_selector('[data-qa="client-job-posting-stats"]')
        if stats_el:
            stats_text = re.sub(r"\s+", " ", (await stats_el.text_content() or "").strip())
            if stats_text:
                client["job_posting_stats"] = stats_text
        spend_el = await client_el.query_selector('[data-qa="client-spend"]')
        if spend_el:
            spend = re.sub(r"\s+", " ", (await spend_el.text_content() or "").strip())
            if spend:
                client["total_spent"] = spend
        contract_el = await client_el.query_selector('[data-qa="client-contract-date"]')
        if contract_el:
            txt = re.sub(r"\s+", " ", (await contract_el.text_content() or "").strip())
            client["member_since"] = txt.replace("Member since", "").strip()
        if client:
            result["client"] = client

    # Status hint — if the "Edit proposal" action is present the proposal is
    # still live; otherwise it's been responded to / withdrawn / archived.
    edit_btn = await page.query_selector('[data-test="edit-proposal"]')
    result["is_editable"] = edit_btn is not None

    return result


async def withdraw_proposal(proposal_url: str) -> dict:
    """Withdraw a submitted proposal.

    NOTE: This is a sensitive action. We DO NOT expose it from the MCP layer
    automatically — it is a remaining stub the user can wire up manually
    after verifying the UI flow. For now it just returns an error.
    """
    return {
        "status": "not_implemented",
        "message": (
            "withdraw_proposal is not implemented. Withdraw proposals "
            "manually from the Upwork UI."
        ),
        "url": proposal_url,
    }
