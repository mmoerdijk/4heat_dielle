"""Provides the MYPV DataUpdateCoordinator."""
from datetime import timedelta
import logging
import socket

from async_timeout import timeout
from homeassistant.util.dt import utcnow
from homeassistant.const import CONF_HOST
"""from homeassistant.helpers.typing import HomeAssistantType"""
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN, SOCKET_BUFFER, SOCKET_TIMEOUT, TCP_PORT, DATA_QUERY, ERROR_QUERY,
    RESULT_ERROR, CONF_MODE, MODES, MODE_TYPE, ERROR_TYPE, SENSOR_TYPES, MODE_NAMES, UnitOfTemperature,
    FUEL_MODE_TYPE
)

_LOGGER = logging.getLogger(__name__)


def extract_sensor_id(packet):
    """Extracts the canonical sensor ID from a raw packet string.

    Used both when creating sensor entities and when decoding updates, so an
    entity's key always matches the key its data is stored under.
    """
    if not isinstance(packet, str) or len(packet) < 6:
        return None

    id_hex = packet[2:6]

    # Heuristic mapping
    candidates = [id_hex, "2" + id_hex]
    try:
        val_dec = int(id_hex, 16)
        candidates.append("20" + str(val_dec))
    except ValueError:
        pass
    if id_hex.startswith('8'):
        candidates.append("c" + id_hex)

    for key in candidates:
        if key in SENSOR_TYPES:
            return key

    # Fallback for known special cases
    if id_hex == "0001":
        return "30001"
    if id_hex == "0000":
        return FUEL_MODE_TYPE

    # Unknown sensor: fall back to the simple slice (legacy behavior)
    return packet[1:6]


class FourHeatDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching 4heat data."""

    def __init__(self, hass: HomeAssistant, *, config: dict, options: dict, id: str):
        """Initialize global 4heat data updater."""
        self._host = config[CONF_HOST]
        self._mode = False
        self.swiches = [MODE_TYPE]
        self.stove_id = id
        
        if CONF_MODE in config:
            self._mode = config[CONF_MODE]

        if self._mode == False:
            self._on_cmd = MODES[0][0]
            self._off_cmd = MODES[0][1]
            self._unblock_cmd = MODES[0][2]
            self.swiches.append(ERROR_TYPE)
        else:
            self._on_cmd = MODES[1][0]
            self._off_cmd = MODES[1][1]
            self._unblock_cmd = MODES[1][2]

        self._next_update = 0
        self.model = "Basic"
        self.serial_number = "1"
        update_interval = timedelta(seconds=60)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )

    async def _async_update_data(self) -> dict:
        """Fetch data from 4heat."""
        def _query_stove(query) -> list[str]:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(SOCKET_TIMEOUT)
                s.connect((self._host, TCP_PORT))
                s.send(query)
                result = s.recv(SOCKET_BUFFER).decode()
                s.close()
                _LOGGER.debug(f"Query sent: {query}")
                _LOGGER.debug(f"Raw data received: {result}")
                
                # Parse JSON-like response manually or use split
                # The response is like ["2WL","0","..."]
                # We want the list of strings
                result = result.replace("[","").replace("]","").replace('"',"")
                # Handle the pipe separator if present (some firmwares use it)
                if '|' in result:
                    parts = result.split('|')
                    # Take the last part which usually contains the JSON array content
                    result = parts[-1]
                
                d = result.split(",")
            except Exception as error:
                _LOGGER.error(f"Update error: {error}")
                self._next_update = 5
                d = []
            return d

        def _parse_hex_val(hex_str):
            try:
                return int(hex_str, 16)
            except (ValueError, TypeError):
                return 0

        def _decode_packet(packet):
            """Decodes a single data packet string into a dictionary of values."""
            if not isinstance(packet, str) or len(packet) < 6:
                return None, None, None

            data_hex = packet[6:]

            # Unknown sensors keep their fallback key and raw value
            sensor_key = extract_sensor_id(packet)
            sensor_info = SENSOR_TYPES.get(sensor_key)
            if not sensor_info and sensor_key == "30001":
                sensor_info = ["Summary Packet", None, ""]

            val_hex = data_hex[0:4]
            val = _parse_hex_val(val_hex)
            name = "Unknown"
            unit = ""

            if sensor_info:
                name = sensor_info[0]
                unit = sensor_info[1] if sensor_info[1] else ""
                
                if sensor_key == "30001":
                    exhaust_hex = packet[-6:-2]
                    val = _parse_hex_val(exhaust_hex)
                    name = "Exhaust temperature (Summary)"
                    unit = UnitOfTemperature.CELSIUS
                elif sensor_key == FUEL_MODE_TYPE:
                    val = _parse_hex_val(packet.strip()[-6:-2])
                elif sensor_key == "c8101":
                    state_hex = packet[16:18]
                    val = _parse_hex_val(state_hex)
                    pass

            # Apply scaling for temperature
            if unit == UnitOfTemperature.CELSIUS:
                if "Exhaust" in name:
                    val = val
                else:
                    val = val / 10.0

            return sensor_key, val, sensor_info

        def _update_data() -> dict:
            """Fetch data from 4heat via sync functions."""
            # Query page 0
            list_data = _query_stove(DATA_QUERY)
            
            # Optional: Query page 1 if needed
            # list_data_1 = _query_stove(b'["2WL","1"]')
            # list_data.extend(list_data_1)

            data_dict = self.data
            if data_dict is None:
                data_dict = {}
            
            if len(list_data) > 0:
                if list_data[0] == RESULT_ERROR:
                    # Handle error or retry
                    pass
                    
                for packet in list_data:
                    if len(packet) > 6:
                        key, val, info = _decode_packet(packet)
                        if key:
                            name = info[0] if info else "Unknown"
                            data_dict[key] = [val, name]

            return data_dict

        try:
            async with timeout(10):
                d = await self.hass.async_add_executor_job(_update_data)
                return d
        except Exception as error:
            raise UpdateFailed(f"Invalid response from API: {error}") from error

    async def async_turn_on(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(SOCKET_TIMEOUT)
            s.connect((self._host, TCP_PORT))
            s.send(self._on_cmd)
            s.recv(SOCKET_BUFFER).decode()
            s.close()
            _LOGGER.debug("Toggle ON")
        except Exception as ex:
            _LOGGER.error(ex)

    async def async_turn_off(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(SOCKET_TIMEOUT)
            s.connect((self._host, TCP_PORT))
            s.send(self._off_cmd)
            s.recv(SOCKET_BUFFER).decode()
            s.close()
            _LOGGER.debug("Toggle OFF")
        except Exception as ex:
            _LOGGER.error(ex)

    async def async_unblock(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(SOCKET_TIMEOUT)
            s.connect((self._host, TCP_PORT))
            s.send(self._unblock_cmd)
            s.recv(SOCKET_BUFFER).decode()
            s.close()
            _LOGGER.debug("Toggle Unblock")
        except Exception as ex:
            _LOGGER.error(ex)


    async def async_set_value(self, id, value) -> bool:
        val = str(value).zfill(12)
        set_val = f'["SEC","1","B{id}{val}"]'.encode()
        _LOGGER.debug(f"Command to send: {set_val}")
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(SOCKET_TIMEOUT)
            s.connect((self._host, TCP_PORT))
            s.send(set_val)
            s.recv(SOCKET_BUFFER).decode()
            s.close()
            _LOGGER.debug("Set value")
        except Exception as ex:
            _LOGGER.error(ex)
