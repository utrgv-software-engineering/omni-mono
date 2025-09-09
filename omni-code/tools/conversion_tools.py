# Initialize MarkItDown
from omniagents.core.tools import rich_function_tool, RichToolOutput
from markitdown import MarkItDown
import os

md_converter = MarkItDown(enable_plugins=True)  # Enable plugins with all features

# Define tools
@rich_function_tool
def convert_to_markdown(file_path: str) -> RichToolOutput:
    """
    Convert virtually any file format to markdown for analysis and review.
    
    Args:
        file_path: Path to the file to convert or a URL (YouTube, web pages, etc.)
        
    Returns:
        JSON object with the following structure:
        {
            "success": boolean indicating if conversion was successful,
            "content": markdown content of the converted file/URL,
            "error": error message (if unsuccessful)
        }
        
    Supported formats include:
    - Documents: PDF, Word (DOCX, DOC), PowerPoint (PPTX, PPT), Excel (XLSX, XLS)
    - Media: Images (with EXIF data and optional OCR), Audio (with transcription)
    - Web content: HTML, YouTube videos (with transcripts)
    - Text formats: CSV, JSON, XML, TXT
    - Archives: ZIP (processes contained files)
    - E-books: EPUB
    - Emails: Outlook messages
    
    For YouTube videos, the content includes:
    - Video title and metadata (views, keywords, runtime)
    - Video description
    - Full transcript (when available)
    
    For images, the content includes:
    - EXIF metadata
    - OCR-extracted text (when applicable)
    
    For audio files, the content includes:
    - File metadata
    - Transcribed speech (when applicable)
    """
    try:
        # Check if file exists (skip for URLs)
        if not file_path.startswith('http') and not os.path.exists(file_path):
            error_msg = f"File not found at {file_path}"
            ui_metadata = {
                "value": error_msg,
                "display_type": "error",
                "summary": "File not found",
                "preview": error_msg,
                "truncated": False,
                "metadata": {
                    "error_type": "file_not_found",
                    "file_path": file_path,
                    "success": False
                }
            }
            return RichToolOutput(error_msg, ui_metadata)
        
        # Convert file to markdown
        result = md_converter.convert(file_path)
        content = result.text_content
        
        # Create preview (first 500 chars)
        preview = content[:500] if content else "No content extracted"
        truncated = len(content) > 500 if content else False
        
        # Determine source type
        if file_path.startswith('http'):
            if 'youtube.com' in file_path or 'youtu.be' in file_path:
                source_type = 'youtube'
            else:
                source_type = 'web'
        else:
            ext = os.path.splitext(file_path)[1].lower()
            source_type = ext[1:] if ext else 'unknown'
        
        ui_metadata = {
            "value": content,  # The actual markdown content
            "display_type": "conversion",
            "summary": f"Converted {os.path.basename(file_path) if not file_path.startswith('http') else file_path}",
            "preview": preview,
            "truncated": truncated,
            "metadata": {
                "success": True,
                "file_path": file_path,
                "source_type": source_type,
                "content_length": len(content) if content else 0
            }
        }
        return RichToolOutput(content, ui_metadata)
    except Exception as e:
        error_msg = f"Error converting file: {str(e)}"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "Conversion error",
            "preview": str(e),
            "truncated": False,
            "metadata": {
                "error_type": "conversion_error",
                "file_path": file_path,
                "error": str(e),
                "success": False
            }
        }
        return RichToolOutput(error_msg, ui_metadata)
