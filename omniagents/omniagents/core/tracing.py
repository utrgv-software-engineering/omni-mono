"""Tracing configuration for omniagents.

This module handles optional tracing integrations without requiring them as dependencies.
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def setup_tracing(tracing_config: Optional[Dict[str, Any]]) -> None:
    """Setup tracing based on configuration.
    
    Defaults to OpenAI SDK's default behavior (sending traces to OpenAI).
    
    Args:
        tracing_config: Tracing configuration from YAML or None for defaults
            Expected format:
            {
                'provider': 'default' | 'agentio' | 'none',
                'project': 'project_name',  # for agentio
                'name': 'trace_name',       # for agentio
                'settings': {                # additional agentio settings
                    'resume': 'allow' | 'must' | 'never',
                    ...
                }
            }
    """
    if not tracing_config:
        # No tracing config - keep SDK's default behavior
        return
    
    provider = tracing_config.get('provider', 'default')
    
    if provider == 'default':
        # Keep SDK's default behavior (traces go to OpenAI)
        logger.debug("Using default OpenAI tracing")
        return
        
    elif provider == 'agentio':
        try:
            import agentio
            
            # Extract agentio configuration
            project = tracing_config.get('project', 'omniagents')
            name = tracing_config.get('name')
            settings = tracing_config.get('settings', {})
            
            # Initialize agentio (this replaces OpenAI's processor)
            # AgentIO already prints its own initialization messages
            agentio.init(
                project=project,
                name=name,
                replace_default=True,  # Replace OpenAI's default processor
                **settings
            )
            
            logger.info(f"AgentIO tracing enabled for project: {project}")
                
        except ImportError:
            logger.warning("agentio package not installed, using default OpenAI tracing")
            print("⚠️  Warning: agentio not installed. Install with: pip install agentio")
            print("    Falling back to default OpenAI tracing")
            
        except Exception as e:
            logger.error(f"Failed to initialize agentio: {e}")
            print(f"❌ Error initializing agentio: {e}")
            print("    Falling back to default OpenAI tracing")
            
    elif provider == 'none':
        try:
            from agents import set_trace_processors
            set_trace_processors([])
            logger.info("Tracing disabled")
            print("🔇 Tracing disabled")
        except ImportError as e:
            logger.error(f"Failed to disable tracing: {e}")
            print(f"❌ Error disabling tracing: {e}")
            
    else:
        logger.warning(f"Unknown tracing provider: {provider}")
        print(f"⚠️  Unknown tracing provider: {provider}")
        print("    Using default OpenAI tracing")