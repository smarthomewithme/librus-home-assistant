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
        """Dodaj sensory przedmiotÃ³w, ktÃ³re pojawiÅ‚y siÄ™ po starcie HA."""
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

    # Akcja encji dziaÅ‚a wyÅ‚Ä…cznie na wskazanym sensorze wiadomoÅ›ci. DziÄ™ki
    # indeksowi uÅ¼ytkownik nie moÅ¼e podaÄ‡ dowolnego adresu URL do klienta.
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

    # Terminarz ma taki sam model obsÅ‚ugi jak wiadomoÅ›ci: karta wskazuje indeks,
    # a sensor udostÄ™pnia dopiero wybrany wpis w osobnych atrybutach podglÄ…du.
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
    """ZwrÃ³Ä‡ wspÃ³lne informacje urzÄ…dzenia z nazwÄ… ucznia."""
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


class LibrusSzczesliwyNumerekSensor(Coordina²È="25å}¹…µ”€ôQÉÕ”(€€€}…ÑÑÉ}¹…µ”€ô€‰9…ÍÑ•Á¹„±•­©„ˆ(€€€}…ÑÑÉ}¥½¸€ô€‰µ‘¤é±½¬µÍ¡½½°µ½ÕÑ±¥¹”ˆ(€€€}…ÑÑÉ}‘•Ù¥•}±…ÍÌ€ôM•¹Í½É•Ù¥•±…ÍÌ¹Q%5MQ5@((€€€‘•˜}}¥¹¥Ñ}| (€€€€€€€Í•±˜°(€€€€€€€½½É‘¥¹…Ñ½Èè1¥‰ÉÕÍQ¥µ•Ñ…‰±•½½É‘¥¹…Ñ½È°(€€€€€€€½¹™¥}•¹ÑÉäè½¹™¥¹ÑÉä°(€€€€¤€´ø9½¹”è(€€€€€€€€ˆˆ‰i…¥¹¥©…±¥éÕ¨éÕ©¹¥¬¸ˆˆˆ(€€€€€€€ÍÕÁ•È ¤¹}}¥¹¥Ñ}|¡½½É‘¥¹…Ñ½È¤(€€€€€€€Í•±˜¹}½¹™¥}•¹ÑÉä€ô½¹™¥}•¹ÑÉä(€€€€€€€Í•±˜¹}…ÑÑÉ}Õ¹¥ÅÕ•}¥€ô˜‰í½¹™¥}•¹ÑÉä¹•¹ÑÉå}¥‘õ}¹•áÑ}±•ÍÍ½¸ˆ((€€€ÁÉ½Á•ÉÑä(€€€‘•˜‘•Ù¥•}¥¹™¼¡Í•±˜¤€´ø¥ÑmÍÑÈ°¹åtè(€€€€€€€€ˆˆ‰A½Ý¥…èéÕ©¹¥¬èÕÉé…‘é•¹¥•´Õé¹¥„¸ˆˆˆ(€€€€€€€É•ÑÕÉ¸±¥‰ÉÕÍ}‘•Ù¥•}¥¹™¼¡Í•±˜¹}½¹™¥}•¹ÑÉä¤((€€€ÁÉ½Á•ÉÑä(€€€‘•˜¹…Ñ¥Ù•}Ù…±Õ”¡Í•±˜¤€´ø‘…Ñ•Ñ¥µ”ð9½¹”è(€€€€€€€€ˆˆ‰iÝÉ½Œ±½­…±¹äé…ÌÉ½éÁ½é•¥„¹…©‰±¥éÍé•¨…­ÑåÝ¹•¨±•­©¤¸ˆˆˆ(€€€€€€€±•ÍÍ½¹Ì€ô€¡Í•±˜¹½½É‘¥¹…Ñ½È¹‘…Ñ„½Èíô¤¹•Ð ‰±•ÍÍ½¹Ìˆ°mt¤(€€€€€€€É•ÑÕÉ¸}±•ÍÍ½¹}ÍÑ…ÉÑ}‘…Ñ•Ñ¥µ”¡¹•áÑ}…Ñ¥Ù•}±•ÍÍ½¸¡±•ÍÍ½¹Ì°‘Ñ}ÕÑ¥°¹¹½Ü ¤¤¤((€€€ÁÉ½Á•ÉÑä(€€€‘•˜•áÑÉ…}ÍÑ…Ñ•}…ÑÑÉ¥‰ÕÑ•Ì¡Í•±˜¤€´ø¥ÑmÍÑÈ°¹åtè(€€€€€€€€ˆˆ‰½±…èÍéé•½±ä¹…©‰±¥éÍé•¨±•­©¤¤Ý¥…Éå½‘¹½ÍŒ‘…¹å ¸ˆˆˆ(€€€€€€€‘…Ñ„€ôÍ•±˜¹½½É‘¥¹…Ñ½È¹‘…Ñ„½Èíô(€€€€€€€±•ÍÍ½¸€ô¹•áÑ}…Ñ¥Ù•}±•ÍÍ½¸¡‘…Ñ„¹•Ð ‰±•ÍÍ½¹Ìˆ°mt¤°‘Ñ}ÕÑ¥°¹¹½Ü ¤¤(€€€€€€€É•ÑÕÉ¸ì(€€€€€€€€€€€€‰±•­©„ˆè±•ÍÍ½¸°(€€€€€€€€€€€€‰ÍÑ…ÑÕÍ}‘…¹å ˆè‘…Ñ„¹•Ð ‰Í½ÕÉ”ˆ°€‰¹½¹”ˆ¤°(€€€€€€€€€€€€‰‘…¹•}…­ÑÕ…±¹”ˆè‘…Ñ„¹•Ð ‰™É•Í ˆ°…±Í”¤°(€€€€€€€€€€€€‰½ÍÑ…Ñ¹¥…}Á½ÁÉ…Ý¹…}…­ÑÕ…±¥é…©„ˆè‘…Ñ„¹•Ð ‰±…ÍÑ}ÍÕ•ÍÍ™Õ±}ÕÁ‘…Ñ”ˆ¤°(€€€€€€€€€€€€‰½ÍÑ…Ñ¹¥}‰±…ˆè‘…Ñ„¹•Ð ‰±…ÍÑ}•ÉÉ½Èˆ¤°(€€€€€€€ô(()±…ÍÌ1¥‰ÉÕÍMÑ…ÑÕÍM•¹Í½È¡M•¹Í½É¹Ñ¥Ñä¤è(€€€€ˆˆ‰)•‘¹„•¹©„‘¥…¹½ÍÑåé¹„‘…¹å ŸÍÝ¹å ¤…¡”Á±…¹Ô¸ˆˆˆ((€€€}…ÑÑÉ}¡…Í}•¹Ñ¥Ñå}¹…µ”€ôQÉÕ”(€€€}…ÑÑÉ}¹…µ”€ô€‰MÑ…ÑÕÌˆ(€€€}…ÑÑÉ}¥½¸€ô€‰µ‘¤é¡•…ÉÐµÁÕ±Í”ˆ(€€€}…ÑÑÉ}Í¡½Õ±‘}Á½±°€ô…±Í”((€€€‘•˜}}¥¹¥Ñ}| (€€€€€€€Í•±˜°(€€€€€€€½½É‘¥¹…Ñ½Èè1¥‰ÉÕÍ…Ñ…UÁ‘…Ñ•½½É‘¥¹…Ñ½È°(€€€€€€€Ñ¥µ•Ñ…‰±•}½½É‘¥¹…Ñ½Èè1¥‰ÉÕÍQ¥µ•Ñ…‰±•½½É‘¥¹…Ñ½È°(€€€€€€€½¹™¥}•¹ÑÉäè½¹™¥¹ÑÉä°(€€€€¤€´ø9½¹”è(€€€€€€€Í•±˜¹}½½É‘¥¹…Ñ½È€ô½½É‘¥¹…Ñ½È(€€€€€€€Í•±˜¹}Ñ¥µ•Ñ…‰±•}½½É‘¥¹…Ñ½È€ôÑ¥µ•Ñ…‰±•}½½É‘¥¹…Ñ½È(€€€€€€€Í•±˜¹}½¹™¥}•¹ÑÉä€ô½¹™¥}•¹ÑÉä(€€€€€€€Í•±˜¹}…ÑÑÉ}Õ¹¥ÅÕ•}¥€ô˜‰í½¹™¥}•¹ÑÉä¹•¹ÑÉå}¥‘õ}ÍÑ…ÑÕÌˆ((€€€…Íå¹Œ‘•˜…Íå¹}…‘‘•‘}Ñ½}¡…ÍÌ¡Í•±˜¤€´ø9½¹”è(€€€€€€€€ˆˆ‰=“mÝ¥—ðÍÑ…¸Á¼éµ¥…¹¥”‘…¹å ŸÍÝ¹å ±ÕˆÁ±…¹Ô¸ˆˆˆ(€€€€€€€…Ý…¥ÐÍÕÁ•È ¤¹…Íå¹}…‘‘•‘}Ñ½}¡…ÍÌ ¤(€€€€€€€Í•±˜¹…Íå¹}½¹}É•µ½Ù” (€€€€€€€€€€€Í•±˜¹}½½É‘¥¹…Ñ½È¹…Íå¹}…‘‘}±¥ÍÑ•¹•È¡Í•±˜¹…Íå¹}ÝÉ¥Ñ•}¡…}ÍÑ…Ñ”¤(€€€€€€€€¤(€€€€€€€Í•±˜¹…Íå¹}½¹}É•µ½Ù” (€€€€€€€€€€€Í•±˜¹}Ñ¥µ•Ñ…‰±•}½½É‘¥¹…Ñ½È¹…Íå¹}…‘‘}±¥ÍÑ•¹•È¡Í•±˜¹…Íå¹}ÝÉ¥Ñ•}¡…}ÍÑ…Ñ”¤(€€€€€€€€¤((€€€ÁÉ½Á•ÉÑä(€€€‘•˜‘•Ù¥•}¥¹™¼¡Í•±˜¤€´ø¥ÑmÍÑÈ°¹åtè(€€€€€€€É•ÑÕÉ¸}‘•Ù¥•}¥¹™¼¡Í•±˜¹}½½É‘¥¹…Ñ½È°Í•±˜¹}½¹™¥}•¹ÑÉä¤((€€€ÁÉ½Á•ÉÑä(€€€‘•˜¹…Ñ¥Ù•}Ù…±Õ”¡Í•±˜¤€´øÍÑÈè(€€€€€€€¥˜¹½ÐÍ•±˜¹}½½É‘¥¹…Ñ½È¹±…ÍÑ}ÕÁ‘…Ñ•}ÍÕ•ÍÌè(€€€€€€€€€€€É•ÑÕÉ¸€‰‰±…ˆ(€€€€€€€‘…Ñ„€ôÍ•±˜¹}½½É‘¥¹…Ñ½È¹‘…Ñ„½Èíô(€€€€€€€Ñ¥µ•Ñ…‰±”€ôÍ•±˜¹}Ñ¥µ•Ñ…‰±•}½½É‘¥¹…Ñ½È¹‘…Ñ„½Èíô(€€€€€€€¥˜‘…Ñ„¹•Ð ‰¹¥•½‘ÍÝ¥•é½¹•}Í•­©”ˆ¤½È¹½ÐÑ¥µ•Ñ…‰±”¹•Ð ‰™É•Í ˆ°…±Í”¤è(€€€€€€€€€€€É•ÑÕÉ¸€‰½ÍÑÉé•é•¹¥”ˆ(€€€€€€€É•ÑÕÉ¸€‰½¬ˆ((€€€ÁÉ½Á•ÉÑä(€€€‘•˜•áÑÉ…}ÍÑ…Ñ•}…ÑÑÉ¥‰ÕÑ•Ì¡Í•±˜¤€´ø¥ÑmÍÑÈ°¹åtè(€€€€€€€‘…Ñ„€ôÍ•±˜¹}½½É‘¥¹…Ñ½È¹‘…Ñ„½Èíô(€€€€€€€Ñ¥µ•Ñ…‰±”€ôÍ•±˜¹}Ñ¥µ•Ñ…‰±•}½½É‘¥¹…Ñ½È¹‘…Ñ„½Èíô(€€€€€€€É•ÑÕÉ¸ì(€€€€€€€€€€€€‰½ÍÑ…Ñ¹¥…}Á½ÁÉ…Ý¹…}…­ÑÕ…±¥é…©„ˆè‘…Ñ„¹•Ð ‰½ÍÑ…Ñ¹¥…}Á½ÁÉ…Ý¹…}…­ÑÕ…±¥é…©„ˆ¤°(€€€€€€€€€€€€‰¹¥•½‘ÍÝ¥•é½¹•}Í•­©”ˆè‘…Ñ„¹•Ð ‰¹¥•½‘ÍÝ¥•é½¹•}Í•­©”ˆ°mt¤°(€€€€€€€€€€€€‰ÑÉå‰}½‘ÍÝ¥•é…¹¥„ˆèÍ•±˜¹}½½É‘¥¹…Ñ½È¹É•™É•Í¡}µ½‘”°(€€€€€€€€€€€€‰¥¹Ñ•ÉÝ…±}½‘ÍÝ¥•é…¹¥…}µ¥¸ˆèÍ•±˜¹}½½É‘¥¹…Ñ½È¹ÕÉÉ•¹Ñ}É•™É•Í¡}¥¹Ñ•ÉÙ…±}µ¥¹ÕÑ•Ì°(€€€€€€€€€€€€‰‰±…‘}‘…¹å¡}±½Ý¹å ˆè€ (€€€€€€€€€€€€€€€ÍÑÈ¡•Ñ…ÑÑÈ¡Í•±˜¹}½½É‘¥¹…Ñ½È°€‰±…ÍÑ}•á•ÁÑ¥½¸ˆ°9½¹”¤½È€ˆˆ¤(€€€€€€€€€€€€€€€½È9½¹”(€€€€€€€€€€€€¤°(€€€€€€€€€€€€‰éÉ½‘±½}Á±…¹ÔˆèÑ¥µ•Ñ…‰±”¹•Ð ‰Í½ÕÉ”ˆ°€‰¹½¹”ˆ¤°(€€€€€€€€€€€€‰Á±…¹}…­ÑÕ…±¹äˆèÑ¥µ•Ñ…‰±”¹•Ð ‰™É•Í ˆ°…±Í”¤°(€€€€€€€€€€€€‰½ÍÑ…Ñ¹¥…}Á½ÁÉ…Ý¹…}…­ÑÕ…±¥é…©…}Á±…¹ÔˆèÑ¥µ•Ñ…‰±”¹•Ð ‰±…ÍÑ}ÍÕ•ÍÍ™Õ±}ÕÁ‘…Ñ”ˆ¤°(€€€€€€€€€€€€‰‰±…‘}Á±…¹ÔˆèÑ¥µ•Ñ…‰±”¹•Ð ‰±…ÍÑ}•ÉÉ½Èˆ¤°(€€€€€€€ô(()±…ÍÌ1¥‰ÉÕÍ=ÍÑ…Ñ¹¥…­ÑÕ…±¥é…©…M•¹Í½È (€€€½½É‘¥¹…Ñ½É¹Ñ¥Ñåm1¥‰ÉÕÍ…Ñ…UÁ‘…Ñ•½½É‘¥¹…Ñ½Ét°M•¹Í½É¹Ñ¥Ñä(¤è(€€€€ˆˆ‰é…Ì½ÍÑ…Ñ¹¥•¼Á½ÁÉ…Ý¹•¼Á½‰É…¹¥„‘…¹å ŸÍÝ¹å ¸ˆˆˆ((€€€}…ÑÑÉ}¡…Í}•¹Ñ¥Ñå}¹…µ”€ôQÉÕ”(€€€}…ÑÑÉ}¹…µ”€ô€‰=ÍÑ…Ñ¹¥„Á½ÁÉ…Ý¹„…­ÑÕ…±¥é…©„ˆ(€€€}…ÑÑÉ}¥½¸€ô€‰µ‘¤é±½¬µ¡•¬µ½ÕÑ±¥¹”ˆ(€€€}…ÑÑÉ}‘•Ù¥•}±…ÍÌ€ôM•¹Í½É•Ù¥•±…ÍÌ¹Q%5MQ5@((€€€‘•˜}}¥¹¥Ñ}| (€€€€€€€Í•±˜°½½É‘¥¹…Ñ½Èè1¥‰ÉÕÍ…Ñ…UÁ‘…Ñ•½½É‘¥¹…Ñ½È°½¹™¥}•¹ÑÉäè½¹™¥¹ÑÉä(€€€€¤€´ø9½¹”è(€€€€€€€ÍÕÁ•È ¤¹}}¥¹¥Ñ}|¡½½É‘¥¹…Ñ½È¤(€€€€€€€Í•±˜¹}½¹™¥}•¹ÑÉä€ô½¹™¥}•¹ÑÉä(€€€€€€€Í•±˜¹}…ÑÑÉ}Õ¹¥ÅÕ•}¥€ô˜‰í½¹™¥}•¹ÑÉä¹•¹ÑÉå}¥‘õ}±…ÍÑ}ÍÕ•ÍÍ™Õ±}ÕÁ‘…Ñ”ˆ((€€€ÁÉ½Á•ÉÑä(€€€‘•˜‘•Ù¥•}¥¹™¼¡Í•±˜¤€´ø¥ÑmÍÑÈ°¹åtè(€€€€€€€É•ÑÕÉ¸}‘•Ù¥•}¥¹™¼¡Í•±˜¹½½É‘¥¹…Ñ½È°Í•±˜¹}½¹™¥}•¹ÑÉä¤((€€€ÁÉ½Á•ÉÑä(€€€‘•˜¹…Ñ¥Ù•}Ù…±Õ”¡Í•±˜¤€´ø‘…Ñ•Ñ¥µ”ð9½¹”è(€€€€€€€É…Ü€ô€¡Í•±˜¹½½É‘¥¹…Ñ½È¹‘…Ñ„½Èíô¤¹•Ð ‰½ÍÑ…Ñ¹¥…}Á½ÁÉ…Ý¹…}…­ÑÕ…±¥é…©„ˆ¤(€€€€€€€¥˜¹½ÐÉ…Üè(€€€€€€€€€€€É•ÑÕÉ¸9½¹”(€€€€€€€ÑÉäè(€€€€€€€€€€€É•ÑÕÉ¸‘…Ñ•Ñ¥µ”¹™É½µ¥Í½™½Éµ…Ð¡ÍÑÈ¡É…Ü¤¤¹É•Á±…”¡Ñé¥¹™¼õ‘Ñ}ÕÑ¥°¹UQ¤(€€€€€€€•á•ÁÐY…±Õ•ÉÉ½Èè(€€€€€€€€€€€É•ÑÕÉ¸9½¹”(()±…ÍÌ}1¥‰ÉÕÍ9•…É•ÍÑM•¹Í½È¡½½É‘¥¹…Ñ½É¹Ñ¥Ñåm1¥‰ÉÕÍ…Ñ…UÁ‘…Ñ•½½É‘¥¹…Ñ½Ét°M•¹Í½É¹Ñ¥Ñä¤è(€€€€ˆˆ‰]ÍÁ½±¹„‰…é„‘±„•¹©¤Á½­…éÕ©…å ¹…©‰±¥éÍé„Éé•è‘¼éÉ½‰¥•¹¥„¸ˆˆˆ((€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜°½½É‘¥¹…Ñ½Èè1¥‰ÉÕÍ…Ñ…UÁ‘…Ñ•½½É‘¥¹…Ñ½È°½¹™¥}•¹ÑÉäè½¹™¥¹ÑÉä¤€´ø9½¹”è(€€€€€€€ÍÕÁ•È ¤¹}}¥¹¥Ñ}|¡½½É‘¥¹…Ñ½È¤(€€€€€€€Í•±˜¹}½¹™¥}•¹ÑÉä€ô½¹™¥}•¹ÑÉä(€€€€€€€Í•±˜¹}…ÑÑÉ}¡…Í}•¹Ñ¥Ñå}¹…µ”€ôQÉÕ”((€€€ÁÉ½Á•ÉÑä(€€€‘•˜‘•Ù¥•}¥¹™¼¡Í•±˜¤€´ø¥ÑmÍÑÈ°¹åtè(€€€€€€€É•ÑÕÉ¸}‘•Ù¥•}¥¹™¼¡Í•±˜¹½½É‘¥¹…Ñ½È°Í•±˜¹}½¹™¥}•¹ÑÉä¤(()±…ÍÌ1¥‰ÉÕÍ9…©‰±¥éÍéåMÁÉ…Ý‘é¥…¹M•¹Í½È¡}1¥‰ÉÕÍ9•…É•ÍÑM•¹Í½È¤è(€€€€ˆˆ‰9…©‰±§ñÍé„­…ÉÑ¯ÍÝ­„±ÕˆÍÁÉ…Ý‘é¥…¸°‰•è‘½‘…Ñ­½Ý•¼Á½‰¥•É…¹¥„¸ˆˆˆ((€€€}…ÑÑÉ}¹…µ”€ô€‰9…©‰±§ñÍéäÍÁÉ…Ý‘é¥…¸ˆ(€€€}…ÑÑÉ}¥½¸€ô€‰µ‘¤é…±•¹‘…ÈµÍÑ…Èˆ((€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜°½½É‘¥¹…Ñ½Èè1¥‰ÉÕÍ…Ñ…UÁ‘…Ñ•½½É‘¥¹…Ñ½È°½¹™¥}•¹ÑÉäè½¹™¥¹ÑÉä¤€´ø9½¹”è(€€€€€€€ÍÕÁ•È ¤¹}}¥¹¥Ñ}|¡½½É‘¥¹…Ñ½È°½¹™¥}•¹ÑÉä¤(€€€€€€€Í•±˜¹}…ÑÑÉ}Õ¹¥ÅÕ•}¥€ô˜‰í½¹™¥}•¹ÑÉä¹•¹ÑÉå}¥‘õ}¹•áÑ}Ñ•ÍÐˆ((€€€ÁÉ½Á•ÉÑä(€€€‘•˜}•Ù•¹Ð¡Í•±˜¤€´ø¥ÑmÍÑÈ°¹åtð9½¹”è(€€€€€€€™½È•Ù•¹Ð¥¸€¡Í•±˜¹½½É‘¥¹…Ñ½È¹‘…Ñ„½Èíô¤¹•Ð ‰Ñ•Éµ¥¹…Éèˆ°mt¤è(€€€€€€€€€€€Ñ¥Ñ±”€ôÍÑÈ¡•Ù•¹Ð¹•Ð ‰ÑåÑÕ°ˆ°€ˆˆ¤½È€ˆˆ¤¹…Í•™½± ¤(€€€€€€€€€€€¥˜€‰ÍÁÉ…Ý‘èˆ¥¸Ñ¥Ñ±”½È€‰­…ÉÑ¬ˆ¥¸Ñ¥Ñ±”è(€€€€€€€€€€€€€€€É•ÑÕÉ¸•Ù•¹Ð(€€€€€€€É•ÑÕÉ¸9½¹”((€€€ÁÉ½Á•ÉÑä(€€€‘•˜¹…Ñ¥Ù•}Ù…±Õ”¡Í•±˜¤€´øÍÑÈð9½¹”è(€€€€€€€•Ù•¹Ð€ôÍ•±˜¹}•Ù•¹Ð(€€€€€€€É•ÑÕÉ¸ÍÑÈ¡•Ù•¹Ð¹•Ð ‰‘…Ñ„ˆ°€ˆˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤½È9½¹”¥˜•Ù•¹Ð•±Í”9½¹”((€€€ÁÉ½Á•ÉÑä(€€€‘•˜•áÑÉ…}ÍÑ…Ñ•}…ÑÑÉ¥‰ÕÑ•Ì¡Í•±˜¤€´ø¥ÑmÍÑÈ°¹åtè(€€€€€€€É•ÑÕÉ¸ì‰Ýå‘…Éé•¹¥”ˆèÍ•±˜¹}•Ù•¹Ñô(()±…ÍÌ1¥‰ÉÕÍ9…©‰±¥éÍé•i…‘…¹¥•M•¹Í½È¡}1¥‰ÉÕÍ9•…É•ÍÑM•¹Í½È¤è(€€€€ˆˆ‰9…©‰±§ñÍé”é…‘…¹¥”Ý•“	Õœ­½±•©¹¿m¤éÝËÍ½¹•¨ÁÉé•è1¥‰ÉÕÍ„¸ˆˆˆ((€€€}…ÑÑÉ}¹…µ”€ô€‰9…©‰±§ñÍé”é…‘…¹¥”ˆ(€€€}…ÑÑÉ}¥½¸€ô€‰µ‘¤é±¥Á‰½…ÉµÑ•áÐµ±½¬µ½ÕÑ±¥¹”ˆ((€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜°½½É‘¥¹…Ñ½Èè1¥‰ÉÕÍ…Ñ…UÁ‘…Ñ•½½É‘¥¹…Ñ½È°½¹™¥}•¹ÑÉäè½¹™¥¹ÑÉä¤€´ø9½¹”è(€€€€€€€ÍÕÁ•È ¤¹}}¥¹¥Ñ}|¡½½É‘¥¹…Ñ½È°½¹™¥}•¹ÑÉä¤(€€€€€€€Í•±˜¹}…ÑÑÉ}Õ¹¥ÅÕ•}¥€ô˜‰í½¹™¥}•¹ÑÉä¹•¹ÑÉå}¥‘õ}¹•áÑ}¡½µ•Ý½É¬ˆ((€€€ÁÉ½Á•ÉÑä(€€€‘•˜}Ñ…Í¬¡Í•±˜¤€´ø¥ÑmÍÑÈ°¹åtð9½¹”è(€€€€€€€Ñ…Í­Ì€ô€¡Í•±˜¹½½É‘¥¹…Ñ½È¹‘…Ñ„½Èíô¤¹•Ð ‰é…‘…¹¥„ˆ°mt¤(€€€€€€€É•ÑÕÉ¸Ñ…Í­ÍlÁt¥˜Ñ…Í­Ì•±Í”9½¹”((€€€ÁÉ½Á•ÉÑä(€€€‘•˜¹…Ñ¥Ù•}Ù…±Õ”¡Í•±˜¤€´øÍÑÈð9½¹”è(€€€€€€€Ñ…Í¬€ôÍ•±˜¹}Ñ…Í¬(€€€€€€€É•ÑÕÉ¸ÍÑÈ¡Ñ…Í¬¹•Ð ‰Ñ•Éµ¥¸ˆ°€ˆˆ¤½È€ˆˆ¤¹ÍÑÉ¥À ¤½È9½¹”¥˜Ñ…Í¬•±Í”9½¹”((€€€ÁÉ½Á•ÉÑä(€€€‘•˜•áÑÉ…}ÍÑ…Ñ•}…ÑÑÉ¥‰ÕÑ•Ì¡Í•±˜¤€´ø¥ÑmÍÑÈ°¹åtè(€€€€€€€É•ÑÕÉ¸ì‰é…‘…¹¥”ˆèÍ•±˜¹}Ñ…Í­ô(