"""Job search and details tools for Upwork MCP."""

import re
import asyncio
import urllib.parse
from pydantic import BaseModel, Field
from ..browser.client import get_browser


class JobSearchParams(BaseModel):
    """Parameters for job search."""
    query: str = Field(description="Search keywords")
    experience_level: str | None = Field(
        default=None,
        description="Experience level: entry, intermediate, or expert"
    )
    job_type: str | None = Field(
        default=None,
        description="Job type: hourly or fixed"
    )
    limit: int = Field(default=10, ge=1, le=50, description="Maximum number of results")


class JobDetailsParams(BaseModel):
    """Parameters for getting job details."""
    job_url: str = Field(description="Full Upwork job URL or job ID")


async def search_jobs(params: JobSearchParams) -> list[dict]:
    """Search for jobs on Upwork matching the specified criteria.

    Returns a list of job summaries with title, budget, and URL.
    """
    browser = get_browser()
    page = await browser.get_page()

    # Build search URL
    base_url = "https://www.upwork.com/nx/find-work/best-matches"
    query_params = {"q": params.query}

    if params.job_type:
        query_params["t"] = "0" if params.job_type.lower() == "hourly" else "1"

    if params.experience_level:
        level_map = {"entry": "1", "intermediate": "2", "expert": "3"}
        level = level_map.get(params.experience_level.lower())
        if level:
            query_params["contractor_tier"] = level

    url = f"{base_url}?{urllib.parse.urlencode(query_params)}"
    await page.goto(url, wait_until="domcontentloaded")
    await asyncio.sleep(3)

    jobs = []

    # Get job sections (each section contains one job)
    sections = await page.query_selector_all("section")

    for section in sections[:params.limit * 2]:  # Check more sections
        try:
            job = {}

            # Get title from h3 or h4 link
            title_link = await section.query_selector("h3 a, h4 a")
            if not title_link:
                continue

            title = await title_link.text_content()
            href = await title_link.get_attribute("href")

            if not title or not href or "/jobs/" not in href:
                continue

            job["title"] = title.strip()
            job["url"] = f"https://www.upwork.com{href}" if href.startswith("/") else href

            # Get description snippet
            desc_el = await section.query_selector("p, [data-test='job-description-text']")
            if desc_el:
                desc = await desc_el.text_content()
                if desc:
                    job["description"] = desc.strip()[:300]

            # Get budget/rate info
            for sel in ["strong", "span"]:
                els = await section.query_selector_all(sel)
                for el in els:
                    text = await el.text_content()
                    if text and ("$" in text or "hourly" in text.lower() or "fixed" in text.lower()):
                        job["budget"] = text.strip()
                        break
                if "budget" in job:
                    break

            # Get skills
            skill_els = await section.query_selector_all("button, [class*='skill'], [class*='token']")
            skills = []
            for el in skill_els[:8]:
                text = await el.text_content()
                if text and len(text.strip()) > 1 and len(text.strip()) < 30:
                    skills.append(text.strip())
            if skills:
                job["skills"] = skills

            # Get posted time
            time_els = await section.query_selector_all("span, small")
            for el in time_els:
                text = await el.text_content()
                if text and ("ago" in text.lower() or "posted" in text.lower()):
                    job["posted"] = text.strip()
                    break

            jobs.append(job)

            if len(jobs) >= params.limit:
                break

        except Exception:
            continue

    return jobs


async def get_job_details(params: JobDetailsParams) -> dict:
    """Get detailed information about a specific Upwork job posting.

    Returns comprehensive job details including description, client history,
    skills required, and application requirements.
    """
    browser = get_browser()
    page = await browser.get_page()

    # Normalize URL
    url = params.job_url
    if not url.startswith("http"):
        url = f"https://www.upwork.com/jobs/{url}"

    await page.goto(url, wait_until="domcontentloaded")

    # Wait for the main job-detail container to actually render — Upwork
    # populates the SSR'd shell asynchronously. Fall back to a short sleep
    # if the selector never appears so we still return partial data.
    try:
        await page.wait_for_selector(
            '[data-test="Description"], [data-test="about-client-container"]',
            timeout=15000,
        )
    except Exception:
        await asyncio.sleep(3)

    job = {"url": url}

    # Title — Upwork uses <h4> for the job title, not h1/h2. Fall back to
    # the document <title> if the h4 isn't found.
    title_el = await page.query_selector(
        'h4 .flex-1, h4 span.flex-1, h1, h2, h3'
    )
    if title_el:
        job["title"] = (await title_el.text_content() or "").strip()
    if not job.get("title"):
        page_title = (await page.title() or "").strip()
        # Strip " - Upwork" / " - Web Development" suffixes
        job["title"] = re.sub(r"\s*[-—]\s*(Upwork|Web Development).*$", "", page_title).strip()

    # Posted time
    posted_el = await page.query_selector(".posted-on-line")
    if posted_el:
        posted_text = (await posted_el.text_content() or "").strip()
        posted_text = re.sub(r"\s+", " ", posted_text)
        if posted_text:
            job["posted"] = posted_text

    # Full description — case-sensitive attribute selector matters here.
    desc_el = await page.query_selector('[data-test="Description"]')
    if desc_el:
        # The element wraps "Summary" <strong> + the body <p>. Prefer the <p>.
        body_p = await desc_el.query_selector("p")
        if body_p:
            text = (await body_p.text_content() or "").strip()
        else:
            text = (await desc_el.text_content() or "").strip()
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text)
        if text:
            job["description"] = text

    # Job features (Hourly/Fixed, weekly hours, duration, experience level,
    # project type). Each <li> has a <strong> headline + <div class="description">
    # label describing what the headline means.
    features = {}
    feature_items = await page.query_selector_all("ul.features > li, .features li")
    for li in feature_items:
        strong = await li.query_selector("strong")
        desc = await li.query_selector(".description")
        if not strong or not desc:
            continue
        value = (await strong.text_content() or "").strip()
        label = (await desc.text_content() or "").strip()
        value = re.sub(r"\s+", " ", value)
        label = re.sub(r"\s+", " ", label)
        if not value or not label:
            continue
        low = label.lower()
        if "hourly" in low or "fixed" in low:
            features["job_type"] = label  # "Hourly" / "Fixed-price"
            features["workload"] = value  # e.g. "Less than 30 hrs/week"
        elif "duration" in low:
            features["duration"] = value
        elif "experience level" in low or "experience" in low:
            features["experience_level"] = value
        elif "$" in value:
            features["budget"] = value
        else:
            # Generic feature — keep first word of label as key
            key = label.split()[0].lower()
            features[key] = value
    if features:
        job.update(features)

    # Project type (One-time / Ongoing) — separate "segmentations" list
    seg_items = await page.query_selector_all(".segmentations li")
    for li in seg_items:
        text = (await li.text_content() or "").strip()
        text = re.sub(r"\s+", " ", text)
        if "Project Type:" in text:
            job["project_type"] = text.replace("Project Type:", "").strip()

    # Skills — only real skill badges, not navigation buttons.
    skill_els = await page.query_selector_all(
        'a.air3-badge[href*="ontology_skill_uid"], a.up-skill-badge'
    )
    skills = []
    for el in skill_els:
        text = (await el.text_content() or "").strip()
        text = re.sub(r"\s+", " ", text)
        if text and text not in skills:
            skills.append(text)
    if skills:
        job["skills"] = skills

    # Activity on this job (proposals, interviewing, invites sent, etc.)
    activity = {}
    activity_items = await page.query_selector_all(".client-activity-items .ca-item")
    for li in activity_items:
        title_el2 = await li.query_selector(".title")
        value_el = await li.query_selector(".value")
        if not title_el2 or not value_el:
            continue
        k = (await title_el2.text_content() or "").strip().rstrip(":").lower()
        v = (await value_el.text_content() or "").strip()
        k = re.sub(r"\s+", "_", k)
        v = re.sub(r"\s+", " ", v)
        if k and v:
            activity[k] = v
    if activity:
        job["activity"] = activity

    # Bid range ("Bid range - High $70.00 | Avg $42.73 | Low $30.00")
    # Lives in its own section near the activity list.
    page_text_for_bid = await page.content()
    bid_match = re.search(
        r"Bid range\s*[-–—]\s*High\s*(\$[\d.,]+)\s*\|\s*Avg\s*(\$[\d.,]+)\s*\|\s*Low\s*(\$[\d.,]+)",
        page_text_for_bid,
    )
    if bid_match:
        job["bid_range"] = {
            "high": bid_match.group(1),
            "avg": bid_match.group(2),
            "low": bid_match.group(3),
        }

    # Client info — the "About the client" sidebar block.
    client_el = await page.query_selector('[data-test="about-client-container"]')
    if client_el:
        client = {}
        client_text = (await client_el.text_content() or "")

        # Payment verification status (positive or negative phrasing)
        if re.search(r"Payment method (verified|not verified)", client_text, re.I):
            client["payment_verified"] = "not verified" not in client_text.lower()
        # Phone verified
        if "phone number verified" in client_text.lower():
            client["phone_verified"] = True
        # Rating like "4.85 of N reviews" / "5.00"
        rating_match = re.search(r"\b([0-5]\.\d{1,2})\b\s*(of|out of)?\s*(\d+)?\s*(reviews?|rating)?", client_text, re.I)
        if rating_match:
            client["rating"] = rating_match.group(1)

        # Location
        loc_el = await client_el.query_selector('[data-qa="client-location"]')
        if loc_el:
            country_el = await loc_el.query_selector("strong")
            if country_el:
                client["country"] = (await country_el.text_content() or "").strip()
            city_spans = await loc_el.query_selector_all("span.nowrap")
            if city_spans:
                client["city"] = (await city_spans[0].text_content() or "").strip()

        # Job posting stats — "1 job posted" + "0% hire rate, 1 open job"
        stats_el = await client_el.query_selector('[data-qa="client-job-posting-stats"]')
        if stats_el:
            stats_text = (await stats_el.text_content() or "").strip()
            stats_text = re.sub(r"\s+", " ", stats_text)
            client["job_posting_stats"] = stats_text
            jobs_posted_match = re.search(r"(\d+)\s+job", stats_text)
            if jobs_posted_match:
                client["jobs_posted"] = int(jobs_posted_match.group(1))
            hire_match = re.search(r"(\d+)%\s*hire rate", stats_text)
            if hire_match:
                client["hire_rate_pct"] = int(hire_match.group(1))

        # Total spent — clients with history have a dedicated <strong data-qa="client-spend">.
        spend_el = await client_el.query_selector('[data-qa="client-spend"]')
        if spend_el:
            spend_text = (await spend_el.text_content() or "").strip()
            spend_text = re.sub(r"\s+", " ", spend_text)
            client["total_spent"] = spend_text  # e.g. "$14K total spent"
            spend_amt = re.search(r"\$[\d.,]+[KMB]?\+?", spend_text)
            if spend_amt:
                client["total_spent_amount"] = spend_amt.group(0)
        else:
            # Fallback: regex over the whole client text
            spent_match = re.search(r"\$[\d.,]+[KMB]?\+?\s*(spent|total spent)", client_text, re.I)
            if spent_match:
                client["total_spent"] = spent_match.group(0).strip()

        # Hires — "20 hires, 9 active" sits in a sibling <div> of client-spend.
        hires_el = await client_el.query_selector('[data-qa="client-hires"]')
        if hires_el:
            hires_text = (await hires_el.text_content() or "").strip()
            hires_text = re.sub(r"\s+", " ", hires_text)
            client["hires"] = hires_text  # raw text
            total_hires = re.search(r"(\d+)\s+hires?", hires_text, re.I)
            if total_hires:
                client["hires_total"] = int(total_hires.group(1))
            active_hires = re.search(r"(\d+)\s+active", hires_text, re.I)
            if active_hires:
                client["hires_active"] = int(active_hires.group(1))

        # Avg hourly rate paid by this client — critical signal for the freelancer
        rate_el = await client_el.query_selector('[data-qa="client-hourly-rate"]')
        if rate_el:
            rate_text = (await rate_el.text_content() or "").strip()
            rate_text = re.sub(r"\s+", " ", rate_text)
            client["avg_hourly_rate_paid"] = rate_text  # e.g. "$9.19 /hr avg hourly rate paid"
            rate_match = re.search(r"\$([\d.,]+)\s*/?\s*hr", rate_text, re.I)
            if rate_match:
                try:
                    client["avg_hourly_rate_paid_amount"] = float(rate_match.group(1).replace(",", ""))
                except ValueError:
                    pass

        # Total hours billed — "1,112 hours"
        hours_el = await client_el.query_selector('[data-qa="client-hours"]')
        if hours_el:
            hours_text = (await hours_el.text_content() or "").strip()
            hours_text = re.sub(r"\s+", " ", hours_text)
            client["total_hours_billed"] = hours_text
            hours_match = re.search(r"([\d.,]+)\s*hours", hours_text, re.I)
            if hours_match:
                try:
                    client["total_hours_billed_amount"] = int(hours_match.group(1).replace(",", ""))
                except ValueError:
                    pass

        # Member since
        contract_el = await client_el.query_selector('[data-qa="client-contract-date"]')
        if contract_el:
            text = (await contract_el.text_content() or "").strip()
            client["member_since"] = text.replace("Member since", "").strip()

        # Company / industry / size
        company_el = await client_el.query_selector('[data-qa="client-company-profile"]')
        if company_el:
            company_text = (await company_el.text_content() or "").strip()
            company_text = re.sub(r"\s+", " ", company_text)
            if company_text:
                client["company"] = company_text

        if client:
            job["client"] = client

    # Connects required ("Send a proposal for: 8 Connects")
    # Use locator with has-text so we don't grab the "Available Connects" line.
    try:
        req_loc = page.locator("text=Send a proposal for").first
        if await req_loc.count() > 0:
            req_text = await req_loc.text_content()
            if req_text:
                m = re.search(r"(\d+)\s*Connects?", req_text)
                if m:
                    job["connects_required"] = int(m.group(1))
    except Exception:
        pass

    # Available connects
    try:
        avail_loc = page.locator("text=Available Connects").first
        if await avail_loc.count() > 0:
            avail_text = await avail_loc.text_content()
            if avail_text:
                m = re.search(r"(\d+)", avail_text)
                if m:
                    job["available_connects"] = int(m.group(1))
    except Exception:
        pass

    return job
