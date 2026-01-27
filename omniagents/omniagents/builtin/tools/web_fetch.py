import time
import requests
from bs4 import BeautifulSoup
from typing import Any, Optional
from omniagents.core.tools import rich_function_tool, RichToolOutput
from agents.run_context import RunContextWrapper


# Constants for web_fetch
_WEB_FETCH_DEFAULT_TIMEOUT = 10
_WEB_FETCH_MAX_LINKS = 100


@rich_function_tool(client_status="Fetching URL...")
def web_fetch(
    ctx: RunContextWrapper[Any],
    url: str,
    extract_text: Optional[bool] = None,
    timeout: Optional[int] = None,
) -> RichToolOutput:
    """
    Fetches content from a given URL and optionally extracts text content.

    Args:
        url: The URL to fetch content from.
        extract_text: Whether to extract and return text content (true) or
            return raw HTML (false). Defaults to true.
        timeout: Request timeout in seconds. Defaults to 10.

    Returns:
        The page content with metadata. For HTML pages with extract_text=true,
        returns cleaned text with scripts/styles removed. Otherwise returns
        raw HTML (truncated to 5000 chars).

    Output format:
        - Shows elapsed time for the request
        - For extracted text: full content is returned (no truncation)
        - For raw HTML: content is truncated to 5000 characters
        - Links are limited to 100; a notice is shown if more exist

    Example:
        web_fetch(url="https://example.com")
        web_fetch(url="https://example.com", extract_text=false, timeout=30)
    """
    # Set defaults
    if extract_text is None:
        extract_text = True
    if timeout is None:
        timeout = _WEB_FETCH_DEFAULT_TIMEOUT

    start_time = time.monotonic()

    try:
        # Make the request to the URL
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        response = requests.get(url, headers=headers, timeout=timeout)

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        if response.status_code != 200:
            error_msg = f"Request failed with status code {response.status_code} (elapsed: {elapsed_ms}ms)"
            ui_metadata = {
                "value": error_msg,
                "display_type": "error",
                "summary": f"HTTP {response.status_code} error",
                "preview": response.text[:500] if response.text else error_msg,
                "truncated": len(response.text) > 500 if response.text else False,
                "metadata": {
                    "error_type": "http_error",
                    "status_code": response.status_code,
                    "url": url,
                    "elapsed_ms": elapsed_ms,
                },
            }
            return RichToolOutput(error_msg, ui_metadata)

        # Get the content type from headers
        content_type = response.headers.get("Content-Type", "")

        # Check if response is HTML
        is_html = "text/html" in content_type.lower()

        result = {
            "status": "success",
            "url": url,
            "content_type": content_type,
            "size": len(response.content),
            "headers": dict(response.headers),
        }

        # Process HTML content if requested and content is HTML
        if extract_text and is_html:
            soup = BeautifulSoup(response.text, "html.parser")

            # Extract title
            title = soup.title.string if soup.title else ""
            result["title"] = title

            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.extract()

            # Get text content
            text_content = soup.get_text(separator="\n", strip=True)

            # Clean up text: remove excessive newlines
            lines = [line.strip() for line in text_content.split("\n")]
            text_content = "\n".join(line for line in lines if line)

            result["text_content"] = text_content

            # Extract metadata
            meta_tags = {}
            for meta in soup.find_all("meta"):
                name = meta.get("name") or meta.get("property")
                content = meta.get("content")
                if name and content:
                    meta_tags[name] = content

            result["meta_tags"] = meta_tags

            # Extract links
            all_links = []
            for link in soup.find_all("a", href=True):
                href = link["href"]
                link_text = link.get_text(strip=True)
                if (
                    href
                    and not href.startswith("#")
                    and not href.startswith("javascript:")
                ):
                    all_links.append(
                        {"href": href, "text": link_text if link_text else None}
                    )

            total_links = len(all_links)
            links = all_links[:_WEB_FETCH_MAX_LINKS]
            links_truncated = total_links > _WEB_FETCH_MAX_LINKS
            result["links"] = links

            # Build LLM output with header
            header_parts = [f"Fetched: {url}", f"Elapsed: {elapsed_ms}ms"]
            if title:
                header_parts.insert(1, f"Title: {title}")
            if links_truncated:
                header_parts.append(
                    f"[Showing {_WEB_FETCH_MAX_LINKS} of {total_links} links]"
                )

            llm_output = " | ".join(header_parts) + "\n\n" + text_content

            # Create preview for UI
            preview = text_content[:500]
            truncated = len(text_content) > 500

            ui_metadata = {
                "value": text_content,
                "display_type": "web_content",
                "summary": f"Fetched {title or url}",
                "preview": preview,
                "truncated": truncated,
                "metadata": {
                    "url": url,
                    "title": title or None,
                    "content_type": content_type,
                    "size": len(response.content),
                    "meta_tags": meta_tags,
                    "link_count": len(links),
                    "total_links": total_links,
                    "links_truncated": links_truncated,
                    "extracted_text": True,
                    "elapsed_ms": elapsed_ms,
                },
            }
        else:
            # Return raw HTML content
            raw_html = response.text
            html_truncated = len(raw_html) > 5000

            # Build LLM output with header
            header = f"Fetched: {url} | Elapsed: {elapsed_ms}ms"
            if html_truncated:
                header += f" | [HTML truncated: showing 5000 of {len(raw_html)} chars]"

            llm_output = header + "\n\n" + (raw_html[:5000] if html_truncated else raw_html)

            ui_metadata = {
                "value": raw_html,
                "display_type": "web_content",
                "summary": f"Fetched HTML from {url}",
                "preview": raw_html[:500],
                "truncated": len(raw_html) > 500,
                "metadata": {
                    "url": url,
                    "content_type": content_type,
                    "size": len(response.content),
                    "extracted_text": False,
                    "elapsed_ms": elapsed_ms,
                    "html_truncated": html_truncated,
                },
            }

        return RichToolOutput(llm_output, ui_metadata)

    except requests.Timeout:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        error_msg = f"Request timed out after {timeout} seconds (elapsed: {elapsed_ms}ms)"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "Request timeout",
            "preview": f"Request to {url} timed out after {timeout}s",
            "truncated": False,
            "metadata": {
                "error_type": "timeout",
                "url": url,
                "timeout": timeout,
                "elapsed_ms": elapsed_ms,
            },
        }
        return RichToolOutput(error_msg, ui_metadata)
    except requests.RequestException as e:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        error_msg = f"Network error occurred: {str(e)} (elapsed: {elapsed_ms}ms)"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "Network error",
            "preview": str(e),
            "truncated": False,
            "metadata": {
                "error_type": "network_error",
                "url": url,
                "error": str(e),
                "elapsed_ms": elapsed_ms,
            },
        }
        return RichToolOutput(error_msg, ui_metadata)
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        error_msg = f"Error during web fetch: {str(e)} (elapsed: {elapsed_ms}ms)"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "Fetch error",
            "preview": str(e),
            "truncated": False,
            "metadata": {
                "error_type": "fetch_error",
                "url": url,
                "error": str(e),
                "elapsed_ms": elapsed_ms,
            },
        }
        return RichToolOutput(error_msg, ui_metadata)
