"""Shared application resources and persistence helpers.

main.py populates these on startup; ui.workspace reads them on every page
request via module-attribute access (live, not snapshotted). This single
module avoids the NiceGUI reload pitfall where the entry script runs under
__mp_main__ in a uvicorn worker — sys.modules['__main__'] from workspace
returns the wrong module and every resource looks like None.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

# Resource singletons — populated by main.init_resources at startup.
tm: Any = None
glossary: Any = None
kg: Any = None
translator: Any = None
doc_parser: Any = None
qa_engine: Any = None

# Persistence layer / paths used by the UI.
PROJECTS_DIR: Optional[Path] = None
llm_executor: Any = None

# Pure functions provided by main.py. Bound during module load (they don't
# depend on init_resources) so they're available immediately.
save_project: Optional[Callable[[dict], None]] = None
load_project: Optional[Callable[[str], Optional[dict]]] = None
save_pair_to_tm: Optional[Callable[..., None]] = None
parse_lang_pair: Optional[Callable[[str], tuple[str, str]]] = None
apply_colors: Optional[Callable[[], None]] = None
config: Any = None
