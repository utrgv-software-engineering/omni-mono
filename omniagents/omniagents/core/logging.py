"""Logging configuration for OmniAgents."""

import logging
import os


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name.
    
    Args:
        name: The name of the logger (usually __name__)
        
    Returns:
        A configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Only configure if not already configured
    if not logger.handlers:
        # Get log level from environment or default to INFO
        log_level = os.environ.get('OMNIAGENTS_LOG_LEVEL', 'INFO').upper()
        logger.setLevel(getattr(logging, log_level, logging.INFO))
        
        # Create console handler
        handler = logging.StreamHandler()
        handler.setLevel(logger.level)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        
        # Add handler to logger
        logger.addHandler(handler)
        
        # Prevent propagation to avoid duplicate logs
        logger.propagate = False
    
    return logger