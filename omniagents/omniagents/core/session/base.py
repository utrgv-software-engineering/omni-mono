"""Base classes and interfaces for session management."""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any
import uuid


class SessionBase(ABC):
    """Abstract base class for session implementations."""
    
    def __init__(self, session_id: str | None = None) -> None:
        self.id: str = session_id or str(uuid.uuid4())
        self.history: list[dict] = []
    
    @abstractmethod
    def append_message(self, message: dict) -> None:
        """Add a message to the session and persist it."""
        pass
    
    @abstractmethod
    def load_history(self) -> List[Dict[str, Any]]:
        """Load the message history for this session."""
        pass


class SessionStorageBase(ABC):
    """Abstract base class for session storage implementations."""
    
    @abstractmethod
    def load_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Load message history for a session."""
        pass
    
    @abstractmethod
    def append_message(self, session_id: str, message: Dict[str, Any]) -> None:
        """Append a message to session history."""
        pass
    
    @abstractmethod
    def register_session(self, session_id: str) -> None:
        """Register a new session."""
        pass
    
    @abstractmethod
    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all sessions."""
        pass
    
    @abstractmethod
    def set_archived(self, session_id: str, archived: bool) -> None:
        """Set the archived status of a session."""
        pass
    
    @abstractmethod
    def get_archived(self, session_id: str) -> bool:
        """Get the archived status of a session."""
        pass