"""Coordinator extensions for persistent read state and richer grade display."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .acknowledgements import LibrusAcknowledgements
from .coordinator import LibrusDataUpdateCoordinator, _is_recent
from .smart_client import SmartHomeLibrusApiClient


class SmartHomeLibrusDataUpdateCoordinator(LibrusDataUpdateCoordinator):
    """Add stable item IDs and persistent acknowledgements to Librus data."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SmartHomeLibrusApiClient,
        config_entry: ConfigEntry,
        acknowledgements: LibrusAcknowledgements,
    ) -> None:
        self.acknowledgements = acknowledgements
        super().__init__(hass, client, config_entry)

    async def _async_update_data(self) -> dict[str, Any]:
        result = await super()._async_update_data()

        # Rebuild the dashboard-facing grade structure from the richer records
        # returned by SmartHomeLibrusApiClient. This preserves a stable href,
        # exposes point-test details, and keeps the raw Librus marker separately.
        grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for grade in result.get("oceny", []) or []:
            if grade.get("type") in {"behavior", "behavior_current"}:
                continue
            raw_grade = str(grade.get("grade", "") or "").strip()
            display_grade = str(grade.get("display_grade", "") or raw_grade).strip()
            grouped[str(grade.get("subject", "") or "")].append(
                {
                    "ocena": display_grade,
                    "ocena_surowa": raw_grade,
                    "data": grade.get("date", ""),
                    "kategoria": grade.get("category", ""),
                    "komentarz": grade.get("comment", ""),
                    "nauczyciel": grade.get("teacher", ""),
                    "semestr": grade.get("semester"),
                    "href": grade.get("href", ""),
                    "szczegoly_oceny": grade.get("grade_details", ""),
                    "szczegoly": grade.get("details", {}),
                    "jest_nowa": _is_recent(str(grade.get("date", ""))),
                }
            )
        result["oceny_wg_przedmiotu"] = dict(grouped)

        return await self.acknowledgements.async_prepare(result)

    async def async_acknowledge(
        self,
        category: str,
        *,
        index: int | None = None,
    ) -> None:
        """Persist acknowledgement and update entities immediately."""
        current = deepcopy(self.data or {})
        await self.acknowledgements.async_mark_from_data(
            category,
            current,
            index=index,
        )
        self.async_set_updated_data(self.acknowledgements.decorate_data(current))
