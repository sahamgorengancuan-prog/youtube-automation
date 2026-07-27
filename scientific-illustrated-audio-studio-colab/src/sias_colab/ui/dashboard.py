"""Dashboard — ipywidgets primary, plain-print forms fallback when widgets are
unavailable. Never hides a failure behind a generic message: every error panel
shows stage, provider, model, request id, recoverability, and the next action.
"""

from __future__ import annotations

from typing import Any

from ..state import STAGE_ORDER, EpisodeState
from .texts import t

try:  # pragma: no cover - environment dependent
    import ipywidgets as widgets
    from IPython.display import display

    HAS_WIDGETS = True
except Exception:  # noqa: BLE001 - any import problem means fallback mode
    widgets = None
    display = None
    HAS_WIDGETS = False


def stage_badges(state: EpisodeState) -> str:
    icon = {"PASS": "🟢", "FAIL": "🔴", "SKIPPED": "⚪", "PENDING": "🟡"}
    return "  ".join(f"{icon.get(state.status(s), '🟡')} {s}" for s in STAGE_ORDER)


def error_panel(stage: str, provider: str, model: str, request_id: str,
                recoverable: bool, recommendation: str, lang: str = "id") -> str:
    return (
        f"❌ {t('stage', lang)}: {stage}\n"
        f"   {t('provider', lang)}: {provider or '-'} | {t('model', lang)}: {model or '-'}\n"
        f"   request_id: {request_id or '-'}\n"
        f"   {t('recoverable', lang)}: {'ya/yes' if recoverable else 'tidak/no'}\n"
        f"   {t('recommended', lang)}: {recommendation}"
    )


class Dashboard:
    """Renders with widgets when available; otherwise prints the same content."""

    def __init__(self, state: EpisodeState, budget_snapshot: dict[str, Any],
                 secrets_present: dict[str, bool], run_mode: str, lang: str = "id"):
        self.state = state
        self.budget = budget_snapshot
        self.secrets = secrets_present
        self.run_mode = run_mode
        self.lang = lang

    def _lines(self) -> list[str]:
        secrets = "  ".join(f"{'🔑' if ok else '⛔'} {name}" for name, ok in self.secrets.items())
        return [
            t("welcome_title", self.lang),
            f"{t('run_mode', self.lang)}: {self.run_mode}   |   {t('episode', self.lang)}: {self.state.episode_id}",
            f"{t('secrets', self.lang)}: {secrets}",
            f"{t('budget_used', self.lang)}: images {self.budget.get('image_calls', 0)}/{self.budget.get('max_image_calls', 0)}"
            f" · vision {self.budget.get('vision_calls', 0)}/{self.budget.get('max_vision_calls', 0)}"
            f" · TTS {self.budget.get('tts_characters', 0)}/{self.budget.get('max_tts_characters', 0)}"
            f" · HF {self.budget.get('higgsfield_calls', 0)}/{self.budget.get('max_higgsfield_calls', 0)}",
            stage_badges(self.state),
            f"➡️  {t('next_action', self.lang)}: {self.state.next_recommended_action()}",
        ]

    def render(self) -> Any:
        lines = self._lines()
        if HAS_WIDGETS:
            box = widgets.VBox([widgets.HTML(f"<b>{lines[0]}</b>")] +
                               [widgets.HTML(line.replace(" ", "&nbsp;")) for line in lines[1:]])
            display(box)
            return box
        print("\n".join(lines))
        return None


def arm_checkbox(lang: str = "id") -> Any:
    """The explicit user-action control for paid sections."""
    if HAS_WIDGETS:
        return widgets.Checkbox(value=False, description=t("arm_warning", lang), indent=False,
                                layout=widgets.Layout(width="95%"))

    class _Fallback:
        value = False

        def __repr__(self) -> str:
            return f"[forms fallback] {t('arm_warning', lang)} -> set .value = True deliberately"

    return _Fallback()
