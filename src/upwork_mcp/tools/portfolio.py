"""Portfolio tools for Upwork MCP."""

import asyncio
import re
import urllib.parse

from pydantic import BaseModel, Field

from ..browser.client import get_browser


class PortfolioItemParams(BaseModel):
    """Parameters for fetching a single portfolio item."""

    url: str = Field(
        description=(
            "Full portfolio URL — either the freelancer profile URL with a "
            "`?p=<project_id>` query (e.g. "
            "https://www.upwork.com/freelancers/~01ABC?p=2053...) or a raw "
            "project id. The id alone is treated as `?p=<id>` on the current "
            "freelancer profile if a freelancer_url is supplied separately."
        )
    )
    freelancer_url: str | None = Field(
        default=None,
        description=(
            "Optional. Freelancer profile URL to combine with a raw project "
            "id. Ignored when `url` already contains `/freelancers/~`."
        ),
    )


async def get_portfolio_item(params: PortfolioItemParams) -> dict:
    """Open a Upwork portfolio item (case study) and return its content.

    Returns:
        title, role, description, skills, published, links, images, url

    The portfolio "case study" is rendered as a modal on top of the freelancer
    profile page. We navigate to the `?p=<id>` URL and wait for the modal
    container to appear before scraping.
    """
    browser = get_browser()
    page = await browser.get_page()

    url = _normalise_portfolio_url(params.url, params.freelancer_url)

    await page.goto(url, wait_until="domcontentloaded")

    # The portfolio modal is hydrated client-side. Wait for the dedicated
    # modal class; fall back to a short sleep if it never shows.
    try:
        await page.wait_for_selector(
            ".air3-modal-portfolio-v2-viewer-modal",
            timeout=15000,
        )
    except Exception:
        await asyncio.sleep(3)

    modal = await page.query_selector(".air3-modal-portfolio-v2-viewer-modal")
    if not modal:
        return {
            "url": url,
            "error": (
                "Portfolio modal did not render. The freelancer profile may "
                "be private, the project id may be invalid, or the page "
                "blocked the modal from opening."
            ),
        }

    item: dict = {"url": url}

    # Title
    title_el = await modal.query_selector(".air3-modal-header h2")
    if title_el:
        title = re.sub(r"\s+", " ", (await title_el.text_content() or "").strip())
        if title:
            item["title"] = title

    # Left column: role / description / skills / published
    # Each block uses `<span class="text-light">Label.</span> value` inline.
    text_blocks = await modal.query_selector_all(
        ".sticky-left-column .span-12.text-body, "
        ".sticky-left-column .text-pre-line, "
        ".sticky-left-column > .span-12"
    )
    for block in text_blocks:
        full_text = re.sub(r"\s+", " ", (await block.text_content() or "").strip())
        if not full_text:
            continue
        # The label sits inside a child span; the rest of the text is the value.
        label_el = await block.query_selector("span.text-light")
        if label_el:
            label = re.sub(r"\s+", " ", (await label_el.text_content() or "").strip())
            label_clean = label.rstrip(".").strip().lower()
            # Value = full text with the label substring removed once.
            value = full_text.replace(label, "", 1).strip()
            if not value:
                continue
            if label_clean == "my role":
                item["role"] = value
            elif label_clean == "project description":
                item["description"] = value
            elif label_clean == "skills and deliverables":
                # Skills sit in a separate token list below, but a fallback
                # text scrape is fine if that selector misses.
                if "skills" not in item:
                    item["skills_raw"] = value

    # Skills tokens (more reliable than the text fallback above)
    skill_els = await modal.query_selector_all(".sticky-left-column .air3-token-wrap .air3-token")
    skills: list[str] = []
    for el in skill_els:
        text = re.sub(r"\s+", " ", (await el.text_content() or "").strip())
        if text and text not in skills:
            skills.append(text)
    if skills:
        item["skills"] = skills
        item.pop("skills_raw", None)

    # Published date — `<small class="text-light">Published on May 10, 2026</small>`
    for el in await modal.query_selector_all(".sticky-left-column small.text-light"):
        text = re.sub(r"\s+", " ", (await el.text_content() or "").strip())
        if text.lower().startswith("published"):
            item["published"] = text.replace("Published on", "").strip()
            break

    # Right column: images and linked URLs.
    images: list[str] = []
    for img in await modal.query_selector_all(
        ".portfolio-v2-viewer-media-block-image img[src]"
    ):
        src = await img.get_attribute("src")
        if not src:
            continue
        # Make absolute
        if src.startswith("/"):
            src = f"https://www.upwork.com{src}"
        if src not in images:
            images.append(src)
    if images:
        item["images"] = images

    links: list[str] = []
    for a in await modal.query_selector_all(
        ".portfolio-v2-viewer-media-block-link a[href]"
    ):
        href = await a.get_attribute("href")
        if href and href not in links and not href.startswith("javascript:"):
            links.append(href)
    if links:
        item["links"] = links

    return item


def _normalise_portfolio_url(raw: str, freelancer_url: str | None) -> str:
    """Build a canonical portfolio modal URL.

    Accepts:
      - Full URL with ?p=<id> already on it
      - A raw numeric project id when freelancer_url is provided
      - A freelancer URL alone (no project id) — passed through unchanged
    """
    raw = raw.strip()

    # Already a fully-qualified URL?
    if raw.startswith("http"):
        return raw

    # Path that looks like /freelancers/~01.../  ?
    if raw.startswith("/freelancers/"):
        return f"https://www.upwork.com{raw}"

    # Raw numeric id — combine with freelancer_url if provided
    if raw.isdigit() and freelancer_url:
        f_url = freelancer_url.strip()
        if not f_url.startswith("http"):
            f_url = f"https://www.upwork.com{f_url if f_url.startswith('/') else '/freelancers/' + f_url}"
        # Append ?p=<id> (or &p=<id> if there's already a query)
        sep = "&" if "?" in f_url else "?"
        return f"{f_url}{sep}p={raw}"

    raise ValueError(
        "Could not interpret portfolio URL. Pass a full "
        "https://www.upwork.com/freelancers/~01...?p=<id> URL, or a raw "
        "project id together with `freelancer_url`."
    )
