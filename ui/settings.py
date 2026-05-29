"""Global UI settings — dark mode + AI pretranslation flag.

100% NiceGUI high-level API. No custom CSS, no add_head_html, no run_javascript.
Dark mode is handled by NiceGUI's `ui.dark_mode()` which sets `body.body--dark`;
every NiceGUI component (ui.card, ui.input, ui.textarea, ui.tabs, ui.table)
re-styles automatically.
"""
from nicegui import app, ui


def install_dark_mode() -> ui.dark_mode:
    """ui.dark_mode bound to app.storage.user['dark']."""
    dm = ui.dark_mode(value=app.storage.user.get("dark", False))
    dm.bind_value(app.storage.user, "dark")
    return dm


def dark_toggle_button(dm: ui.dark_mode):
    return (
        ui.button(icon="dark_mode", on_click=lambda: dm.toggle())
        .props("flat round dense color=grey-6")
        .tooltip("Toggle dark mode")
    )


def ai_pretranslate_enabled() -> bool:
    """Per-session toggle: should AI auto-draft run for new segments?
    Obeyed only when the master switch is also on."""
    if not ai_master_enabled():
        return False
    return bool(app.storage.user.get("ai_pretranslate", True))


def set_ai_pretranslate(value: bool) -> None:
    app.storage.user["ai_pretranslate"] = bool(value)


def ai_master_enabled() -> bool:
    """Master kill switch for AI/LLM features.
    Stored in app.storage.general so it persists across browser sessions.
    When off, the LLM model is never loaded and all AI controls are disabled."""
    return bool(app.storage.general.get("ai_master_enabled", True))


def set_ai_master_enabled(value: bool) -> None:
    app.storage.general["ai_master_enabled"] = bool(value)


# Structural CSS for the dual-layer ghost-text editor. The overlay (a styled
# HTML span) must align pixel-perfectly with the transparent native textarea
# so the visible caret lands on the visible text. To do that the typography
# of both layers has to be identical *and* unmodifiable by the browser's
# default font-smoothing / ligature handling — even a single sub-pixel of
# kerning drift produces a 1-px caret-offset that translators hate.
SHARED_CSS = """
.prediction-textarea .q-field__control,
.prediction-textarea .q-field__control-container { padding: 0 !important; }
.prediction-textarea .q-field__control { background: transparent !important; min-height: 0 !important; }
.prediction-textarea .q-field__control:before,
.prediction-textarea .q-field__control:after { display: none !important; }

.prediction-textarea textarea,
.ghost-prediction-overlay {
    padding: 16px !important;
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
    font-size: 16px !important;
    line-height: 1.625 !important;
    letter-spacing: normal !important;
    word-spacing: normal !important;
    text-rendering: optimizeSpeed !important;
    font-variant-ligatures: none !important;
    font-feature-settings: "liga" 0 !important;
    -webkit-font-smoothing: antialiased !important;
    -moz-osx-font-smoothing: grayscale !important;
    box-sizing: border-box !important;
}
.prediction-textarea textarea {
    background: transparent !important;
    color: transparent !important;
    caret-color: var(--q-primary) !important;
    word-wrap: break-word !important;
    word-break: break-word !important;
}
.ghost-prediction-overlay {
    white-space: pre-wrap !important;
    word-wrap: break-word !important;
    word-break: break-word !important;
}
.ghost-prediction-overlay .ghost-fragment { opacity: 0.55; }
"""
