# 河东 HA 集成 Bug 修复报告（2026-08-27）

审查报告：BUG_REVIEW.md（17 个问题）
修复数量：9 个（5 个致命 + 4 个中等）

---

## 修复清单

### Bug 1: climate.py — HVACAction.HEAT → HEATING
- **文件**：climate.py（第 287 行附近）
- **问题**：`HVACAction.HEAT` 不存在，HA 的地暖 action 是 `HEATING`
- **修复**：改为 `HVACAction.HEATING`

### Bug 2: climate.py — 首次加载 KeyError
- **文件**：climate.py（`update()` 方法和 `preset_mode` 属性）
- **问题**：`HDL_TO_HA_PRESET[self._mode]` 首次加载 `_mode=None` 时 KeyError
- **修复**：改为 `HDL_TO_HA_PRESET.get(self._mode, PRESET_NONE)`

### Bug 3: ac.py — mode_and_fan 写死 0x30
- **文件**：pybuspro/devices/ac.py
- **问题**：`mode_and_fan` 字节始终写死为 0x30（自动模式+自动风），无法反映用户选择的 mode/fan_speed
- **修复**：增加 `MODE_BYTE_MAP` 和 `FAN_BYTE_MAP`，根据 `mode` 和 `fan_speed` 动态计算 `(mode_byte << 4) | fan_byte`

### Bug 4: ac.py — 状态未知时 control() 静默 return
- **文件**：pybuspro/devices/ac.py
- **问题**：`control()` 方法中 `if new_status is None: return` 直接丢弃命令，状态首次未知时永久无法控制
- **修复**：去掉 return，改为 `new_status = 1`（默认 ON），信任 HA 上层幂等保护

### Bug 5: universal_switch.py — OnOff.ON=255 → SwitchStatusOnOff.ON=1
- **文件**：pybuspro/devices/universal_switch.py
- **问题**：`OnOff.ON` 值为 255，HDL 协议期望 `SwitchStatusOnOff.ON=1`
- **修复**：`OnOff.ON/OFF` 替换为 `SwitchStatusOnOff.ON/OFF`

### Bug 6: cover.py — is_closed 注释补充
- **文件**：cover.py
- **问题**：代码注释不清，关态判断依据不明确
- **修复**：补充详细注释说明 status=2=关，status=1=开，status=0=停

### Bug 7: sensor.py — device_class 字符串 → 枚举
- **文件**：sensor.py
- **问题**：`device_class="temperature"/"illuminance"` 用字符串，新版 HA 要求 `SensorDeviceClass` 枚举
- **修复**：`SensorDeviceClass.TEMPERATURE` / `SensorDeviceClass.ILLUMINANCE`

### Bug 8: binary_sensor.py — motion 子类 device_class
- **文件**：binary_sensor.py
- **问题**：motion 类 binary_sensor 的 `device_class` 恒为 None，图标和自动化不匹配
- **修复**：motion 子类返回 `BinarySensorDeviceClass.MOTION`

### Bug 9: __init__.py — port 默认值 1 → 6000
- **文件**：__init__.py（第 72 行）
- **问题**：`port = config_entry.data.get(CONF_PORT, 1)`，缺省值 1 不是 HDL 标准端口
- **修复**：改为 `port = config_entry.data.get(CONF_PORT, 6000)`

---

## 未修复（已标记，可后续处理）

- Bug 10-17（低/设计类）：位置不刷新、扫描任务泄漏、dead code 等
- 需实机验证 sensor subtype 下拉框在 HA UI 上的渲染