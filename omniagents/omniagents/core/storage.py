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
            wait_timeout=10,
            wait_for_result=True,
        )
        # --- ADDED DEBUG PRINT ---
        ts_raw = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{ts_raw}] Raw read from clientStorage for key '{key}': {result!r} (Type: {type(result)})")
        # ------------------------
        if result:
            # --- FIX: Remove extra json.loads --- #
            # The result is already a JSON string from Flet
            return json.loads(result)
            # ------------------------------------
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
            value = await get_with_longer_timeout(page, key, field.default) # Uses original get
            loaded_settings[field.key] = value
            ts_loaded = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            # --- MODIFIED LOGGING (NO SECRET MASKING) --- 
            value_repr = repr(value)
            # Removed masking: log_value = '******' if field.secret else value_repr
            print(f"[{ts_loaded}]     Loaded '{field.key}': {value_repr} (Type: {type(value)}, Secret: {field.secret})") # Now always prints repr(value)
            # ---------------------------------------------
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

async def save_settings(page: ft.Page, prefix: str, settings_dict: Dict[str, Any]):
    """Saves settings to client storage (asynchronously with timeout)."""
    print(f"Saving settings with prefix: {prefix}")
    save_timeout = 10 # Seconds
    try:
        for key, value in settings_dict.items():
            storage_key = f"{prefix}{key}"
            # --- ADDED DEBUG PRINT ---
            ts_write = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            print(f"[{ts_write}] Raw write to clientStorage: key='{storage_key}', value={value!r} (Type: {type(value)})")
            # ------------------------
            # Use invoke_method_async for set with timeout
            # NOTE: We are still passing the raw value, matching the 'original'
            # save behavior. If this causes issues (e.g., type errors),
            # we might need json.dumps(value) here instead.
            await page._invoke_method_async(
                method_name="clientStorage:set",
                arguments={"key": storage_key, "value": value},
                wait_timeout=save_timeout
                # No wait_for_result needed for set
            )
            # Optional: Print saved value for debugging (consider showing the key)
            # print(f"  Saved '{key}'") # Avoid printing potentially sensitive values
        print("Settings saved successfully.")
    except Exception as e:
        print(f"Error saving settings to client storage: {e}") 