import pytest

from investment_assistant.adapters.news import (
    FTNewsAdapter,
    UniversityNewsDatabaseAdapter,
)
from investment_assistant.ports.news import SubscriptionAdapterUnavailable


@pytest.mark.parametrize("adapter", [FTNewsAdapter(), UniversityNewsDatabaseAdapter()])
def test_subscription_adapters_are_disabled(adapter: object) -> None:
    assert adapter.enabled is False  # type: ignore[attr-defined]

    with pytest.raises(SubscriptionAdapterUnavailable, match="disabled"):
        adapter.fetch_recent("NVDA", 20)  # type: ignore[attr-defined]
