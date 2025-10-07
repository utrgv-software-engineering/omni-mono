import random
from typing import List, Optional, Generator

def generate_readable_name(used_names: Optional[List[str]] = None) -> str:
    """
    Generate a readable name like "brave-sunset-0" with collision handling.
    Inspired by trackio's name generation.
    """
    adjectives = [
        "brave", "calm", "eager", "fancy", "gentle", "happy", "jolly", "kind",
        "lively", "merry", "nice", "proud", "quick", "silly", "witty", "bright",
        "bold", "clever", "daring", "elegant", "faithful", "graceful", "honest",
        "keen", "lucky", "modest", "noble", "patient", "quirky", "sincere",
        "upbeat", "valiant", "warm", "zesty", "cheerful", "energetic", "fearless",
        "hopeful", "joyful", "radiant", "spirited", "tranquil", "unique", "vivid"
    ]
    
    nouns = [
        "trace", "span", "agent", "function", "workflow", "pipeline", "session",
        "context", "stream", "handler", "processor", "analyzer", "monitor", "logger",
        "tracker", "observer", "collector", "reporter", "runner", "executor",
        "builder", "creator", "manager", "controller", "coordinator", "orchestrator"
    ]
    
    used_names = used_names or []
    
    # Try up to 100 times to find a unique name
    for i in range(100):
        adj = random.choice(adjectives)
        noun = random.choice(nouns)
        base_name = f"{adj}-{noun}"
        
        # Add number suffix if base name exists
        if base_name in used_names:
            for j in range(1000):
                name = f"{base_name}-{j}"
                if name not in used_names:
                    return name
        else:
            return base_name
    
    # Fallback to UUID if can't find unique name
    import uuid
    return f"trace-{uuid.uuid4().hex[:8]}"

def fibo() -> Generator[Optional[int], None, None]:
    """
    Fibonacci sequence generator for exponential backoff.
    Used for retry delays when connecting to remote servers.
    """
    a, b = 1, 1
    while True:
        yield a
        a, b = b, a + b
        if a > 100:  # Cap at ~10 seconds
            yield None
            break

# Reserved keys that users cannot use in trace metadata
RESERVED_KEYS = ["trace_id", "span_id", "project", "timestamp", "__"]

def validate_keys(data: dict) -> None:
    """Validate that no reserved keys are used"""
    if isinstance(data, dict):
        for key in data.keys():
            if key in RESERVED_KEYS or key.startswith("__"):
                raise ValueError(f"Reserved key not allowed: {key}")