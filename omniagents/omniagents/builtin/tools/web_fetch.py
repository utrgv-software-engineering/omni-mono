import requests
from bs4 import BeautifulSoup
from typing import Any, Optional
from omniagents.core.tools import rich_function_tool, RichToolOutput
from agents.run_context import RunContextWrapper


@rich_function_tool(client_status="Fetching URL...")
def web_fetch(
    ctx: RunContextWrapper[Any],
    url: str,
    extract_text: Optional[bool] = True,
) -> RichToolOutput:
    """
    Fetches content from a given URL and optionally extracts text content.

    Args:
        url: The URL to fetch content from.
        extract_text: Whether to extract and return text content (true) or return raw HTML (false).

    Returns:
        Dictionary with status, content, and metadata.

    Usage:
        This function requires all parameters to be explicitly provided.

    Example:
        web_fetch(
            url="https://example.com",
            extract_text=true
        )
    """
    try:
        extract_text = True if extract_text is None else extract_text
        # Make the request to the URL
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            error_msg = f"Request failed with status code {response.status_code}"
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
            links = []
            for link in soup.find_all("a", href=True):
                href = link["href"]
                link_text = link.get_text(strip=True)
                if (
                    href
                    and not href.startswith("#")
                    and not href.startswith("javascript:")
                ):
                    links.append(
                        {"href": href, "text": link_text if link_text else None}
                    )

            result["links"] = links[:100]  # Limit to 100 links

            # For LLM output, provide the extracted text
            llm_output = text_content

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
                    "extracted_text": True,
                },
            }
        else:
            # Return raw HTML content
            llm_output = (
                response.text[:5000] if len(response.text) > 5000 else response.text
            )

            ui_metadata = {
                "value": response.text,
                "display_type": "web_content",
                "summary": f"Fetched HTML from {url}",
                "preview": response.text[:500],
                "truncated": len(response.text) > 500,
                "metadata": {
                    "url": url,
                    "content_type": content_type,
                    "size": len(response.content),
                    "extracted_text": False,
                },
            }

        return RichToolOutput(llm_output, ui_metadata)

    except requests.RequestException as e:
        error_msg = f"Network error occurred: {str(e)}"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "Network error",
            "preview": str(e),
            "truncated": False,
            "metadata": {"error_type": "network_error", "url": url, "error": str(e)},
        }
        return RichToolOutput(error_msg, ui_metadata)
    except Exception as e:
        error_msg = f"Error during web fetch: {str(e)}"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "Fetch error",
            "preview": str(e),
            "truncated": False,
            "metadata": {"error_type": "fetch_error", "url": url, "error": str(e)},
        }
        return RichToolOutput(error_msg, ui_metadata)
