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
    await asyncio.sleep(3)

    job = {"url": url}

    # Title
    title_el = await page.query_selector("h1, h2")
    if title_el:
        job["title"] = (await title_el.text_content() or "").strip()

    # Full description
    desc_el = await page.query_selector("[data-test='description'], .description, article p")
    if desc_el:
        job["description"] = (await desc_el.text_content() or "").strip()

    # Get all text blocks to find budget, experience, etc.
    all_text = await page.query_selector_all("p, span, div")
    for el in all_text:
        text = await el.text_content()
        if not text:
            continue
        text = text.strip()

        # Budget
        if "$" in text and len(text) < 50 and not job.get("budget"):
            job["budget"] = text

        # Experience level
        if any(x in text.lower() for x in ["entry level", "intermediate", "expert"]):
            if not job.get("experience_level"):
                job["experience_level"] = text

        # Project length
        if any(x in text.lower() for x in ["less than", "1 to 3", "3 to 6", "more than"]):
            if "month" in text.lower() and not job.get("project_length"):
                job["project_length"] = text

    # Skills
    skill_els = await page.query_selector_all("[class*='skill'], [class*='token'], button")
    skills = []
    for el in skill_els[:15]:
        text = await el.text_content()
        if text and 2 < len(text.strip()) < 30:
            skills.append(text.strip())
    if skills:
        job["skills"] = list(set(skills))[:10]

    # Client info
    client = {}
    client_section = await page.query_selector("[data-test='client-info'], [class*='client']")
    if client_section:
        client_text = await client_section.text_content()
        if client_text:
            # Look for location, rating, etc.
            if "Payment" in client_text and "verified" in client_text.lower():
                client["payment_verified"] = True
            # Extract spending info
            spent_match = re.search(r"\$[\d,]+[KMB]?\+?\s*(spent|total)", client_text, re.I)
            if spent_match:
                client["total_spent"] = spent_match.group(0)

    if client:
        job["client"] = client

    # Connects required
    connects_els = await page.query_selector_all("span, div")
    for el in connects_els:
        text = await el.text_content()
        if text and "connect" in text.lower():
            numbers = re.findall(r"\d+", text)
            if numbers:
                job["connects_required"] = int(numbers[0])
                break

    return job
