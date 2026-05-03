import os
import tempfile
import requests
from typing import Dict, Any, Optional
from agents.run_context import RunContextWrapper
from omniagents.core.tools import rich_function_tool, RichToolOutput, approval_metadata


from omniagents.builtin.tools._workspace import require_workspace_root


@rich_function_tool(client_status="Downloading file...")
def download_file(
    ctx: RunContextWrapper[Any],
    url: str,
    output_path: Optional[str] = None,
) -> RichToolOutput:
    """
    Downloads a file from a given URL and saves it locally to a temporary location.

    Args:
        url: The URL of the file to download.
        output_path: Optional path where the file should be saved. If not provided,
                    will save to a temporary location with filename from URL.

    Returns:
        Dictionary with status, file path, and metadata.

    Usage:
        This function downloads a file from a URL and saves it to a temporary location.
        It can be chained with convert_to_markdown for document analysis.

    Example:
        result = download_file(
            url="https://example.com/document.pdf"
        )
        if result["success"]:
            markdown_result = convert_to_markdown(result["file_path"])
    """
    try:
        # Make the request to the URL
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        response = requests.get(url, headers=headers, stream=True, timeout=30)

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
                    "success": False,
                    "file_path": None,
                },
            }
            return RichToolOutput(error_msg, ui_metadata)

        # Get content type and filename
        content_type = response.headers.get("Content-Type", "")

        # Determine filename or extension for the temporary file
        if output_path:
            # User provided a specific path to use. Anchor relative paths to
            # the workspace root so the agent's "downloads/foo" lands inside
            # its workspace, not the host process cwd. Absolute paths are
            # honored as-is.
            if not os.path.isabs(output_path):
                wr = require_workspace_root(ctx, where="download_file")
                output_path = os.path.join(wr, output_path)
            file_dir = os.path.dirname(os.path.abspath(output_path))
            filename = os.path.basename(output_path)

            # Create directory for output_path if it doesn't exist
            os.makedirs(file_dir, exist_ok=True)

            # Use the provided output path
            final_path = output_path
        else:
            # -----------------------------------------------------
            # 1. Try to extract filename from Content-Disposition
            # -----------------------------------------------------
            filename = None
            disposition_header = response.headers.get("Content-Disposition", "")
            if "filename=" in disposition_header:
                # Content-Disposition takes precedence because it is set
                # by the server explicitly and usually contains the
                # canonical filename for the download.
                filename = disposition_header.split("filename=")[1].strip(" \"'")
                filename_source = "header"
            else:
                # -------------------------------------------------
                # 2. Fallback to the filename in the URL path
                # -------------------------------------------------
                filename = url.split("/")[-1].split("?")[0]
                filename_source = "url"

            # Ensure filename is at least an empty string if nothing found
            filename = filename or ""

            # Determine file extension based on filename or content type
            extension = os.path.splitext(filename)[1]
            if not extension:
                # Map common content types to extensions when the filename
                # does not already contain one.
                extension_map = {
                    "application/pdf": ".pdf",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                    "application/msword": ".doc",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                    "application/vnd.ms-excel": ".xls",
                    "text/plain": ".txt",
                    "text/html": ".html",
                    "image/jpeg": ".jpg",
                    "image/png": ".png",
                }
                extension = extension_map.get(content_type.split(";")[0], "")

            # -----------------------------------------------------
            # Decide how to create the temporary file:
            #   - If the filename came from the header, we preserve the
            #     provided name by creating a temporary directory and
            #     joining the filename.
            #   - Otherwise (filename from URL or generated), we rely on
            #     mkstemp so that the OS chooses a safe unique location.
            # -----------------------------------------------------
            if filename_source == "header" and filename:
                temp_dir = tempfile.mkdtemp(prefix="download_")
                final_path = os.path.join(temp_dir, filename)
            else:
                # If no extension could be determined yet, append the one
                # we inferred from the content type (may be empty string).
                suffix = extension if extension else ""
                fd, final_path = tempfile.mkstemp(suffix=suffix)
                os.close(fd)  # Close the file descriptor

        # Save the file
        with open(final_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        file_size = os.path.getsize(final_path)

        # Create LLM-friendly output
        llm_output = f"Successfully downloaded file from {url} to {final_path} ({file_size} bytes)"

        ui_metadata = {
            "value": final_path,  # The actual result - the file path
            "display_type": "download",
            "summary": f"Downloaded {os.path.basename(final_path)}",
            "preview": f"File saved to: {final_path}\nSize: {file_size} bytes\nType: {content_type}",
            "truncated": False,
            "metadata": {
                "success": True,
                "file_path": final_path,
                "url": url,
                "content_type": content_type,
                "file_size": file_size,
                "is_temporary": output_path is None,
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
            "metadata": {
                "error_type": "network_error",
                "url": url,
                "error": str(e),
                "success": False,
                "file_path": None,
            },
        }
        return RichToolOutput(error_msg, ui_metadata)
    except Exception as e:
        error_msg = f"Error during file download: {str(e)}"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "Download error",
            "preview": str(e),
            "truncated": False,
            "metadata": {
                "error_type": "download_error",
                "url": url,
                "error": str(e),
                "success": False,
                "file_path": None,
            },
        }
        return RichToolOutput(error_msg, ui_metadata)


@approval_metadata()
def download_file_approval(url: str, output_path: Optional[str] = None) -> dict:
    """Generate approval metadata for download_file showing URL and destination."""
    if output_path:
        dest = output_path
    else:
        filename = url.split("/")[-1].split("?")[0] or "unknown"
        dest = f"(temporary) {filename}"

    preview = f"URL: {url}\nSave to: {dest}"

    return {
        "display_type": "command",
        "summary": f"Download {os.path.basename(dest) if output_path else url.split('/')[-1].split('?')[0] or 'file'}",
        "value": url,
        "preview": preview,
        "truncated": False,
        "metadata": {
            "url": url,
            "output_path": output_path,
            "command": f"download {url}",
        },
    }
