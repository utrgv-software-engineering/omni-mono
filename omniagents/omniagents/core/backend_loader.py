"""Backend loading mechanism for OmniAgents."""

from typing import Optional, Dict, Any
import importlib
import importlib.metadata


def load_backend(backend_name: str) -> Optional[Any]:
    """Load a backend by name using entry points or direct import.
    
    Args:
        backend_name: Name of the backend to load (e.g., 'cli', 'flet', 'server')
        
    Returns:
        The backend class if found, None otherwise
    """
    # First try to load from entry points
    try:
        entry_points = importlib.metadata.entry_points()
        if hasattr(entry_points, 'select'):
            # Python 3.10+
            backends = entry_points.select(group='omniagents.backends')
        else:
            # Python 3.9
            backends = entry_points.get('omniagents.backends', [])
        
        for entry_point in backends:
            if entry_point.name == backend_name:
                return entry_point.load()
    except Exception:
        pass
    
    # Fallback to direct import for backward compatibility
    backend_modules = {
        'cli': 'omniagents.backends.cli:CLIBackend',
        'flet': 'omniagents.core.app_base:AgentApp',  # Still in core for now
        'server': 'omniagents.backends.server:ServerBackend',
    }
    
    if backend_name in backend_modules:
        try:
            module_path, class_name = backend_modules[backend_name].split(':')
            module = importlib.import_module(module_path)
            return getattr(module, class_name)
        except (ImportError, AttributeError):
            pass
    
    return None


def is_backend_available(backend_name: str) -> bool:
    """Check if a backend is available for loading.
    
    Args:
        backend_name: Name of the backend to check
        
    Returns:
        True if the backend can be loaded, False otherwise
    """
    return load_backend(backend_name) is not None