from ..core.telegram import Telegram
from ..helpers.enums import CurtainAction, OperateCode


class _Control:
    def __init__(self, buspro):
        self._buspro = buspro
        self.subnet_id = None
        self.device_id = None

    @staticmethod
    def build_telegram_from_control(control):

        if control is None:
            return None

        if type(control) == _SingleChannelControl:
            operate_code = OperateCode.SingleChannelControl
            payload = [control.channel_number, control.channel_level, control.running_time_minutes,
                       control.running_time_seconds]

        elif type(control) == _SceneControl:
            operate_code = OperateCode.SceneControl
            payload = [control.area_number, control.scene_number]

        elif type(control) == _ReadStatusOfChannels:
            operate_code = OperateCode.ReadStatusOfChannels
            payload = []

        elif type(control) == _GenericControl:
            operate_code = control.operate_code
            payload = control.payload

        elif type(control) == _UniversalSwitch:
            operate_code = OperateCode.UniversalSwitchControl
            payload = [control.switch_number, control.switch_status.value]

        elif type(control) == _ReadStatusOfUniversalSwitch:
            operate_code = OperateCode.ReadStatusOfUniversalSwitch
            payload = [control.switch_number]

        elif type(control) == _ReadSensorStatus:
            operate_code = OperateCode.ReadSensorStatus
            payload = []

        elif type(control) == _ReadSensorsInOneStatus:
            operate_code = OperateCode.ReadSensorsInOneStatus
            payload = []

        elif type(control) == _ReadTemperature:
            # M-10: channel-addressed panel temperature read (0xE3E7).
            operate_code = OperateCode.ReadTemperature
            payload = [control.channel_number if control.channel_number is not None else 1]

        elif type(control) == _ReadMotionSensorStatus:
            # M-10: motion-only status read (0xDB00) for CMS-PIR modules.
            operate_code = OperateCode.ReadMotionSensorStatus
            payload = []

        elif type(control) == _ReadFloorHeatingStatus:
            operate_code = OperateCode.ReadFloorHeatingStatus
            payload = []

        elif type(control) == _ReadDryContactStatus:
            operate_code = OperateCode.ReadDryContactStatus
            payload = [1, control.switch_number]

        elif type(control) == _ControlFloorHeatingStatus:
            operate_code = OperateCode.ControlFloorHeatingStatus
            payload = [control.temperature_type, control.status, control.mode, control.normal_temperature,
                       control.day_temperature, control.night_temperature, control.away_temperature]

        elif type(control) == _CurtainControl:
            operate_code = OperateCode.CurtainSwitchControl
            # F-C1: the action is either a CurtainAction (open/close/stop on a
            # normal channel) or a bare 0-100 int (direct position on No.=17).
            action = control.action
            payload = [
                control.curtain_number,
                action.value if isinstance(action, CurtainAction) else int(action),
            ]

        elif type(control) == _ReadStatusOfCurtainSwitch:
            operate_code = OperateCode.ReadStatusOfCurtainSwitch
            payload = [control.curtain_number]

        elif type(control) == _ReadAcStatus:
            operate_code = OperateCode.ReadAcStatus
            payload = [control.ac_number if control.ac_number is not None else 1]

        elif type(control) == _ControlAcStatus:
            operate_code = OperateCode.ControlAcStatus
            payload = [control.ac_number, control.temperature_type, control.current_temperature,
                       control.cooling_temperature, control.heating_temperature, control.auto_temperature,
                       control.dry_temperature, control.mode_and_fan, control.status, control.mode,
                       control.fan_speed, control.current_mode_temperature, control.sweep]

        else:
            return None

        telegram = Telegram()
        telegram.target_address = (control.subnet_id, control.device_id)
        telegram.operate_code = operate_code
        telegram.payload = payload
        return telegram

    @property
    def telegram(self):
        return self.build_telegram_from_control(self)

    async def send(self):
        telegram = self.telegram

        # if telegram.target_address[1] == 100:
        #     print("==== {}".format(str(telegram)))

        await self._buspro.network_interface.send_telegram(telegram)


class _GenericControl(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)

        self.payload = None
        self.operate_code = None


class _SingleChannelControl(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)

        self.channel_number = None
        self.channel_level = None
        self.running_time_minutes = None
        self.running_time_seconds = None


class _SceneControl(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)

        self.area_number = None
        self.scene_number = None


class _ReadStatusOfChannels(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)
        # no more properties


class _UniversalSwitch(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)

        self.switch_number = None
        self.switch_status = None


class _ReadStatusOfUniversalSwitch(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)

        self.switch_number = None


class _ReadSensorStatus(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)
        # no more properties


class _ReadSensorsInOneStatus(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)
        # no more properties


class _ReadTemperature(_Control):
    """Channel-addressed temperature read (0xE3E7) for MPTL/panel devices.

    M-10: upstream v5.0.7 added this for the Granite/Enviro panel family,
    whose onboard sensor answers on channel 1.
    """

    def __init__(self, buspro):
        super().__init__(buspro)
        self.channel_number = 1


class _ReadMotionSensorStatus(_Control):
    """Motion-only status read (0xDB00) for CMS-PIR style modules.

    M-10: these modules never answer the generic 0x1645 read, only 0xDB00.
    """

    def __init__(self, buspro):
        super().__init__(buspro)
        # no more properties


class _ReadFloorHeatingStatus(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)
        # no more properties


class _ControlFloorHeatingStatus(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)

        self.temperature_type = None
        self.status = None
        self.mode = None
        self.normal_temperature = None
        self.day_temperature = None
        self.night_temperature = None
        self.away_temperature = None


class _ReadDryContactStatus(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)

        self.switch_number = None


class _CurtainControl(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)

        self.curtain_number = None
        self.action = None


class _ReadStatusOfCurtainSwitch(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)

        self.curtain_number = None


class _ReadAcStatus(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)
        # F-A1: the official read request carries the AC number (Size=1,
        # Index1 = AC No. 1-128). None falls back to 1 in the telegram builder.
        self.ac_number = None


class _ControlAcStatus(_Control):
    def __init__(self, buspro):
        super().__init__(buspro)

        self.ac_number = None
        self.temperature_type = None
        self.current_temperature = None
        self.cooling_temperature = None
        self.heating_temperature = None
        self.auto_temperature = None
        self.dry_temperature = None
        self.mode_and_fan = None
        self.status = None
        self.mode = None
        self.fan_speed = None
        self.current_mode_temperature = None
        self.sweep = None
