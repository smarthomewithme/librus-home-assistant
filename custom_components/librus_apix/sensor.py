"""Czujniki integracji Librus Synergia."""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import voluptuous as vol
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_MESSAGE_INDEX,
    ATTR_SCHEDULE_INDEX,
    DOMAIN,
    SERVICE_FETCH_MESSAGE_CONTENT,
    SERVICE_FETCH_SCHEDULE_CONTENT,
)
from .coordinator import LibrusDataUpdateCoordinator
from .entity import librus_device_info
from .timetable import (
    active_lessons,
    first_active_lesson,
    lessons_by_date,
    lessons_for_date,
    next_active_lesson,
    timetable_hours,
)
from .timetable_coordinator import LibrusTimetableCoordinator

_MESSAGE_SERVICE_REGISTERED = "_message_service_registered"
_SCHEDULE_SERVICE_REGISTERED = "_schedule_service_registered"


def _srednia_ocen(oceny: List[Dict]) -> Optional[float]:
    """Oblicz srednia ocen z listy ocen."""
    wartosci = []
    for g in oceny:
        grade_str = str(g.get("ocena", ""))
        try:
            base = float(grade_str[0])
            if len(grade_str) > 1:
                if "+" in grade_str:
                    base += 0.5
                elif "-" in grade_str:
                    base -= 0.25
            wartosci.append(base)
        except (ValueError, IndexError):
            continue
    return round(sum(wartosci) / len(wartosci), 2) if wartosci else None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Dodaj czujniki jednego konta Librus."""
    runtime = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = runtime["main_coordinator"]
    timetable_coordinator = runtime["timetable_coordinator"]

    messages_sensor = LibrusWiadomosciSensor(coordinator, config_entry)
    schedule_sensor = LibrusTerminarzSensor(coordinator, config_entry)

    entities: list[SensorEntity] = [
        LibrusUczenSensor(coordinator, config_entry),
        LibrusSzczesliwyNumerekSensor(coordinator, config_entry),
        LibrusOcenySensor(coordinator, config_entry),
        LibrusZachowanieSensor(coordinator, config_entry),
        LibrusZachowanieBiezaceSensor(coordinator, config_entry),
        LibrusUwagiSensor(coordinator, config_entry),
        LibrusSzczegolneOsiagnieciaSensor(coordinator, config_entry),
        messages_sensor,
        LibrusZadaniaSensor(coordinator, config_entry),
        schedule_sensor,
        LibrusFrekwencjaSensor(coordinator, config_entry),
        LibrusOgloszeniaSensor(coordinator, config_entry),
        LibrusSredniaOcenSensor(coordinator, config_entry),
        LibrusPlanLekcjiSensor(timetable_coordinator, config_entry),
        LibrusNastepnaLekcjaSensor(timetable_coordinator, config_entry),
        LibrusStatusSensor(coordinator, timetable_coordinator, config_entry),
        LibrusOstatniaAktualizacjaSensor(coordinator, config_entry),
        LibrusNajblizszySprawdzianSensor(coordinator, config_entry),
        LibrusNajblizszeZadanieSensor(coordinator, config_entry),
    ]
    async_add_entities(entities)

    added_subjects: set[str] = set()

    @callback
    def add_new_subject_entities() -> None:
        """Dodaj sensory przedmiotów, które pojawiły się po starcie HA."""
        subjects = (coordinator.data or {}).get("oceny_wg_przedmiotu", {})
        new_subjects = [subject for subject in subjects if subject not in added_subjects]
        if not new_subjects:
            return
        added_subjects.update(new_subjects)
        async_add_entities(
            [
                entity
                for subject in new_subjects
                for entity in (
                    LibrusPrzedmiotSensor(coordinator, subject, config_entry),
                    LibrusSredniaPrzedmiotuSensor(
                        coordinator, subject, config_entry
                    ),
                )
            ]
        )

    add_new_subject_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(add_new_subject_entities))

    # Akcja encji działa wyłącznie na wskazanym sensorze wiadomości. Dzięki
    # indeksowi użytkownik nie może podać dowolnego adresu URL do klienta.
    if not hass.data[DOMAIN].get(_MESSAGE_SERVICE_REGISTERED):
        platform = entity_platform.async_get_current_platform()
        platform.async_register_entity_service(
            SERVICE_FETCH_MESSAGE_CONTENT,
            {
                vol.Required(ATTR_MESSAGE_INDEX): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=4)
                )
            },
            "async_fetch_message_content",
        )
        hass.data[DOMAIN][_MESSAGE_SERVICE_REGISTERED] = True

    # Terminarz ma taki sam model obsługi jak wiadomości: karta wskazuje indeks,
    # a sensor udostępnia dopiero wybrany wpis w osobnych atrybutach podglądu.
    if not hass.data[DOMAIN].get(_SCHEDULE_SERVICE_REGISTERED):
        platform = entity_platform.async_get_current_platform()
        platform.async_register_entity_service(
            SERVICE_FETCH_SCHEDULE_CONTENT,
            {
                vol.Required(ATTR_SCHEDULE_INDEX): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=4)
                )
            },
            "async_fetch_schedule_content",
        )
        hass.data[DOMAIN][_SCHEDULE_SERVICE_REGISTERED] = True


def _device_info(
    coordinator: LibrusDataUpdateCoordinator,
    config_entry: ConfigEntry,
) -> dict[str, Any]:
    """Zwróć wspólne informacje urządzenia z nazwą ucznia."""
    student = (coordinator.data or {}).get("student_info")
    student_name = getattr(student, "name", None)
    return librus_device_info(config_entry, student_name)


class LibrusUczenSensor(CoordinatorEntity, SensorEntity):
    """Czujnik z informacjami o uczniu."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Informacje o uczniu"
        self._attr_unique_id = f"{config_entry.entry_id}_uczen"
        self._attr_icon = "mdi:account-school"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> Optional[str]:
        info = (self.coordinator.data or {}).get("student_info")
        return info.name if info else None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        info = (self.coordinator.data or {}).get("student_info")
        if not info:
            return {}
        return {
            "klasa": info.class_name,
            "numer_w_klasie": info.number,
            "numer_w_dzienniku": info.number,
            "wychowawca": info.tutor,
            "szkola": info.school,
            "szczesliwy_numerek": info.lucky_number,
        }


class LibrusSzczesliwyNumerekSensor(CoordinatorEntity, SensorEntity):
    """Czujnik ze szczesliwym numerkiem dnia."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Szczesliwy numerek"
        self._attr_unique_id = f"{config_entry.entry_id}_szczesliwy_numerek"
        self._attr_icon = "mdi:numeric"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> Any:
        info = (self.coordinator.data or {}).get("student_info")
        return info.lucky_number if info else None


class LibrusOcenySensor(CoordinatorEntity, SensorEntity):
    """Czujnik z wszystkimi ocenami pogrupowanymi wedlug przedmiotow."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Oceny"
        self._attr_unique_id = f"{config_entry.entry_id}_oceny"
        self._attr_icon = "mdi:school"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("oceny", []))

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        data = self.coordinator.data or {}
        oceny_wg_przedmiotu = data.get("oceny_wg_przedmiotu", {})
        sa_nowe = any(
            g["jest_nowa"]
            for grades in oceny_wg_przedmiotu.values()
            for g in grades
        )
        return {
            "oceny_wg_przedmiotu": oceny_wg_przedmiotu,
            "liczba_ocen": len((self.coordinator.data or {}).get("oceny", [])),
            "liczba_przedmiotow": len(oceny_wg_przedmiotu),
            "sa_nowe_oceny": sa_nowe,
            "semestr": data.get("semestr_biezacy"),
        }


def _parse_librus_date(value: str) -> Optional[datetime]:
    """Spróbuj odczytać datę wpisu zwróconą przez Librusa."""
    raw = str(value or "").strip()
    for fmt in (
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


class LibrusZachowanieSensor(CoordinatorEntity, SensorEntity):
    """Czujnik oceny/stanu zachowania z ocen opisowych Librusa."""

    def __init__(
        self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Zachowanie klasyfikacyjne"
        self._attr_unique_id = f"{config_entry.entry_id}_zachowanie"
        self._attr_icon = "mdi:account-star"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    def _entries(self) -> list[dict[str, Any]]:
        return list((self.coordinator.data or {}).get("zachowanie", []))

    def _latest_entry(self) -> Optional[dict[str, Any]]:
        entries = self._entries()
        if not entries:
            return None

        dated = [
            (parsed, index, entry)
            for index, entry in enumerate(entries)
            if (parsed := _parse_librus_date(str(entry.get("data", "")))) is not None
        ]
        if dated:
            return max(dated, key=lambda item: (item[0], item[1]))[2]
        return entries[-1]

    @property
    def native_value(self) -> Optional[str]:
        entries = self._entries()
        if not entries:
            return None

        latest = self._latest_entry()
        if latest and str(latest.get("stan", "")).strip():
            return str(latest["stan"]).strip()

        # Nie każdy wariant Librusa ustawia datę przy ocenie opisowej. Jeśli
        # najnowszy rekord jest wyłącznie opisem, szukamy ostatniego jawnego
        # stanu zamiast wymyślać wartość sensora.
        for entry in reversed(entries):
            state = str(entry.get("stan", "")).strip()
            if state:
                return state
        return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        entries = self._entries()
        states: list[str] = []
        for entry in entries:
            state = str(entry.get("stan", "")).strip()
            if state and state not in states:
                states.append(state)

        return {
            "stany": states,
            "wpisy": entries,
            "liczba_wpisow": len(entries),
            "najnowszy_wpis": self._latest_entry(),
            "sa_nowe_wpisy": any(entry.get("jest_nowy", False) for entry in entries),
            "semestr": (self.coordinator.data or {}).get("semestr_biezacy"),
        }


class LibrusZachowanieBiezaceSensor(CoordinatorEntity, SensorEntity):
    """Bieżące wpisy/uwagi z przedmiotu Zachowanie."""

    def __init__(
        self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Zachowanie bieżące"
        # Zachowujemy dotychczasowy unique_id, żeby aktualizacja nie tworzyła
        # użytkownikowi drugiej encji i nie psuła istniejących automatyzacji.
        # Home Assistant zachowuje własny entity_id z rejestru encji, więc
        # integracja nie dokleja tutaj nazw pomieszczeń/stref.
        self._attr_unique_id = f"{config_entry.entry_id}_przedmiot_zachowanie"
        self._attr_icon = "mdi:account-alert"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    def _entries(self) -> list[dict[str, Any]]:
        return list((self.coordinator.data or {}).get("zachowanie_biezace", []))

    @property
    def native_value(self) -> int:
        # Tak samo jak sensor Oceny: stan = liczba wpisów, a szczegóły są
        # czytelnie zebrane w jednym atrybucie listowym.
        return len(self._entries())

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        entries = self._entries()
        return {
            "wpisy": entries,
            "liczba_wpisow": len(entries),
            "sa_nowe_wpisy": any(entry.get("jest_nowy", False) for entry in entries),
            "semestr": (self.coordinator.data or {}).get("semestr_biezacy"),
        }


class LibrusUwagiSensor(CoordinatorEntity, SensorEntity):
    """Dedykowane uwagi z menu ``Uczeń → Uwagi``."""

    def __init__(
        self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Uwagi"
        self._attr_unique_id = f"{config_entry.entry_id}_uwagi"
        self._attr_icon = "mdi:message-alert-outline"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    def _entries(self) -> list[dict[str, Any]]:
        return list((self.coordinator.data or {}).get("uwagi", []))

    @property
    def native_value(self) -> int:
        return len(self._entries())

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        entries = self._entries()
        return {
            "wpisy": entries,
            "liczba_wpisow": len(entries),
            "sa_nowe_wpisy": any(item.get("jest_nowa", False) for item in entries),
        }


class LibrusSzczegolneOsiagnieciaSensor(CoordinatorEntity, SensorEntity):
    """Szczególne osiągnięcia ucznia z dedykowanej strony Librusa."""

    def __init__(
        self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Szczególne osiągnięcia"
        self._attr_unique_id = f"{config_entry.entry_id}_szczegolne_osiagniecia"
        self._attr_icon = "mdi:trophy-outline"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    def _entries(self) -> list[dict[str, Any]]:
        return list((self.coordinator.data or {}).get("szczegolne_osiagniecia", []))

    @property
    def native_value(self) -> int:
        return len(self._entries())

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        entries = self._entries()
        return {
            "wpisy": entries,
            "liczba_wpisow": len(entries),
            "status": "brak_osiagniec" if not entries else "sa_osiagniecia",
        }


class LibrusPrzedmiotSensor(CoordinatorEntity, SensorEntity):
    """Czujnik z ocenami dla konkretnego przedmiotu."""

    def __init__(
        self,
        coordinator: LibrusDataUpdateCoordinator,
        subject: str,
        config_entry: ConfigEntry,
    ) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._subject = subject
        safe_name = subject.lower().replace(" ", "_").replace("/", "_")
        self._attr_has_entity_name = False
        self._attr_name = subject
        self._attr_unique_id = f"{config_entry.entry_id}_przedmiot_{safe_name}"
        self._attr_icon = "mdi:book-open-variant"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> Optional[str]:
        oceny = (self.coordinator.data or {}).get("oceny_wg_przedmiotu", {}).get(self._subject, [])
        if not oceny:
            return None
        return ", ".join(g["ocena"] for g in oceny)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        oceny = (self.coordinator.data or {}).get("oceny_wg_przedmiotu", {}).get(self._subject, [])
        if not oceny:
            return {}

        srednia = _srednia_ocen(oceny)

        # Najnowsza ocena wg daty
        najnowsza: Optional[Dict] = None
        najnowsza_data: Optional[date] = None
        for g in oceny:
            for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                try:
                    d = datetime.strptime(g["data"].strip(), fmt).date()
                    if najnowsza_data is None or d > najnowsza_data:
                        najnowsza_data = d
                        najnowsza = g
                    break
                except ValueError:
                    continue

        return {
            "oceny": oceny,
            "lista_ocen": ", ".join(g["ocena"] for g in oceny),
            "srednia": srednia,
            "najnowsza_ocena": najnowsza,
            "sa_nowe_oceny": any(g["jest_nowa"] for g in oceny),
        }


class LibrusSredniaOcenSensor(CoordinatorEntity, SensorEntity):
    """Czujnik ze srednia wszystkich ocen biezacego semestru (do wykresu)."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Srednia ocen"
        self._attr_unique_id = f"{config_entry.entry_id}_srednia_ocen"
        self._attr_icon = "mdi:chart-line"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = None

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> Optional[float]:
        data = self.coordinator.data or {}
        wszystkie = [
            g
            for oceny in data.get("oceny_wg_przedmiotu", {}).values()
            for g in oceny
        ]
        return _srednia_ocen(wszystkie)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        data = self.coordinator.data or {}
        srednie_przedmiotow = {
            subject: _srednia_ocen(oceny)
            for subject, oceny in data.get("oceny_wg_przedmiotu", {}).items()
            if _srednia_ocen(oceny) is not None
        }
        return {
            "srednie_wg_przedmiotow": srednie_przedmiotow,
            "semestr": data.get("semestr_biezacy"),
        }


class LibrusSredniaPrzedmiotuSensor(CoordinatorEntity, SensorEntity):
    """Czujnik ze srednia ocen dla konkretnego przedmiotu (do wykresu)."""

    def __init__(
        self,
        coordinator: LibrusDataUpdateCoordinator,
        subject: str,
        config_entry: ConfigEntry,
    ) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._subject = subject
        safe_name = subject.lower().replace(" ", "_").replace("/", "_")
        self._attr_has_entity_name = False
        self._attr_name = f"Srednia {subject}"
        self._attr_unique_id = f"{config_entry.entry_id}_srednia_{safe_name}"
        self._attr_icon = "mdi:chart-bar"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = None

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> Optional[float]:
        oceny = (self.coordinator.data or {}).get("oceny_wg_przedmiotu", {}).get(self._subject, [])
        return _srednia_ocen(oceny)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        oceny = (self.coordinator.data or {}).get("oceny_wg_przedmiotu", {}).get(self._subject, [])
        return {
            "przedmiot": self._subject,
            "lista_ocen": ", ".join(g["ocena"] for g in oceny),
            "liczba_ocen": len(oceny),
        }


class LibrusTerminarzSensor(CoordinatorEntity, SensorEntity):
    """Czujnik z nadchodzacymi zdarzeniami z kalendarza Librusa (biezacy + nastepny miesiac)."""

    # Wybrany wpis jest tylko stanem podglądu karty i nie powinien trafiać do
    # Recordera. Pełna lista terminarza pozostaje dostępna jak wcześniej.
    _unrecorded_attributes = frozenset(
        {
            "wybrane_zdarzenie",
            "status_tresci",
            "blad_tresci",
            "indeks_wybranego_zdarzenia",
        }
    )

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Terminarz"
        self._attr_unique_id = f"{config_entry.entry_id}_terminarz"
        self._attr_icon = "mdi:calendar-month"
        self._selected_event: Optional[Dict[str, Any]] = None
        self._selected_event_index: Optional[int] = None
        self._selected_event_key: Optional[str] = None
        self._content_status = "nie_wybrano"
        self._content_error: Optional[str] = None

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("terminarz", []))

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        terminarz = (self.coordinator.data or {}).get("terminarz", [])
        visible_events = terminarz[:5]
        selected_index = self._selected_event_index
        if self._selected_event_key:
            selected_index = next(
                (
                    index
                    for index, event in enumerate(visible_events)
                    if self._event_key(event) == self._selected_event_key
                ),
                None,
            )
        selection_available = selected_index is not None
        typy: Dict[str, int] = {}
        for z in terminarz:
            t = z.get("tytul", "")
            typy[t] = typy.get(t, 0) + 1
        return {
            "zdarzenia": terminarz,
            "liczba_zdarzen": len(terminarz),
            "typy": typy,
            "wybrane_zdarzenie": (
                self._selected_event if selection_available else None
            ),
            "indeks_wybranego_zdarzenia": selected_index,
            "status_tresci": (
                self._content_status if selection_available else "nie_wybrano"
            ),
            "blad_tresci": self._content_error if selection_available else None,
        }

    @staticmethod
    def _event_key(event: Dict[str, Any]) -> str:
        """Zbuduj stabilny klucz wpisu, także gdy Librus nie zwróci href."""
        href = str(event.get("href", "") or "").strip()
        if href:
            return href
        return "|".join(
            str(event.get(field, "") or "").strip()
            for field in ("data", "godzina", "tytul", "przedmiot")
        )

    @staticmethod
    def _event_content(
        event: Dict[str, Any], details: Dict[str, Any] | None = None
    ) -> str:
        """Wyciągnij tekst wpisany przez nauczyciela bez zmiany jego pisowni."""
        details = details if details is not None else event.get("szczegoly") or {}
        candidates: list[Any] = []
        if isinstance(details, dict):
            # Nazwy pól różnią się między szkołami, dlatego dopasowanie jest
            # nieczułe na wielkość liter. Sama wartość pozostaje nietknięta.
            candidates.extend(
                value
                for key, value in details.items()
                if any(
                    label in str(key).casefold()
                    for label in ("opis", "treść", "tresc")
                )
            )
        elif isinstance(details, str):
            candidates.append(details)
        candidates.extend(event.get(key) for key in ("opis", "tresc"))
        for value in candidates:
            text = str(value or "").strip()
            if text.lower() not in {"", "unknown", "unavailable", "none"}:
                return text
        return "Brak dodatkowej treści od nauczyciela."

    async def async_fetch_schedule_content(self, indeks: int) -> None:
        """Otwórz jeden z pięciu wpisów widocznych na karcie terminarza."""
        events = (self.coordinator.data or {}).get("terminarz", [])[:5]
        if indeks < 0 or indeks >= len(events):
            raise HomeAssistantError(
                f"Wydarzenie o indeksie {indeks} nie jest już dostępne na liście."
            )

        event = events[indeks]
        href = str(event.get("href", "") or "").strip()
        self._selected_event_index = indeks
        self._selected_event_key = self._event_key(event)
        self._selected_event = {
            "tytul": str(event.get("tytul", "") or "").strip(),
            "przedmiot": str(event.get("przedmiot", "") or "").strip(),
            "data": str(event.get("data", "") or "").strip(),
            "godzina": str(event.get("godzina", "") or "").strip(),
            "numer_lekcji": event.get("numer_lekcji"),
            "tresc": "",
            "szczegoly": {},
        }
        self._content_status = "pobieranie"
        self._content_error = None
        self.async_write_ha_state()

        details = (
            await self.coordinator.client.async_get_schedule_content(href)
            if href
            else None
        )
        cached_details = event.get("szczegoly")
        usable_details = details if details is not None else cached_details
        content = self._event_content(event, usable_details)
        if (
            details is not None
            and content == "Brak dodatkowej treści od nauczyciela."
        ):
            content = self._event_content(event, cached_details)

        self._selected_event["tresc"] = content
        self._selected_event["szczegoly"] = (
            usable_details if isinstance(usable_details, dict) else {}
        )
        self._content_status = "gotowa"
        self._content_error = None
        self.async_write_ha_state()


class LibrusZadaniaSensor(CoordinatorEntity, SensorEntity):
    """Czujnik z nadchodzacymi zadaniami i sprawdzianami (30 dni do przodu)."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Zadania"
        self._attr_unique_id = f"{config_entry.entry_id}_zadania"
        self._attr_icon = "mdi:calendar-check"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("zadania", []))

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        zadania = (self.coordinator.data or {}).get("zadania", [])
        kategorie: Dict[str, int] = {}
        for z in zadania:
            k = z.get("kategoria", "")
            kategorie[k] = kategorie.get(k, 0) + 1
        return {
            "zadania": zadania,
            "liczba_zadan": len(zadania),
            "kategorie": kategorie,
        }


class LibrusFrekwencjaSensor(CoordinatorEntity, SensorEntity):
    """Czujnik nieobecności i spóźnień zwróconych przez Librusa."""

    def __init__(
        self,
        coordinator: LibrusDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Zainicjalizuj czujnik frekwencji."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Frekwencja"
        self._attr_unique_id = f"{config_entry.entry_id}_frekwencja"
        self._attr_icon = "mdi:account-check"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @staticmethod
    def _symbol(entry: Dict[str, Any]) -> str:
        return str(entry.get("symbol", "") or "").strip().casefold()

    @property
    def native_value(self) -> int:
        """Stan to liczba nieobecności, tak jak w dzienniku Librusa."""
        entries = (self.coordinator.data or {}).get("frekwencja", [])
        return sum(1 for entry in entries if self._symbol(entry) in {"nb", "u"})

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        entries = (self.coordinator.data or {}).get("frekwencja", [])
        absences = [
            entry for entry in entries if self._symbol(entry) in {"nb", "u"}
        ]
        late = [entry for entry in entries if self._symbol(entry) == "sp"]
        return {
            "frekwencja": entries,
            "lista_wpisow": entries,
            "liczba_wpisow": len(entries),
            "liczba_nieobecnosci": len(absences),
            "liczba_spoznien": len(late),
        }


class LibrusOgloszeniaSensor(CoordinatorEntity, SensorEntity):
    """Czujnik ogłoszeń szkolnych."""

    def __init__(
        self,
        coordinator: LibrusDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Zainicjalizuj czujnik ogłoszeń."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Ogloszenia"
        self._attr_unique_id = f"{config_entry.entry_id}_ogloszenia"
        self._attr_icon = "mdi:bulletin-board"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("ogloszenia", []))

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        announcements = (self.coordinator.data or {}).get("ogloszenia", [])
        return {
            "ogloszenia": announcements,
            "lista_ogloszen": announcements,
            "liczba_ogloszen": len(announcements),
            "najnowsze_ogloszenie": announcements[0] if announcements else None,
        }


class LibrusWiadomosciSensor(CoordinatorEntity, SensorEntity):
    """Lista wiadomości z pełną treścią pobieraną wyłącznie na żądanie."""

    # Pełna treść jest chwilowa: nie zapisuj jej ani stanu podglądu w Recorderze.
    _unrecorded_attributes = frozenset(
        {
            "wybrana_wiadomosc",
            "status_tresci",
            "blad_tresci",
            "indeks_wybranej_wiadomosci",
        }
    )

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        """Inicjalizacja."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = False
        self._attr_name = "Wiadomosci"
        self._attr_unique_id = f"{config_entry.entry_id}_wiadomosci"
        self._attr_icon = "mdi:message-text"
        self._selected_message: Optional[Dict[str, Any]] = None
        self._selected_message_index: Optional[int] = None
        self._selected_message_href: Optional[str] = None
        self._content_status = "nie_wybrano"
        self._content_error: Optional[str] = None

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> int:
        """Liczba nieprzeczytanych wiadomosci."""
        msgs = (self.coordinator.data or {}).get("wiadomosci", [])
        return sum(1 for m in msgs if m.get("unread", False))

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        msgs = (self.coordinator.data or {}).get("wiadomosci", [])[:5]
        selected_index = self._selected_message_index
        if self._selected_message_href:
            selected_index = next(
                (
                    index
                    for index, message in enumerate(msgs)
                    if message.get("href") == self._selected_message_href
                ),
                None,
            )
        return {
            "wiadomosci": [
                {
                    "nadawca": m["author"],
                    "temat": m["title"],
                    "data": m["date"],
                    "nieprzeczytana": m.get("unread", False),
                    "jest_nowa": m.get("jest_nowa", False),
                    "ma_zalacznik": m.get("has_attachment", False),
                }
                for m in msgs
            ],
            "liczba_nieprzeczytanych": sum(1 for m in msgs if m.get("unread", False)),
            "sa_nowe_wiadomosci": any(m.get("jest_nowa", False) for m in msgs),
            "wybrana_wiadomosc": self._selected_message,
            "indeks_wybranej_wiadomosci": selected_index,
            "status_tresci": self._content_status,
            "blad_tresci": self._content_error,
        }

    async def async_fetch_message_content(self, indeks: int) -> None:
        """Otwórz jedną z pięciu wiadomości widocznych w atrybutach sensora."""
        messages = (self.coordinator.data or {}).get("wiadomosci", [])[:5]
        if indeks < 0 or indeks >= len(messages):
            raise HomeAssistantError(
                f"Wiadomość o indeksie {indeks} nie jest już dostępna na liście."
            )

        header = messages[indeks]
        href = header.get("href", "")
        if not href:
            raise HomeAssistantError("Wybrana wiadomość nie ma identyfikatora Librusa.")

        self._selected_message_index = indeks
        self._selected_message_href = href
        self._selected_message = {
            "temat": str(header.get("title", "") or "").strip(),
            "nadawca": str(header.get("author", "") or "").strip(),
            "data": str(header.get("date", "") or "").strip(),
            "tresc": "",
        }
        self._content_status = "pobieranie"
        self._content_error = None
        self.async_write_ha_state()

        content = await self.coordinator.client.async_get_message_content(href)
        if content is None:
            self._content_status = "blad"
            self._content_error = (
                "Librus nie zwrócił treści wiadomości. Spróbuj ponownie później."
            )
            self.async_write_ha_state()
            return

        self._selected_message = {
            "temat": content.get("title") or self._selected_message["temat"],
            "nadawca": content.get("author") or self._selected_message["nadawca"],
            "data": content.get("date") or self._selected_message["data"],
            "tresc": content.get("content") or "Brak treści wiadomości.",
        }
        self._content_status = "gotowa"
        self._content_error = None

        self.async_write_ha_state()

        # Pobierz ponownie wyłącznie nagłówki, aby pokazać faktyczny status
        # przeczytania zwrócony przez Librusa, bez pełnego cyklu ocen i planu.
        await self.coordinator.async_refresh_messages_only()


def _lesson_start_datetime(lesson: Dict[str, Any] | None) -> datetime | None:
    """Zamien date i godzine lekcji na lokalny znacznik czasu HA."""
    if not lesson:
        return None
    try:
        value = datetime.fromisoformat(f"{lesson['date']}T{lesson['start']}")
    except (KeyError, TypeError, ValueError):
        return None
    return value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)


class LibrusPlanLekcjiSensor(
    CoordinatorEntity[LibrusTimetableCoordinator], SensorEntity
):
    """Czujnik planu lekcji z danymi dla dashboardu i Node-RED."""

    _attr_has_entity_name = True
    _attr_name = "Plan lekcji"
    _attr_icon = "mdi:timetable"

    def __init__(
        self,
        coordinator: LibrusTimetableCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Zainicjalizuj czujnik."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_timetable"

    @property
    def device_info(self) -> Dict[str, Any]:
        """Powiaz czujnik z urzadzeniem ucznia."""
        return librus_device_info(self._config_entry)

    @property
    def native_value(self) -> int:
        """Stan to liczba aktywnych lekcji dzisiaj."""
        lessons = (self.coordinator.data or {}).get("lessons", [])
        return len(lessons_for_date(lessons, dt_util.now().date(), active_only=True))

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Udostepnij plan oraz gotowe wskazniki dla automatyzacji."""
        data = self.coordinator.data or {}
        lessons = data.get("lessons", [])
        today = dt_util.now().date()
        tomorrow = today + timedelta(days=1)
        first_today = first_active_lesson(lessons, today)
        first_tomorrow = first_active_lesson(lessons, tomorrow)
        next_lesson = next_active_lesson(lessons, dt_util.now())

        return {
            "status_danych": data.get("source", "none"),
            "dane_aktualne": data.get("fresh", False),
            "ostatnia_poprawna_aktualizacja": data.get("last_successful_update"),
            "ostatnia_proba": data.get("last_attempt"),
            "ostatni_blad": data.get("last_error"),
            "tygodnie": data.get("week_starts", []),
            "tygodnie_sprawdzane": data.get("requested_week_starts", []),
            "bledy_tygodni": data.get("week_errors", []),
            "liczba_lekcji_dzis": len(
                lessons_for_date(lessons, today, active_only=True)
            ),
            "liczba_lekcji_jutro": len(
                lessons_for_date(lessons, tomorrow, active_only=True)
            ),
            "pierwsza_lekcja_dzis": first_today,
            "pierwsza_lekcja_dzis_start": (
                _lesson_start_datetime(first_today).isoformat()
                if _lesson_start_datetime(first_today)
                else None
            ),
            "pierwsza_lekcja_jutro": first_tomorrow,
            "pierwsza_lekcja_jutro_start": (
                _lesson_start_datetime(first_tomorrow).isoformat()
                if _lesson_start_datetime(first_tomorrow)
                else None
            ),
            "nastepna_lekcja": next_lesson,
            "nastepna_lekcja_start": (
                _lesson_start_datetime(next_lesson).isoformat()
                if _lesson_start_datetime(next_lesson)
                else None
            ),
            "lekcje_dzis": lessons_for_date(lessons, today),
            "lekcje_jutro": lessons_for_date(lessons, tomorrow),
            "lekcje_wg_daty": lessons_by_date(lessons),
            "aktywne_lekcje_wg_daty": lessons_by_date(
                active_lessons(lessons), active_only=True
            ),
            "godziny_lekcji": timetable_hours(lessons),
        }


class LibrusNastepnaLekcjaSensor(
    CoordinatorEntity[LibrusTimetableCoordinator], SensorEntity
):
    """Znacznik czasu najblizszej aktywnej lekcji."""

    _attr_has_entity_name = True
    _attr_name = "Nastepna lekcja"
    _attr_icon = "mdi:clock-school-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: LibrusTimetableCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Zainicjalizuj czujnik."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_next_lesson"

    @property
    def device_info(self) -> Dict[str, Any]:
        """Powiaz czujnik z urzadzeniem ucznia."""
        return librus_device_info(self._config_entry)

    @property
    def native_value(self) -> datetime | None:
        """Zwroc lokalny czas rozpoczecia najblizszej aktywnej lekcji."""
        lessons = (self.coordinator.data or {}).get("lessons", [])
        return _lesson_start_datetime(next_active_lesson(lessons, dt_util.now()))

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Dolacz szczegoly najblizszej lekcji i wiarygodnosc danych."""
        data = self.coordinator.data or {}
        lesson = next_active_lesson(data.get("lessons", []), dt_util.now())
        return {
            "lekcja": lesson,
            "status_danych": data.get("source", "none"),
            "dane_aktualne": data.get("fresh", False),
            "ostatnia_poprawna_aktualizacja": data.get("last_successful_update"),
            "ostatni_blad": data.get("last_error"),
        }


class LibrusStatusSensor(SensorEntity):
    """Jedna encja diagnostyczna danych głównych i cache planu."""

    _attr_has_entity_name = True
    _attr_name = "Status"
    _attr_icon = "mdi:heart-pulse"
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: LibrusDataUpdateCoordinator,
        timetable_coordinator: LibrusTimetableCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        self._coordinator = coordinator
        self._timetable_coordinator = timetable_coordinator
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_status"

    async def async_added_to_hass(self) -> None:
        """Odśwież stan po zmianie danych głównych lub planu."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
        self.async_on_remove(
            self._timetable_coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self._coordinator, self._config_entry)

    @property
    def native_value(self) -> str:
        if not self._coordinator.last_update_success:
            return "blad"
        data = self._coordinator.data or {}
        timetable = self._timetable_coordinator.data or {}
        if data.get("nieodswiezone_sekcje") or not timetable.get("fresh", False):
            return "ostrzezenie"
        return "ok"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        data = self._coordinator.data or {}
        timetable = self._timetable_coordinator.data or {}
        return {
            "ostatnia_poprawna_aktualizacja": data.get("ostatnia_poprawna_aktualizacja"),
            "nieodswiezone_sekcje": data.get("nieodswiezone_sekcje", []),
            "tryb_odswiezania": self._coordinator.refresh_mode,
            "interwal_odswiezania_min": self._coordinator.current_refresh_interval_minutes,
            "blad_danych_glownych": (
                str(getattr(self._coordinator, "last_exception", None) or "")
                or None
            ),
            "zrodlo_planu": timetable.get("source", "none"),
            "plan_aktualny": timetable.get("fresh", False),
            "ostatnia_poprawna_aktualizacja_planu": timetable.get("last_successful_update"),
            "blad_planu": timetable.get("last_error"),
        }


class LibrusOstatniaAktualizacjaSensor(
    CoordinatorEntity[LibrusDataUpdateCoordinator], SensorEntity
):
    """Czas ostatniego poprawnego pobrania danych głównych."""

    _attr_has_entity_name = True
    _attr_name = "Ostatnia poprawna aktualizacja"
    _attr_icon = "mdi:clock-check-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_last_successful_update"

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)

    @property
    def native_value(self) -> datetime | None:
        raw = (self.coordinator.data or {}).get("ostatnia_poprawna_aktualizacja")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw)).replace(tzinfo=dt_util.UTC)
        except ValueError:
            return None


class _LibrusNearestSensor(CoordinatorEntity[LibrusDataUpdateCoordinator], SensorEntity):
    """Wspolna baza dla encji pokazujacych najblizsza rzecz do zrobienia."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = True

    @property
    def device_info(self) -> Dict[str, Any]:
        return _device_info(self.coordinator, self._config_entry)


class LibrusNajblizszySprawdzianSensor(_LibrusNearestSensor):
    """Najbliższa kartkówka lub sprawdzian, bez dodatkowego pobierania."""

    _attr_name = "Najbliższy sprawdzian"
    _attr_icon = "mdi:calendar-star"

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_next_test"

    @property
    def _event(self) -> Dict[str, Any] | None:
        for event in (self.coordinator.data or {}).get("terminarz", []):
            title = str(event.get("tytul", "") or "").casefold()
            if "sprawdz" in title or "kartk" in title:
                return event
        return None

    @property
    def native_value(self) -> str | None:
        event = self._event
        return str(event.get("data", "") or "").strip() or None if event else None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return {"wydarzenie": self._event}


class LibrusNajblizszeZadanieSensor(_LibrusNearestSensor):
    """Najbliższe zadanie według kolejności zwróconej przez Librusa."""

    _attr_name = "Najbliższe zadanie"
    _attr_icon = "mdi:clipboard-text-clock-outline"

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_next_homework"

    @property
    def _task(self) -> Dict[str, Any] | None:
        tasks = (self.coordinator.data or {}).get("zadania", [])
        return tasks[0] if tasks else None

    @property
    def native_value(self) -> str | None:
        task = self._task
        return str(task.get("termin", "") or "").strip() or None if task else None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return {"zadanie": self._task}
