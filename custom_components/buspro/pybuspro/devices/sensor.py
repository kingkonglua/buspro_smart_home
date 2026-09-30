import asyncio
import struct

# from ..helpers.generics import Generics
from .control import _ReadSensorStatus, _ReadStatusOfUniversalSwitch, _ReadStatusOfChannels, _ReadFloorHeatingStatus, \
    _ReadDryContactStatus, _ReadSensorsInOneStatus, _ReadTemperature, _ReadMotionSensorStatus
from .device import Device
from ..helpers.enums import *


class Sensor(Device):
    # G7: sensor-status / broadcast frames encode HDL's raw value as
    # `degC + 20` (so negatives fit an unsigned byte). These device kinds use
    # that encoding; anything else (e.g. "dlp") already reports true Celsius.
    # Mirrors upstream v5.0.7's _CMS_BIASED_KINDS.
    _CMS_BIASED_KINDS = ("generic", None, "12in1", "sensors_in_one", "8in1")

    def __init__(self, buspro, device_address, universal_switch_number=None, channel_number=None, device=None,
                 switch_number=None, name="", delay_read_current_state_seconds=0, temperature_channel=1):
        super().__init__(buspro, device_address, name)

        self._buspro = buspro
        self._device_address = device_address
        self._universal_switch_number = universal_switch_number
        self._channel_number = channel_number
        self._name = name
        self._device = device
        self._switch_number = switch_number
        # M-10: channel the MPTL/panel family's channel-addressed temperature
        # read (0xE3E7/0xE3E8) answers on. Panels report every channel they
        # own, so only accept this one rather than let channels fight.
        self._temperature_channel = temperature_channel or 1

        self._current_temperature = None
        # Sub-degree value from a panel/float32 temperature field. Carries the
        # true value with no +20 bias, so it is preferred over the coarse byte.
        self._current_temperature_precise = None
        self._current_humidity = None
        self._brightness = None
        self._motion_sensor = None
        self._sonic = None
        self._dry_contact_1_status = None
        self._dry_contact_2_status = None
        self._universal_switch_status = OnOffStatus.OFF
        self._channel_status = 0
        self._switch_status = 0

        self.register_telegram_received_cb(self._telegram_received_cb)
        self._call_read_current_status_of_sensor(run_from_init=True)

    def _telegram_received_cb(self, telegram):
        if telegram.operate_code == OperateCode.ReadSensorStatusResponse:
            if len(telegram.payload) < 8:
                return
            self._store_temperature(telegram.payload[1], self._cms_biased)
            brightness_high = telegram.payload[2]
            brightness_low = telegram.payload[3]
            self._motion_sensor = telegram.payload[4]
            self._sonic = telegram.payload[5]
            self._dry_contact_1_status = telegram.payload[6]
            self._dry_contact_2_status = telegram.payload[7]
            # F-3: payload[0] is a raw int byte; SuccessOrFailure members are
            # byte-valued enums, so the old `== SuccessOrFailure.Success`
            # comparison was always False and this reply never updated HA.
            # Store the readings unconditionally (some firmware does not use
            # 0xF8 in the success byte) and always notify listeners.
            self._brightness = (brightness_high << 8) | brightness_low
            self._call_device_updated()

        elif telegram.operate_code == OperateCode.ReadSensorsInOneStatusResponse:
            if len(telegram.payload) < 10:
                return
            self._store_temperature(telegram.payload[1], biased=True)
            # M-10: humidity (%RH) at payload[4]. 0xFF is HDL's "no humidity
            # sensor fitted" sentinel -- keep None instead of showing 255%.
            humidity = telegram.payload[4]
            self._current_humidity = None if humidity == 0xFF else humidity
            self._motion_sensor = telegram.payload[7]
            self._dry_contact_1_status = telegram.payload[8]
            self._dry_contact_2_status = telegram.payload[9]
            self._call_device_updated()

        # sensors_in_one 主动推送。对齐上游 v5.0.7：0x1630 与轮询的 0x1605
        # 布局不同——没有开头的 success 字节，所有字段整体前移一格。
        # 因此温度在 [0]（带 +20 偏移，在 ingest 时由 _store_temperature 校正），
        # lux 在 [1..2]。motion 字节上游在抓包确认前刻意不解析，这里同样不解析，
        # 避免凭空产生误触发。
        elif telegram.operate_code == OperateCode.BroadcastSensorsInOneStatusResponse:
            if not telegram.payload:
                return
            self._store_temperature(telegram.payload[0], biased=True)
            if len(telegram.payload) >= 3:
                brightness_high = telegram.payload[1]
                brightness_low = telegram.payload[2]
                self._brightness = (brightness_high << 8) | brightness_low
            # M-10: humidity sits one index lower than the polled 0x1605 frame
            # (payload[3]); same 0xFF "not fitted" sentinel.
            if len(telegram.payload) >= 4:
                humidity = telegram.payload[3]
                self._current_humidity = None if humidity == 0xFF else humidity
            self._call_device_updated()

        elif telegram.operate_code == OperateCode.BroadcastSensorStatusResponse:
            if len(telegram.payload) < 7:
                return
            self._store_temperature(telegram.payload[0], self._cms_biased)
            brightness_high = telegram.payload[1]
            brightness_low = telegram.payload[2]
            self._motion_sensor = telegram.payload[3]
            self._sonic = telegram.payload[4]
            self._dry_contact_1_status = telegram.payload[5]
            self._dry_contact_2_status = telegram.payload[6]
            self._brightness = (brightness_high << 8) | brightness_low
            self._call_device_updated()

        elif telegram.operate_code == OperateCode.BroadcastSensorStatusAutoResponse:
            if len(telegram.payload) < 7:
                return
            # G7: the +20 bias is now applied inside _store_temperature via
            # _cms_biased, so the old device-special-cased -20 here (and the
            # matching exemption in the `temperature` property) is gone.
            self._store_temperature(telegram.payload[0], self._cms_biased)

            brightness_high = telegram.payload[1]
            brightness_low = telegram.payload[2]
            self._motion_sensor = telegram.payload[3]
            self._sonic = telegram.payload[4]
            self._dry_contact_1_status = telegram.payload[5]
            self._dry_contact_2_status = telegram.payload[6]
            self._brightness = (brightness_high << 8) | brightness_low
            self._call_device_updated()

        elif telegram.operate_code == OperateCode.ReadFloorHeatingStatusResponse:
            if len(telegram.payload) < 2:
                return
            self._store_temperature(telegram.payload[1], biased=False)
            self._call_device_updated()

        elif telegram.operate_code == OperateCode.BroadcastTemperatureResponse:
            if len(telegram.payload) < 2:
                return
            # G7: this broadcast carries the TRUE Celsius value with no +20
            # bias, so it must NOT be corrected as if it were a raw byte.
            # Previously the `temperature` property subtracted 20 from every
            # non-dlp/12in1 device, so a real 26 degC frame read as 6.
            self._store_temperature(telegram.payload[1], biased=False)
            self._call_device_updated()

        # M-10: MPTL/Enviro/Granite panel channel-addressed temperature
        # (0xE3E8): [channel, signed_whole_degC, <float32 LE degC>]. The whole
        # byte carries the true value with no +20 bias (biased=False), and the
        # float32, when present, is preferred by the `temperature` property.
        elif telegram.operate_code == OperateCode.ReadTemperatureResponse:
            if len(telegram.payload) >= 2 and telegram.payload[0] == self._temperature_channel:
                whole = telegram.payload[1]
                if whole > 127:
                    whole -= 256
                self._store_temperature(whole, biased=False)
                if len(telegram.payload) >= 6:
                    try:
                        self._current_temperature_precise = round(
                            struct.unpack("<f", bytes(telegram.payload[2:6]))[0], 2
                        )
                    except (struct.error, ValueError, TypeError):
                        self._current_temperature_precise = None
                else:
                    self._current_temperature_precise = float(whole)
                self._call_device_updated()

        # M-10: periodic illuminance push (0xE441) from a sensors-in-one
        # module -- payload = [?, ?, lux_hi, lux_lo, ...].
        elif telegram.operate_code == OperateCode.BroadcastLuminanceResponse:
            if len(telegram.payload) >= 4:
                self._brightness = (telegram.payload[2] << 8) | telegram.payload[3]
                self._call_device_updated()

        # M-10: CMS-PIR style motion-only module (0xDB01); motion flag at [3].
        elif telegram.operate_code == OperateCode.ReadMotionSensorStatusResponse:
            if len(telegram.payload) >= 4:
                self._motion_sensor = telegram.payload[3]
                self._call_device_updated()

        elif telegram.operate_code == OperateCode.ReadStatusOfUniversalSwitchResponse:
            if len(telegram.payload) < 2:
                return
            switch_number = telegram.payload[0]
            universal_switch_status = telegram.payload[1]

            if switch_number == self._universal_switch_number:
                self._universal_switch_status = universal_switch_status
                self._call_device_updated()

        elif telegram.operate_code == OperateCode.BroadcastStatusOfUniversalSwitch:
            if (self._universal_switch_number is not None
                    and len(telegram.payload) > self._universal_switch_number
                    and self._universal_switch_number <= telegram.payload[0]):
                self._universal_switch_status = telegram.payload[self._universal_switch_number]
                self._call_device_updated()

        elif telegram.operate_code == OperateCode.UniversalSwitchControlResponse:
            if len(telegram.payload) < 2:
                return
            switch_number = telegram.payload[0]
            universal_switch_status = telegram.payload[1]

            if switch_number == self._universal_switch_number:
                self._universal_switch_status = universal_switch_status
                self._call_device_updated()

        elif telegram.operate_code == OperateCode.ReadStatusOfChannelsResponse:
            if self._channel_number is None:
                return
            if len(telegram.payload) <= self._channel_number:
                return
            if self._channel_number <= telegram.payload[0]:
                self._channel_status = telegram.payload[self._channel_number]
                self._call_device_updated()

        elif telegram.operate_code == OperateCode.SingleChannelControlResponse:
            if len(telegram.payload) < 3:
                return
            if self._channel_number == telegram.payload[0]:
                # if telegram.payload[1] == SuccessOrFailure.Success::
                self._channel_status = telegram.payload[2]
                self._call_device_updated()

        elif telegram.operate_code == OperateCode.ReadDryContactStatusResponse:
            if len(telegram.payload) < 3:
                return
            if self._switch_number == telegram.payload[1]:
                self._switch_status = telegram.payload[2]
                self._call_device_updated()

    async def read_sensor_status(self):
        if self._universal_switch_number is not None:
            rsous = _ReadStatusOfUniversalSwitch(self._buspro)
            rsous.subnet_id, rsous.device_id = self._device_address
            rsous.switch_number = self._universal_switch_number
            await rsous.send()
        elif self._channel_number is not None:
            rsoc = _ReadStatusOfChannels(self._buspro)
            rsoc.subnet_id, rsoc.device_id = self._device_address
            await rsoc.send()
        elif self._device is not None and self._device == "dlp":
            rfhs = _ReadFloorHeatingStatus(self._buspro)
            rfhs.subnet_id, rfhs.device_id = self._device_address
            await rfhs.send()
        elif self._device is not None and self._device == "dry_contact":
            rdcs = _ReadDryContactStatus(self._buspro)
            rdcs.subnet_id, rdcs.device_id = self._device_address
            rdcs.switch_number = self._switch_number
            await rdcs.send()
        elif self._device is not None and self._device == "sensors_in_one":
            rsios = _ReadSensorsInOneStatus(self._buspro)
            rsios.subnet_id, rsios.device_id = self._device_address
            await rsios.send()
        elif self._device is not None and self._device == "pir":
            # M-10: CMS-PIR modules only answer 0xDB00, never the generic
            # 0x1645 read.
            rms = _ReadMotionSensorStatus(self._buspro)
            rms.subnet_id, rms.device_id = self._device_address
            await rms.send()
        elif self._device is not None and self._device == "panel":
            # M-10: MPTL/panel family channel-addressed temperature read.
            rt = _ReadTemperature(self._buspro)
            rt.subnet_id, rt.device_id = self._device_address
            rt.channel_number = self._temperature_channel
            await rt.send()
        else:
            rss = _ReadSensorStatus(self._buspro)
            rss.subnet_id, rss.device_id = self._device_address
            await rss.send()

    @property
    def _cms_biased(self):
        """True if this device's sensor-status frames carry HDL's +20 bias."""
        return self._device in self._CMS_BIASED_KINDS

    def _store_temperature(self, raw, biased):
        """Normalise a raw HDL temperature byte to true Celsius and store it.

        G7: applying the +20 correction here, per frame, keeps it from being
        applied twice (some frames already carry true Celsius). Previously the
        `temperature` property subtracted 20 from every non-dlp/12in1 device,
        so the 0xE3E5 broadcast -- which carries the true value -- read 20 degC
        too low (a real 26 degC frame showed as 6). Mirrors upstream v5.0.7.
        """
        try:
            value = raw - 20 if biased else raw
        except TypeError:
            return
        self._current_temperature = value

    @property
    def temperature(self):
        # Every decode branch normalises to true Celsius on arrival via
        # _store_temperature, so no +20 correction is applied here. Prefer the
        # sub-degree float a panel/0xE3E8 read supplies over the whole byte.
        if self._current_temperature is None:
            return None
        if self._current_temperature_precise is not None:
            return self._current_temperature_precise
        return self._current_temperature

    @property
    def brightness(self):
        if self._brightness is None:
            return None
        return self._brightness

    @property
    def humidity(self):
        # M-10: decoded from the polled sensors-in-one frame (0x1605 [4]) or
        # its 0x1630 push ([3]); None until a real reading arrives.
        return self._current_humidity

    @property
    def movement(self):
        if self._motion_sensor == 1 or self._sonic == 1:
            return True
        if self._motion_sensor == 0 and self._sonic == 0:
            return False

    @property
    def dry_contact_1_is_on(self):
        if self._dry_contact_1_status == 1:
            return True
        else:
            return False

    @property
    def dry_contact_2_is_on(self):
        if self._dry_contact_2_status == 1:
            return True
        else:
            return False

    @property
    def universal_switch_is_on(self):
        if self._universal_switch_status == 1:
            return True
        else:
            return False

    @property
    def single_channel_is_on(self):
        if self._channel_status > 0:
            return True
        else:
            return False

    @property
    def switch_status(self):
        if self._switch_status == 1:
            return True
        else:
            return False

    @property
    def device_identifier(self):
        return f"{self._device_address}-{self._universal_switch_number}-{self._channel_number}-{self._switch_number}"

    def _call_read_current_status_of_sensor(self, run_from_init=False):

        async def read_current_status_of_sensor():
            if run_from_init:
                await asyncio.sleep(5)
            await self.read_sensor_status()

        asyncio.create_task(read_current_status_of_sensor())
