"""Messaging tools for Upwork MCP."""

from pydantic import BaseModel, Field
from ..browser.client import get_browser


class MessagesParams(BaseModel):
    """Parameters for getting messages."""
    room_id: str | None = Field(default=None, description="Specific chat room ID or URL")
    unread_only: bool = Field(default=False, description="Only show unread messages")
    limit: int = Field(default=20, ge=1, le=50, description="Maximum conversations to return")


class SendMessageParams(BaseModel):
    """Parameters for sending a message."""
    room_id: str = Field(description="Chat room ID or URL")
    message: str = Field(description="Message content to send")


async def get_messages(params: MessagesParams) -> list[dict]:
    """Get messages from Upwork inbox.

    Returns a list of conversations with last message, sender info, and unread status.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Navigate to messages
    url = "https://www.upwork.com/nx/messages"
    if params.unread_only:
        url += "?filter=unread"

    await page.goto(url, wait_until="domcontentloaded")

    conversations = []

    # Wait for message list
    try:
        await page.wait_for_selector('[data-test="room-list"], .room-list, .message-list', timeout=10000)
    except Exception:
        pass

    # Extract conversation items
    room_els = await page.query_selector_all('[data-test="room-item"], .room-item, .conversation-item')

    for el in room_els[:params.limit]:
        try:
            conv = await _extract_conversation(el)
            if conv:
                conversations.append(conv)
        except Exception:
            continue

    return conversations


async def _extract_conversation(el) -> dict | None:
    """Extract conversation data from element."""
    conv = {}

    # Contact name
    name_el = await el.query_selector('[data-test="contact-name"], .contact-name, .sender-name')
    if name_el:
        conv["contact_name"] = (await name_el.text_content() or "").strip()

    if not conv.get("contact_name"):
        return None

    # Room URL/ID
    room_link = await el.query_selector('a[href*="/messages/"]')
    if room_link:
        href = await room_link.get_attribute("href")
        if href:
            conv["room_url"] = href if href.startswith("http") else f"https://www.upwork.com{href}"
            # Extract room ID from URL
            if "/messages/" in href:
                conv["room_id"] = href.split("/messages/")[-1].split("/")[0].split("?")[0]

    # Last message preview
    preview_el = await el.query_selector('[data-test="message-preview"], .preview, .last-message')
    if preview_el:
        conv["last_message"] = (await preview_el.text_content() or "").strip()

    # Timestamp
    time_el = await el.query_selector('[data-test="timestamp"], time, .time')
    if time_el:
        conv["timestamp"] = (await time_el.text_content() or "").strip()

    # Unread indicator
    unread_el = await el.query_selector('[data-test="unread"], .unread-badge, .unread-indicator')
    conv["unread"] = unread_el is not None

    # Related job (if any)
    job_el = await el.query_selector('[data-test="related-job"], .job-title')
    if job_el:
        conv["related_job"] = (await job_el.text_content() or "").strip()

    return conv


async def get_conversation_messages(room_id: str, limit: int = 50) -> dict:
    """Get all messages in a specific conversation.

    Args:
        room_id: The room ID or URL
        limit: Maximum messages to return

    Returns conversation details with full message history.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Build URL
    if room_id.startswith("http"):
        url = room_id
    else:
        url = f"https://www.upwork.com/nx/messages/{room_id}"

    await page.goto(url, wait_until="domcontentloaded")

    conversation = {"room_id": room_id, "messages": []}

    # Contact name
    contact_el = await page.query_selector('[data-test="contact-name"], .contact-name, h2')
    if contact_el:
        conversation["contact_name"] = (await contact_el.text_content() or "").strip()

    # Related job
    job_el = await page.query_selector('[data-test="related-job"], .job-link')
    if job_el:
        conversation["related_job"] = (await job_el.text_content() or "").strip()

    # Extract messages
    message_els = await page.query_selector_all('[data-test="message"], .message-item, .chat-message')

    for el in message_els[-limit:]:  # Get last N messages
        try:
            msg = await _extract_message(el)
            if msg:
                conversation["messages"].append(msg)
        except Exception:
            continue

    return conversation


async def _extract_message(el) -> dict | None:
    """Extract message data from element."""
    msg = {}

    # Sender
    sender_el = await el.query_selector('[data-test="sender"], .sender, .author')
    if sender_el:
        msg["sender"] = (await sender_el.text_content() or "").strip()

    # Message content
    content_el = await el.query_selector('[data-test="content"], .content, .message-text, p')
    if content_el:
        msg["content"] = (await content_el.text_content() or "").strip()

    if not msg.get("content"):
        return None

    # Timestamp
    time_el = await el.query_selector('[data-test="timestamp"], time, .time')
    if time_el:
        msg["timestamp"] = (await time_el.text_content() or "").strip()

    # Check if it's from me
    me_indicator = await el.query_selector('.my-message, [data-test="my-message"], .sent')
    msg["is_mine"] = me_indicator is not None

    # Attachments
    attachment_els = await el.query_selector_all('[data-test="attachment"], .attachment')
    attachments = []
    for att in attachment_els:
        att_name = await att.text_content()
        if att_name:
            attachments.append(att_name.strip())
    if attachments:
        msg["attachments"] = attachments

    return msg


async def send_message(params: SendMessageParams) -> dict:
    """Send a message in a conversation.

    Args:
        params.room_id: Chat room ID or URL
        params.message: Message content

    Returns send status.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Navigate to conversation
    if params.room_id.startswith("http"):
        url = params.room_id
    else:
        url = f"https://www.upwork.com/nx/messages/{params.room_id}"

    await page.goto(url, wait_until="domcontentloaded")

    # Find message input
    input_el = await page.query_selector('[data-test="message-input"], textarea[name*="message"], .message-input textarea')
    if not input_el:
        return {"status": "error", "message": "Message input not found"}

    # Type message
    await input_el.fill(params.message)

    # Find and click send button
    send_btn = await page.query_selector('[data-test="send-button"], button[type="submit"]:has-text("Send"), button:has-text("Send")')
    if not send_btn:
        # Try pressing Enter
        await input_el.press("Enter")
    else:
        await send_btn.click()

    # Wait for message to appear
    import asyncio
    await asyncio.sleep(2)

    # Verify message was sent by checking if input is cleared
    input_value = await input_el.input_value()
    if not input_value:
        return {"status": "sent", "message": "Message sent successfully"}

    return {"status": "unknown", "message": "Could not confirm message was sent"}


async def get_unread_count() -> dict:
    """Get count of unread messages.

    Returns total unread message count.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Check messages badge in header
    await page.goto("https://www.upwork.com/nx/find-work/", wait_until="domcontentloaded")

    unread_el = await page.query_selector('[data-test="messages-badge"], .messages-count, .unread-count')
    if unread_el:
        text = (await unread_el.text_content() or "").strip()
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            return {"unread_count": int(numbers[0])}

    return {"unread_count": 0}
