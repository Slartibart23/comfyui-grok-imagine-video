from .grok_imagine_nodes import (
    NODE_CLASS_MAPPINGS as _API_NODES,
    NODE_DISPLAY_NAME_MAPPINGS as _API_NAMES,
    __version__,
    _temp_folder_report,
)
from .grok_save_nodes import (
    NODE_CLASS_MAPPINGS as _SAVE_NODES,
    NODE_DISPLAY_NAME_MAPPINGS as _SAVE_NAMES,
    register_routes,
)

NODE_CLASS_MAPPINGS = {**_API_NODES, **_SAVE_NODES}
NODE_DISPLAY_NAME_MAPPINGS = {**_API_NAMES, **_SAVE_NAMES}

WEB_DIRECTORY = "./web"

register_routes(__version__)
_temp_folder_report()
print(f"[Grok Imagine] v{__version__} loaded ({len(NODE_CLASS_MAPPINGS)} nodes)")

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
