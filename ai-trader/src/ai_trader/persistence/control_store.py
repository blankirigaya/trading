"""Read/write the persisted operator controls.

The universe mode is a deliberate operator choice, not state that resets with a
session restart, so it lives beside the trade history rather than in memory.
"""

from datetime import UTC, datetime

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ai_trader.application.universe import DEFAULT_UNIVERSE_MODE, normalize_mode
from ai_trader.persistence.control_models import RuntimeSettingRow
from ai_trader.persistence.retry import with_sqlite_retry

UNIVERSE_MODE_KEY = "universe_mode"
CAPITAL_CAP_DISABLED_KEY = "capital_cap_disabled"
POSITION_COUNT_CAP_DISABLED_KEY = "position_count_cap_disabled"
TIER1_NEWS_ENABLED_KEY = "tier1_news_enabled"
NEWS_ANALYSIS_ENABLED_KEY = "news_analysis_enabled"
SONNET_ESCALATION_ENABLED_KEY = "sonnet_escalation_enabled"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class ControlStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_universe_mode_sync(self, default: str = DEFAULT_UNIVERSE_MODE) -> str:
        with Session(self.engine) as session:
            row = session.get(RuntimeSettingRow, UNIVERSE_MODE_KEY)
        if row is None:
            return normalize_mode(default)
        try:
            return normalize_mode(row.value)
        except ValueError:
            # A corrupt/legacy value must not stop the backend from booting.
            return normalize_mode(default)

    async def get_universe_mode(self, default: str = DEFAULT_UNIVERSE_MODE) -> str:
        return await with_sqlite_retry(
            "get_universe_mode", self.get_universe_mode_sync, default
        )

    async def set_universe_mode(self, mode: str) -> str:
        normalized = normalize_mode(mode)
        await with_sqlite_retry("set_universe_mode", self._set_universe_mode_sync, normalized)
        return normalized

    def _set_universe_mode_sync(self, mode: str) -> None:
        self._set_sync(UNIVERSE_MODE_KEY, mode)

    def get_capital_cap_disabled_sync(self, default: bool = False) -> bool:
        with Session(self.engine) as session:
            row = session.get(RuntimeSettingRow, CAPITAL_CAP_DISABLED_KEY)
        if row is None:
            return default
        return row.value.strip().lower() in _TRUE_VALUES

    async def get_capital_cap_disabled(self, default: bool = False) -> bool:
        return await with_sqlite_retry(
            "get_capital_cap_disabled", self.get_capital_cap_disabled_sync, default
        )

    async def set_capital_cap_disabled(self, disabled: bool) -> bool:
        await with_sqlite_retry(
            "set_capital_cap_disabled", self._set_capital_cap_disabled_sync, disabled
        )
        return disabled

    def _set_capital_cap_disabled_sync(self, disabled: bool) -> None:
        self._set_sync(CAPITAL_CAP_DISABLED_KEY, "true" if disabled else "false")

    def get_position_count_cap_disabled_sync(self, default: bool = False) -> bool:
        with Session(self.engine) as session:
            row = session.get(RuntimeSettingRow, POSITION_COUNT_CAP_DISABLED_KEY)
        if row is None:
            return default
        return row.value.strip().lower() in _TRUE_VALUES

    async def get_position_count_cap_disabled(self, default: bool = False) -> bool:
        return await with_sqlite_retry(
            "get_position_count_cap_disabled",
            self.get_position_count_cap_disabled_sync,
            default,
        )

    async def set_position_count_cap_disabled(self, disabled: bool) -> bool:
        await with_sqlite_retry(
            "set_position_count_cap_disabled",
            self._set_position_count_cap_disabled_sync,
            disabled,
        )
        return disabled

    def _set_position_count_cap_disabled_sync(self, disabled: bool) -> None:
        self._set_sync(POSITION_COUNT_CAP_DISABLED_KEY, "true" if disabled else "false")

    def _get_flag_sync(self, key: str, default: bool) -> bool:
        with Session(self.engine) as session:
            row = session.get(RuntimeSettingRow, key)
        if row is None:
            return default
        return row.value.strip().lower() in _TRUE_VALUES

    async def _get_flag(self, key: str, default: bool) -> bool:
        return await with_sqlite_retry(f"get_{key}", self._get_flag_sync, key, default)

    async def _set_flag(self, key: str, enabled: bool) -> bool:
        await with_sqlite_retry(f"set_{key}", self._set_sync_flag, key, enabled)
        return enabled

    def _set_sync_flag(self, key: str, enabled: bool) -> None:
        self._set_sync(key, "true" if enabled else "false")

    def get_tier1_news_enabled_sync(self, default: bool = True) -> bool:
        return self._get_flag_sync(TIER1_NEWS_ENABLED_KEY, default)

    async def get_tier1_news_enabled(self, default: bool = True) -> bool:
        return await self._get_flag(TIER1_NEWS_ENABLED_KEY, default)

    async def set_tier1_news_enabled(self, enabled: bool) -> bool:
        return await self._set_flag(TIER1_NEWS_ENABLED_KEY, enabled)

    def get_news_analysis_enabled_sync(self, default: bool = True) -> bool:
        return self._get_flag_sync(NEWS_ANALYSIS_ENABLED_KEY, default)

    async def get_news_analysis_enabled(self, default: bool = True) -> bool:
        return await self._get_flag(NEWS_ANALYSIS_ENABLED_KEY, default)

    async def set_news_analysis_enabled(self, enabled: bool) -> bool:
        return await self._set_flag(NEWS_ANALYSIS_ENABLED_KEY, enabled)

    def get_sonnet_escalation_enabled_sync(self, default: bool = True) -> bool:
        return self._get_flag_sync(SONNET_ESCALATION_ENABLED_KEY, default)

    async def get_sonnet_escalation_enabled(self, default: bool = True) -> bool:
        return await self._get_flag(SONNET_ESCALATION_ENABLED_KEY, default)

    async def set_sonnet_escalation_enabled(self, enabled: bool) -> bool:
        return await self._set_flag(SONNET_ESCALATION_ENABLED_KEY, enabled)

    def _set_sync(self, key: str, value: str) -> None:
        with Session(self.engine) as session:
            row = session.get(RuntimeSettingRow, key)
            if row is None:
                session.add(RuntimeSettingRow(key=key, value=value, updated_at=datetime.now(UTC)))
            else:
                row.value = value
                row.updated_at = datetime.now(UTC)
            session.commit()
