"""Job search and details tools for Upwork MCP."""

import re
import asyncio
import urllib.parse
from pydantic import BaseModel, Field
from ..browser.client import get_browser


class JobSearchParams(BaseModel):
    """Parameters for job search."""
    query: str = Field(description="Search keywords")
    category: str | None = Field(default=None, description="Job category filter")
    budget_min: int | None = Field(default=None, description="Minimum budget in USD")
    budget_max: int | None = Field(default=None, description="Maximum budget in USD")
    experience_level: list[str] | str | None = Field(
        default=None,
        description=(
            "Experience level filter. Accepts a single value or a list. "
            "Allowed: 'entry', 'intermediate', 'expert'."
        ),
    )
    job_type: str | None = Field(
        default=None,
        description="Job type: hourly or fixed (omit to include both)",
    )
    hourly_rate_min: int | None = Field(
        default=None,
        description="Minimum hourly rate in USD (only applied when job_type allows hourly).",
    )
    hourly_rate_max: int | None = Field(
        default=None,
        description="Maximum hourly rate in USD (only applied when job_type allows hourly).",
    )
    workload: list[str] | str | None = Field(
        default=None,
        description=(
            "Workload filter. Accepts a single value or list. "
            "Allowed: 'as_needed' (<30 hrs/wk), 'part_time' (<30 hrs/wk steady), "
            "'full_time' (30+ hrs/wk)."
        ),
    )
    duration: list[str] | str | None = Field(
        default=None,
        description=(
            "Project duration filter. Accepts a single value or list. "
            "Allowed: 'week' (<1 month), 'month' (1-3 months), "
            "'semester' (3-6 months), 'ongoing' (6+ months)."
        ),
    )
    location: list[str] | str | None = Field(
        default=None,
        description=(
            "Client location filter. Accepts a country/region name or list "
            "(e.g. 'United States', 'Europe', 'United Kingdom')."
        ),
    )
    proposals_max: int | None = Field(
        default=None,
        description=(
            "Maximum proposals tier on the job. Accepts 4, 9, 14, 19 or 49 "
            "(maps to Upwork's 0-4, 0-9, 0-14, 0-19, 0-49 buckets)."
        ),
    )
    payment_verified: bool | None = Field(
        default=None,
        description="If true, restrict to clients with verified payment method.",
    )
    contract_to_hire: bool | None = Field(
        default=None,
        description="If true, include 'Contract-to-hire' jobs.",
    )
    client_hires: str | None = Field(
        default=None,
        description=(
            "Filter by client's prior hire count. Pass Upwork's raw bucket string, "
            "e.g. '1-9,10-' for 1+ hires."
        ),
    )
    sort: str | None = Field(
        default=None,
        description="Sort order. Common: 'recency', 'relevance+desc' (default), 'client_total_charge+desc'.",
    )
    feed: bool = Field(
        default=False,
        description=(
            "If true, use the personalised best-matches feed (largely ignores the query). "
            "If false (default) perform a real keyword search."
        ),
    )
    limit: int = Field(default=10, ge=1, le=50, description="Maximum number of results")


class JobDetailsParams(BaseModel):
    """Parameters for getting job details."""
    job_url: str = Field(description="Full Upwork job URL or job ID")


_EXP_LEVEL_MAP = {"entry": "1", "intermediate": "2", "expert": "3"}
_DURATION_MAP = {
    "week": "week",
    "month": "month",
    "semester": "semester",
    "ongoing": "ongoing",
    "<1 month": "week",
    "1-3 months": "month",
    "3-6 months": "semester",
    "6+ months": "ongoing",
}
_WORKLOAD_MAP = {
    "as_needed": "as_needed",
    "as needed": "as_needed",
    "part_time": "part_time",
    "part time": "part_time",
    "full_time": "full_time",
    "full time": "full_time",
}
_PROPOSALS_BUCKETS = {
    4: "0-4",
    9: "0-4,5-9",
    14: "0-4,5-9,10-14",
    19: "0-4,5-9,10-14,15-19",
    49: "0-4,5-9,10-14,15-19,20-49",
}


def _as_list(v) -> list[str]:
    """Normalize a str | list[str] | None to a list of stripped strings."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    return [str(x).strip() for x in v if str(x).strip()]


async def search_jobs(params: JobSearchParams) -> list[dict]:
    """Search for jobs on Upwork matching the specified criteria.

    Returns lightweight job previews including title, url, posted, budget,
    skills, the client's payment_verified/rating/total_spent/country, AND
    the proposals_tier bucket Upwork shows on the tile (e.g. "Less than 5",
    "5 to 10", "10 to 15"). The richer "Activity on this job" block
    (interviewing count, hires, last_viewed_by_client) still requires
    get_job_details.
    """
    browser = get_browser()
    page = await browser.get_page()

    # Build search URL.
    # /nx/search/jobs/ is the real keyword-search endpoint. /find-work/best-matches
    # is a personalised feed and largely ignores the `q` parameter.
    if params.feed:
        base_url = "https://www.upwork.com/nx/find-work/best-matches"
    else:
        base_url = "https://www.upwork.com/nx/search/jobs/"

    query_params: list[tuple[str, str]] = [("q", params.query)]

    if params.category:
        query_params.append(("category", params.category))

    # job_type → t param. Omit to include both (Upwork accepts t=0,1).
    if params.job_type:
        jt = params.job_type.lower()
        if jt == "hourly":
            query_params.append(("t", "0"))
        elif jt == "fixed":
            query_params.append(("t", "1"))

    # Experience level (list allowed). Upwork joins with commas: contractor_tier=1,2,3
    levels = []
    for v in _as_list(params.experience_level):
        mapped = _EXP_LEVEL_MAP.get(v.lower())
        if mapped:
            levels.append(mapped)
    if levels:
        query_params.append(("contractor_tier", ",".join(levels)))

    # Fixed-price budget. Upwork's `amount` uses bracketed ranges joined
    # by commas, e.g. amount=0-99,100-499. We expose a simple min/max pair.
    if params.budget_min is not None or params.budget_max is not None:
        lo = params.budget_min if params.budget_min is not None else 0
        hi = params.budget_max if params.budget_max is not None else ""
        if params.job_type is None or params.job_type.lower() == "fixed":
            query_params.append(("amount", f"{lo}-{hi}"))

    # Hourly rate range.
    if params.hourly_rate_min is not None or params.hourly_rate_max is not None:
        lo = params.hourly_rate_min if params.hourly_rate_min is not None else 0
        hi = params.hourly_rate_max if params.hourly_rate_max is not None else ""
        if params.job_type is None or params.job_type.lower() == "hourly":
            query_params.append(("hourly_rate", f"{lo}-{hi}"))

    # Workload
    workloads = []
    for v in _as_list(params.workload):
        mapped = _WORKLOAD_MAP.get(v.lower())
        if mapped:
            workloads.append(mapped)
    if workloads:
        query_params.append(("workload", ",".join(workloads)))

    # Duration (Upwork's filter is `duration_v3`)
    durations = []
    for v in _as_list(params.duration):
        mapped = _DURATION_MAP.get(v.lower())
        if mapped:
            durations.append(mapped)
    if durations:
        query_params.append(("duration_v3", ",".join(durations)))

    # Location (comma-separated list of country / region names)
    locs = _as_list(params.location)
    if locs:
        query_params.append(("location", ",".join(locs)))

    # Proposals bucket
    if params.proposals_max is not None:
        bucket = _PROPOSALS_BUCKETS.get(params.proposals_max)
        if bucket:
            query_params.append(("proposals", bucket))

    if params.payment_verified:
        query_params.append(("payment_verified", "1"))
    if params.contract_to_hire:
        query_params.append(("contract_to_hire", "true"))
    if params.client_hires:
        query_params.append(("client_hires", params.client_hires))
    if params.sort:
        query_params.append(("sort", params.sort))

    url = f"{base_url}?{urllib.parse.urlencode(query_params)}"
    # Use `commit` (earliest navigation event) instead of `domcontentloaded`
    # — heavy Upwork pages can take >30s to fire DOMContentLoaded but the
    # job tiles hydrate well before that. We rely on wait_for_selector for
    # the actual data readiness.
    await page.goto(url, wait_until="commit")

    try:
        await page.wait_for_selector(
            'article.job-tile [data-test="job-tile-title-link"]',
            timeout=20000,
        )
    except Exception:
        await asyncio.sleep(3)

    jobs: list[dict] = []
    seen_urls: set[str] = set()

    tiles = await page.query_selector_all("article.job-tile")

    for tile in tiles:
        if len(jobs) >= params.limit:
            break
        try:
            job = await _extract_job_tile(tile)
            if not job or job["url"] in seen_urls:
                continue
            seen_urls.add(job["url"])
            jobs.append(job)
        except Exception:
            continue

    return jobs


async def _extract_job_tile(tile) -> dict | None:
    """Extract a single job tile <article.job-tile> into a dict."""
    # Title link
    link_el = await tile.query_selector('[data-test="job-tile-title-link"]')
    if not link_el:
        return None
    href = await link_el.get_attribute("href")
    if not href or "/jobs/" not in href:
        return None

    # Title — strip the `<span class="highlight">` markers used for search
    # term highlighting so we get the clean job title.
    title_html_el = link_el
    title = await title_html_el.text_content()
    title = re.sub(r"\s+", " ", (title or "").strip())
    if not title:
        return None

    job: dict = {
        "title": title,
        # Strip the `?referrer_url_path=/nx/search/jobs/` tracking query.
        "url": (f"https://www.upwork.com{href}" if href.startswith("/") else href).split("?")[0],
    }

    # Posted time. `[data-test="job-pubilshed-date"]` (yes, Upwork's typo)
    # wraps a "Posted yesterday" / "Posted 2 hours ago" text.
    posted_el = await tile.query_selector('[data-test="job-pubilshed-date"]')
    if posted_el:
        posted_text = re.sub(r"\s+", " ", (await posted_el.text_content() or "").strip())
        posted_text = re.sub(r"^Posted\s+", "", posted_text, flags=re.I)
        if posted_text:
            job["posted"] = posted_text

    # Payment verified is a presence flag.
    if await tile.query_selector('[data-test="payment-verified"]'):
        job["payment_verified"] = True

    # Rating — read the numeric value from `.air3-rating-value-text`.
    rating_el = await tile.query_selector(
        '[data-test="total-feedback"] .air3-rating-value-text'
    )
    if rating_el:
        rating_text = (await rating_el.text_content() or "").strip()
        try:
            job["client_rating"] = float(rating_text)
        except ValueError:
            pass

    # Total spent — `<strong>$3K+</strong> spent`.
    spent_el = await tile.query_selector('[data-test="total-spent"] strong')
    if spent_el:
        spent = re.sub(r"\s+", " ", (await spent_el.text_content() or "").strip())
        if spent:
            job["total_spent"] = spent

    # Client country code (e.g. "DEU", "USA"). The visible label is wrapped
    # in a span with `sr-only` "Location " prefix — we drop that.
    loc_el = await tile.query_selector('[data-test="location"] .rr-mask')
    if loc_el:
        country = re.sub(r"\s+", " ", (await loc_el.text_content() or "").strip())
        country = country.replace("Location", "").strip()
        if country:
            job["client_country"] = country

    # Job type / budget. `[data-test="job-type-label"]` holds:
    #   "Fixed price"
    #   "Hourly: $15.00 - $30.00 "
    job_type_el = await tile.query_selector('[data-test="job-type-label"]')
    if job_type_el:
        job_type_text = re.sub(r"\s+", " ", (await job_type_el.text_content() or "").strip())
        if job_type_text:
            job["job_type"] = job_type_text
            # If it's "Hourly: $X - $Y" pull out the rate range.
            rate_match = re.search(
                r"Hourly:\s*\$([\d.,]+)\s*-\s*\$([\d.,]+)", job_type_text, re.I
            )
            if rate_match:
                job["hourly_rate_range"] = f"${rate_match.group(1)} - ${rate_match.group(2)}"

    # Experience level
    exp_el = await tile.query_selector('[data-test="experience-level"] strong')
    if exp_el:
        exp = re.sub(r"\s+", " ", (await exp_el.text_content() or "").strip())
        if exp:
            job["experience_level"] = exp

    # Fixed-price budget — "Est. budget: $30.00"
    fixed_el = await tile.query_selector('[data-test="is-fixed-price"]')
    if fixed_el:
        fixed_text = re.sub(r"\s+", " ", (await fixed_el.text_content() or "").strip())
        budget_match = re.search(r"\$[\d.,]+", fixed_text)
        if budget_match:
            job["budget"] = budget_match.group(0)

    # Proposals tier — "Proposals: 5 to 10" / "Less than 5"
    proposals_el = await tile.query_selector('[data-test="proposals-tier"] strong')
    if proposals_el:
        prop = re.sub(r"\s+", " ", (await proposals_el.text_content() or "").strip())
        if prop:
            job["proposals_tier"] = prop

    # Description snippet — single <p> inside the line-clamp wrapper.
    desc_el = await tile.query_selector(
        '.air3-line-clamp-wrapper.clamp p, .air3-line-clamp.is-clamped p'
    )
    if desc_el:
        desc = re.sub(r"\s+", " ", (await desc_el.text_content() or "").strip())
        if desc:
            job["description"] = desc[:500]

    # Skills — `<button data-test="token" class="air3-token"><span class="highlight-color">SKILL</span></button>`
    skills: list[str] = []
    for el in await tile.query_selector_all('[data-test="token"]'):
        text = re.sub(r"\s+", " ", (await el.text_content() or "").strip())
        if text and text not in skills:
            skills.append(text)
    if skills:
        job["skills"] = skills

    return job


async def get_job_details(params: JobDetailsParams) -> dict:
    """Get detailed information about a specific Upwork job posting.

    Returns comprehensive job details including description, client history,
    skills required, and application requirements.
    """
    browser = get_browser()
    page = await browser.get_page()

    # Normalize URL. We accept four input shapes:
    #   1. Full canonical URL  https://www.upwork.com/jobs/Slug_~022...../
    #   2. Search-modal URL    https://www.upwork.com/nx/search/jobs/details/~022....?_modalInfo=...
    #   3. Raw job id          ~022053317751386536044
    #   4. Bare path           /jobs/Slug_~022..../
    #
    # Important: Upwork ships TWO different layouts for the same job. The
    # standalone `/jobs/~ID/` page is a NEW redesign that ships description
    # under `.job-description-content` and drops the `data-test="Description"`
    # / `[data-test="about-client-container"]` hooks we depend on. The
    # modal-style URL `/nx/search/jobs/details/~ID` still renders the OLD
    # layout that exposes those data-test attributes — every selector in
    # this file is built against that layout. So whatever URL we receive,
    # we extract the `~ID` token and navigate to the modal route.
    raw = params.job_url.strip()
    id_match = re.search(r"(~0[\dA-Za-z]+)", raw)
    if id_match:
        url = f"https://www.upwork.com/nx/search/jobs/details/{id_match.group(1)}"
    else:
        # Fallback — couldn't find an id, hand the raw URL to the browser
        # and let it resolve. Likely a 404 but at least it's deterministic.
        if raw.startswith("http"):
            url = raw
        elif raw.startswith("/"):
            url = f"https://www.upwork.com{raw}"
        else:
            url = f"https://www.upwork.com/jobs/{raw}"
    url = url.split("?")[0]

    # Use `commit` so we don't block on DOMContentLoaded — heavy Upwork
    # pages can take >30s to fire that event, but the data we want
    # hydrates well before then.
    await page.goto(url, wait_until="commit")

    # The job detail page hydrates progressively: <h4> title and the
    # `.segmentations` row appear early (server-rendered), while the
    # `[data-test="Description"]` body, `about-client-container` sidebar
    # and `.client-activity-items` block come in with a later Vue render.
    # `#submit-proposal-button` is the stable end-of-hydration signal —
    # the Apply button only mounts once the full detail card is built.
    # We wait for it, then wait specifically for the Description before
    # giving up, then sleep as a last-resort fallback.
    try:
        await page.wait_for_selector(
            '#submit-proposal-button, [data-test="about-client-container"]',
            timeout=40000,
        )
    except Exception:
        pass
    try:
        await page.wait_for_selector('[data-test="Description"]', timeout=10000)
    except Exception:
        await asyncio.sleep(2)

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
