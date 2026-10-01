"""Persistent read-state tracking for Librus dashboard items."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.acknowledgements"

ACK_CATEGORIES = (
    "oceny",
    "terminarz",
    "wiadomosci",
    "zadania",
    "zachowanie",
    "uwagi",
)

_SOURCE_SECTION = {
    "oceny": "oceny",
    "terminarz": "terminarz",
    "wiadomosci": "wiadomosci",
    "zadania": "zadania",
    "zachowanie": "zachowanie_biezace",
    "uwagi": "uwagi",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(*parts: Any) -> str:
    raw = "\x1f".join(_text(part) for part in parts)
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def item_identifier(category: str, item: dict[str, Any], *, subject: str = "") -> str:
    """Return a stable, privacy-safe identifier for one Librus item."""
    href = _text(item.get("href"))

    if category == "oceny":
        if href:
            return _fingerprint("grade", href)
        return _fingerprint(
            "grade",
            subject or item.get("subject") or item.get("przedmiot"),
            item.get("date") or item.get("data"),
            item.get("grade") or item.get("ocena_surowa") or item.get("ocena"),
            item.get("category") or item.get("kategoria"),
            item.get("teacher") or item.get("nauczyciel"),
            item.get("semester") or item.get("semestr"),
        )

    if category == "wiadomosci":
        if href:
            return _fingerprint("message", href)
        return _fingerprint(
            "message",
            item.get("author") or item.get("nadawca"),
            item.get("title") or item.get("temat"),
            item.get("date") or item.get("data"),
        )

    if category == "terminarz":
        if href:
            return _fingerprint("schedule", href)
        return _fingerprint(
            "schedule",
            item.get("data"),
            item.get("godzina"),
            item.get("tytul"),
            item.get("przedmiot"),
        )

    if category == "zadania":
        if href:
            return _fingerprint("homework", href)
        return _fingerprint(
            "homework",
            item.get("przedmiot"),
            item.get("termin"),
            item.get("kategoria"),
            item.get("nauczyciel"),
        )

    if category == "zachowanie":
        return _fingerprint(
            "behavior",
            item.get("data"),
            item.get("wartosc") or item.get("stan"),
            item.get("kategoria"),
            item.get("nauczyciel"),
            item.get("semestr"),
        )

    if category == "uwagi":
        return _fingerprint(
            "note",
            item.get("data"),
            item.get("tresc"),
            item.get("dodal"),
            item.get("rodzaj"),
            item.get("kategoria"),
        )

    raise ValueError(f"Nieobsługiwana kategoria potwierdzenia: {category}")


def _category_items(data: dict[str, Any], category: str) -> list[tuple[str, dict[str, Any]]]:
    """Return (subject, item) pairs in the same order the UI sees them."""
    if category == "oceny":
        result: list[tuple[str, dict[str, Any]]] = []
        for subject, items in (data.get("oceny_wg_przedmiotu") or {}).items():
            for item in items or []:
                result.append((str(subject), item))
        return result

    key = {
        "wiadomosci": "wiadomosci",
        "terminarz": "terminarz",
        "zadania": "zadania",
        "zachowanie": "zachowanie_biezace",
        "uwagi": "uwagi",
    }[category]
    return [("", item) for item in (data.get(key) or [])]


class LibrusAcknowledgements:
    """Keep read acknowledgements inside Home Assistant storage, not helpers."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY}.{entry_id}",
        )
        self._read: dict[str, set[str]] = {category: set() for category in ACK_CATEGORIES}
        self._initialized: set[str] = set()

    async def async_load(self) -> None:
        raw = await self._store.async_load() or {}
        self._initialized = {
            str(category)
            for category in raw.get("initialized", [])
            if category in ACK_CATEGORIES
        }
        stored = raw.get("read", {})
        if isinstance(stored, dict):
            for category in ACK_CATEGORIES:
                values = stored.get(category, [])
                if isinstance(values, list):
                    self._read[category] = {str(value) for value in values if value}

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "initialized": sorted(self._initialized),
                "read": {
                    category: sorted(values)
                    for category, values in self._read.items()
                },
            }
        )

    def is_read(self, category: str, identifier: str) -> bool:
        return identifier in self._read.get(category, set())

    async def async_prepare(self, data: dict[str, Any]) -> dict[str, Any]:
        """Baseline each section once, then decorate every item with read state."""
        stale = set(data.get("nieodswiezone_sekcje") or [])
        changed = False

        for category in ACK_CATEGORIES:
            if category == "wiadomosci":
                # Librus itself has a real unread flag. We still keep an ID on
                # messages, but do not baseline that external read state here.
                continue
            section = _SOURCE_SECTION[category]
            if category in self._initialized or section in stale:
                continue

            current = {
                item_identifier(category, item, subject=subject)
                for subject, item in _category_items(data, category)
            }
            self._read[category].update(current)
            self._initialized.add(category)
            changed = True

        if changed:
            await self._async_save()

        return self.decorate_data(data)

    def decorate_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return a copy with stable IDs and persistent unread markers."""
        result = deepcopy(data)

        grouped = result.get("oceny_wg_przedmiotu") or {}
        for subject, items in grouped.items():
            for item in items or []:
                identifier = item_identifier("oceny", item, subject=str(subject))
                read = self.is_read("oceny", identifier)
                item["id"] = identifier
                item["odczytana"] = read
                item["nieodczytana"] = not read
                item["jest_nowa"] = not read

        for category, key, recent_key in (
            ("zachowanie", "zachowanie_biezace", "jest_nowy"),
            ("uwagi", "uwagi", "jest_nowa"),
            ("zadania", "zadania", None),
            ("terminarz", "terminarz", None),
        ):
            for item in result.get(key) or []:
                identifier = item_identifier(category, item)
                read = self.is_read(category, identifier)
                item["id"] = identifier
                item["odczytana"] = read
                item["nieodczytana"] = not read
                if recent_key:
                    item[recent_key] = not read

        # Messages have a real unread state in Librus, but a local explicit
        # acknowledgement is allowed to win. This keeps a message read after
        # the user confirms it even if Librus updates its unread flag slowly.
        for item in result.get("wiadomosci") or []:
            identifier = item_identifier("wiadomosci", item)
            librus_unread = bool(item.get("unread", False))
            read = self.is_read("wiadomosci", identifier) or not librus_unread
            item["id"] = identifier
            item["unread_librus"] = librus_unread
            item["unread"] = not read
            item["odczytana"] = read
            item["nieodczytana"] = not read
            item["jest_nowa"] = not read

        return result

    async def async_mark_from_data(
        self,
        category: str,
        data: dict[str, Any],
        *,
        index: int | None = None,
    ) -> None:
        if category not in ACK_CATEGORIES:
            raise ValueError(f"Nieobsługiwana kategoria potwierdzenia: {category}")

        items = _category_items(data, category)
        if index is None:
            selected = items
        elif 0 <= index < len(items):
            selected = [items[index]]
        else:
            raise ValueError(f"Pozycja {index} nie istnieje w kategorii {category}")

        identifiers = {
            item_identifier(category, item, subject=subject)
            for subject, item in selected
        }
        if not identifiers:
            return

        was_initialized = category in self._initialized
        before = len(self._read[category])
        self._read[category].update(identifiers)
        self._initialized.add(category)
        if len(self._read[category]) != before or not was_initialized:
            await self._async_save()
