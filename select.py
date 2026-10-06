"""Select entities for the writable 4Heat stove settings."""

import logging
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, DATA_COORDINATOR, SENSOR_TYPES,
    POWER_TYPE, FAN_TYPE, CANALISATION_TYPE, POWER_NAMES, FAN_NAMES,
)
from .coordinator import FourHeatDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Writable settings with their value -> option tables. The power setting
# packet reports a 1-6 range, so P6 is the highest option.
SELECT_OPTIONS = {
    POWER_TYPE: {v: POWER_NAMES[v] for v in range(1, 7)},
    FAN_TYPE: FAN_NAMES,
    CANALISATION_TYPE: FAN_NAMES,
}


async def async_setup_entry(hass, entry, async_add_entities):
    """Add the 4Heat setting selects."""
    coordinator: FourHeatDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]
    async_add_entities(
        [FourHeatSelect(coordinator, setting, entry.title) for setting in SELECT_OPTIONS]
    )


class FourHeatSelect(CoordinatorEntity, SelectEntity):
    """A stove setting that can be read and changed."""

    def __init__(self, coordinator, setting_type, name):
        """Initialize the select."""
        super().__init__(coordinator)
        self.type = setting_type
        self._name = name
        self.coordinator = coordinator
        self._value_to_option = SELECT_OPTIONS[setting_type]
        self._option_to_value = {o: v for v, o in self._value_to_option.items()}
        self.serial_number = coordinator.serial_number
        self.model = coordinator.model

    @property
    def name(self):
        """Return the name of the select."""
        return f"{self._name} {SENSOR_TYPES[self.type][0]}"

    @property
    def unique_id(self):
        """Return unique id based on device name and setting."""
        return f"{self._name}_{self.type}"

    @property
    def icon(self):
        """Return icon."""
        return SENSOR_TYPES[self.type][2] or None

    @property
    def options(self):
        """Return the selectable options."""
        return list(self._value_to_option.values())

    @property
    def current_option(self):
        """Return the option matching the stove's current value."""
        if self.type not in self.coordinator.data:
            return None
        return self._value_to_option.get(self.coordinator.data[self.type][0])

    async def async_select_option(self, option):
        """Write the chosen option to the stove."""
        value = self._option_to_value[option]
        _LOGGER.debug(f"Setting {self.type} to {option} ({value})")
        await self.coordinator.async_set_setting(self.type, value)
        await self.coordinator.async_request_refresh()

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "identifiers": {(DOMAIN, self.serial_number)},
            "name": self._name,
            "manufacturer": "4Heat",
            "model": self.model,
        }
