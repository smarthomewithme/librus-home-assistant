"""Konfiguracja integracji Librus Synergia z poziomu interfejsu HA."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from librus_apix.client import new_client
from librus_apix.exceptions import AuthorizationError, MaintananceError
from librus_synergia import (
    Librus,
    LibrusInvalidCredentialsError,
    LibrusError,
)

from .const import (
    API_BACKEND_CURRENT,
    API_BACKEND_LEGACY,
    CONF_API_BACKEND,
    CONF_DATA_REFRESH_INTERVAL,
    CONF_REFRESH_MODE,
    CONF_TIMETABLE_REFRESH_INTERVAL,
    DEFAULT_DATA_REFRESH_MINUTES,
    DEFAULT_TIMETABLE_REFRESH_MINUTES,
    DOMAIN,
    MAX_DATA_REFRESH_MINUTES,
    MAX_TIMETABLE_REFRESH_MINUTES,
    MIN_DATA_REFRESH_MINUTES,
    MIN_TIMETABLE_REFRESH_MINUTES,
    REFRESH_MODE_AUTO,
    REFRESH_MODE_MANUAL,
    option_minutes,
    option_refresh_mode,
)

_LOGGER = logging.getLogger(__name__)

LOGIN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(
            CONF_API_BACKEND, default=API_BACKEND_LEGACY
        ): vol.In(
            {
                API_BACKEND_LEGACY: "Klasyczne API (obecne konta)",
                API_BACKEND_CURRENT: "Nowe API (w tym zerówka / przedszkole)",
            }
        ),
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Sprawdź dane logowania wybranym silnikiem, bez zachowania sesji."""
    if data.get(CONF_API_BACKEND, API_BACKEND_LEGACY) == API_BACKEND_CURRENT:
        client = Librus(data[CONF_USERNAME], data[CONF_PASSWORD])
        try:
            await client.login()
        finally:
            await client.close()
        return

    client = await hass.async_add_executor_job(new_client)
    token = await hass.async_add_executor_job(
        client.get_token,
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
    )
    if not token:
        raise ValueError("Librus nie zwrócił tokenu logowania")


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Obsłuż dodawanie konta Librus Synergia."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Pokaż formularz logowania i utwórz wpis integracji."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = str(user_input[CONF_USERNAME]).strip()
            user_input = {**user_input, CONF_USERNAME: username}

            if any(
                entry.data.get(CONF_USERNAME) == username
                for entry in self._async_current_entries()
            ):
                return self.async_abort(reason="already_configured")

            try:
                await validate_input(self.hass, user_input)
            except (AuthorizationError, LibrusInvalidCredentialsError):
                errors["base"] = "invalid_auth"
            except (MaintananceError, LibrusError, OSError, ValueError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Nieoczekiwany błąd podczas logowania do Librusa")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(username)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Librus Synergia ({username})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=LOGIN_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, _entry_data: dict[str, Any]):
        """Rozpocznij reautoryzację po jawnym odrzuceniu danych przez Librusa."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        """Poproś wyłącznie o nowe hasło i zachowaj istniejące encje."""
        entry = self._get_reauth_entry()
        username = str(entry.data.get(CONF_USERNAME, ""))
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {
                CONF_USERNAME: username,
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_API_BACKEND: entry.data.get(
                    CONF_API_BACKEND, API_BACKEND_LEGACY
                ),
            }
            try:
                await validate_input(self.hass, data)
            except (AuthorizationError, LibrusInvalidCredentialsError):
                errors["base"] = "invalid_auth"
            except (MaintananceError, LibrusError, OSError, ValueError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception(
                    "Nieoczekiwany błąd podczas reautoryzacji Librusa"
                )
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={"username": username},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ):
        """Pozwól zmienić hasło bez usuwania konta i encji."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {
                CONF_USERNAME: entry.data[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_API_BACKEND: entry.data.get(
                    CONF_API_BACKEND, API_BACKEND_LEGACY
                ),
            }
            try:
                await validate_input(self.hass, data)
            except (AuthorizationError, LibrusInvalidCredentialsError):
                errors["base"] = "invalid_auth"
            except (MaintananceError, LibrusError, OSError, ValueError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception(
                    "Nieoczekiwany błąd podczas ponownej konfiguracji Librusa"
                )
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                    reason="reconfigure_successful",
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "username": str(entry.data.get(CONF_USERNAME, "")),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(_config_entry: ConfigEntry):
        """Udostępnij ustawienia częstotliwości odświeżania."""
        return LibrusOptionsFlow()


class LibrusOptionsFlow(config_entries.OptionsFlow):
    """Opcje odświeżania osobne dla każdego ucznia."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Zapisz interwały i przeładuj wpis przez listener integracji."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        refresh_mode = option_refresh_mode(options)
        data_interval = option_minutes(
            options,
            CONF_DATA_REFRESH_INTERVAL,
            DEFAULT_DATA_REFRESH_MINUTES,
            MIN_DATA_REFRESH_MINUTES,
            MAX_DATA_REFRESH_MINUTES,
        )
        timetable_interval = option_minutes(
            options,
            CONF_TIMETABLE_REFRESH_INTERVAL,
            DEFAULT_TIMETABLE_REFRESH_MINUTES,
            MIN_TIMETABLE_REFRESH_MINUTES,
            MAX_TIMETABLE_REFRESH_MINUTES,
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_REFRESH_MODE,
                        default=refresh_mode,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[REFRESH_MODE_AUTO, REFRESH_MODE_MANUAL],
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key="refresh_mode",
                        )
                    ),
                    vol.Required(
                        CONF_DATA_REFRESH_INTERVAL,
                        default=data_interval,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_DATA_REFRESH_MINUTES,
                            max=MAX_DATA_REFRESH_MINUTES,
                            step=5,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    ),
                    vol.Required(
                        CONF_TIMETABLE_REFRESH_INTERVAL,
                        default=timetable_interval,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_TIMETABLE_REFRESH_MINUTES,
                            max=MAX_TIMETABLE_REFRESH_MINUTES,
                            step=30,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    ),
                }
            ),
        )
