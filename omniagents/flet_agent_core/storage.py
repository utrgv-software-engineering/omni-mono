import flet as ft
import json
from typing import Dict, Any, List
from datetime import datetime # Import datetime
from .settings import SettingField # Import SettingField for default values

async def get_with_longer_timeout(page: ft.Page, key: str, default_value: Any = "") -> Any:
    """Gets value from client storage with a longer timeout."""
    try:
        result = await page._invoke_method_async( # noqa: WPS437
            method_name="clientStorage:get",
            arguments={"key": key},
            wait_timeout=0.5,
            wait_for_result=True,
        )
        if result:
            # The result seems to be double-encoded JSON
            return json.loads(json.loads(result))
        return default_value
    except Exception as e:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] # Timestamp
        print(f"[{ts}] Error getting {key} from client storage: {e}")
        return default_value

async def load_settings(page: ft.Page, prefix: str, fields: List[SettingField]) -> Dict[str, Any]:
    """Loads settings from client storage based on SettingField specs."""
    loaded_settings = {}
    ts_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{ts_start}] Loading settings with prefix: {prefix}")
    try: # Add try/except around the loop
        for field in fields:
            key = f"{prefix}{field.key}"
            ts_attempt = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            print(f"[{ts_attempt}]   Attempting to load key: {key} (Field: {field.label})") # Added print
            # Use the field's default value if not found in storage
            value = await get_with_longer_timeout(page, key, field.default)
            loaded_settings[field.key] = value
            ts_loaded = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            print(f"[{ts_loaded}]     Loaded '{field.key}': {'******' if field.secret else value}") # Added print
        ts_complete = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{ts_complete}] Settings loading complete.") # This should now be reached if loop finishes
    except Exception as e:
        ts_error = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{ts_error}] ERROR during settings load loop: {e}") # Print error if loop fails
        # Decide how to handle partial load: return defaults or partially loaded?
        # Returning defaults for safety:
        loaded_settings = {f.key: f.default for f in fields}
        ts_error_return = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{ts_error_return}]   Returning default settings due to error during load.")
    return loaded_settings

def save_settings(page: ft.Page, prefix: str, settings_dict: Dict[str, Any]):
    """Saves settings to client storage."""
    print(f"Saving settings with prefix: {prefix}")
    try:
        for key, value in settings_dict.items():
            storage_key = f"{prefix}{key}"
            page.client_storage.set(storage_key, value)
            # Optional: Print saved value for debugging
            # print(f"  Saved '{key}': {'******' if key.endswith('_key') or key.endswith('_token') else value}")
        print("Settings saved successfully.")
    except Exception as e:
        print(f"Error saving settings to client storage: {e}") 