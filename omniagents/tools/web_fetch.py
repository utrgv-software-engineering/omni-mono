import os
import json
import requests
from bs4 import BeautifulSoup
from typing import Dict, Any, Optional
from agents import function_tool

@function_tool
def web_fetch(url: str, extract_text: bool) -> Dict[str, Any]:
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
        # Make the request to the URL
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            return {
                "status": "error",
                "error": f"Request failed with status code {response.status_code}: {response.text}"
            }
        
        # Get the content type from headers
        content_type = response.headers.get('Content-Type', '')
        
        # Check if response is HTML
        is_html = 'text/html' in content_type.lower()
        
        result = {
            "status": "success",
            "url": url,
            "content_type": content_type,
            "size": len(response.content),
            "headers": dict(response.headers)
        }
        
        # Process HTML content if requested and content is HTML
        if extract_text and is_html:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract title
            title = soup.title.string if soup.title else ''
            result["title"] = title
            
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.extract()
            
            # Get text content
            text = soup.get_text(separator='\n', strip=True)
            
            # Clean up text: remove excessive newlines
            lines = [line.strip() for line in text.split('\n')]
            text = '\n'.join(line for line in lines if line)
            
            result["text_content"] = text
            
            # Extract metadata
            meta_tags = {}
            for meta in soup.find_all('meta'):
                name = meta.get('name') or meta.get('property')
                content = meta.get('content')
                if name and content:
                    meta_tags[name] = content
            
            result["meta_tags"] = meta_tags
            
            # Extract links
            links = []
            for link in soup.find_all('a', href=True):
                href = link['href']
                text = link.get_text(strip=True)
                if href and not href.startswith('#') and not href.startswith('javascript:'):
                    links.append({
                        "href": href,
                        "text": text if text else None
                    })
            
            result["links"] = links[:100]  # Limit to 100 links
        else:
            # Return raw content
            result["raw_content"] = response.text
        
        return result
        
    except requests.RequestException as e:
        return {
            "status": "error",
            "error": f"Network error occurred: {str(e)}"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": f"Error during web fetch: {str(e)}"
        }
