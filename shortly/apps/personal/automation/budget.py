"""Budget checks separated from provider execution."""

from __future__ import annotations

from datetime import datetime, timezone

from .settings import PersonalAutomationSettings
from .store import AutomationStore, BudgetExceededError


def _period_starts() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    return day.isoformat(), month.isoformat()


def assert_budget(
    store: AutomationStore,
    settings: PersonalAutomationSettings,
    *,
    manifest_id: str,
    kind: str,
    estimated_cost_usd: float,
) -> None:
    estimated = max(0.0, estimated_cost_usd)
    summary = store.usage_summary(manifest_id)
    per_video_limit = (
        settings.image_budget_per_video_usd
        if kind == "image"
        else settings.video_budget_per_video_usd
    )
    current_kind = summary.get(f"{kind}_usd", 0.0)
    if current_kind + estimated > per_video_limit:
        raise BudgetExceededError(
            f"{kind} budget per video would exceed ${per_video_limit:.2f}"
        )
    day_start, month_start = _period_starts()
    if store.spending_since(day_start) + estimated > settings.daily_budget_usd:
        raise BudgetExceededError(
            f"daily asset budget would exceed ${settings.daily_budget_usd:.2f}"
        )
    if store.spending_since(month_start) + estimated > settings.monthly_budget_usd:
        raise BudgetExceededError(
            "monthly asset budget would exceed "
            f"${settings.monthly_budget_usd:.2f}"
        )
