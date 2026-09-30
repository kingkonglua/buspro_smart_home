# 河东 HA 集成 — 代码审查 / Bug 与缺陷报告

审查范围：`custom_components/buspro/` 全部自定义代码（含 `pybuspro/` 传输与设备层、`config_flow.py`、`discovery.py`、`gateway_discovery.py` 及各平台）。
`_base_eyesoft` / `_base_fengxs` 仅作对照，不在本报告范围。

严重程度说明：**🔴 严重**（必现崩溃或功能失效）、**🟠 中**（特定条件下失效/错误）、**🟡 低/设计**（健壮性、兼容性、可维护）。

---

## 🔴 严重 Bug

### 1. `climate.py` 地暖 `hvac_action` 用到不存在的枚举 → 必然崩溃
文件：`custom_components/buspro/climate.py:287`

```python
if self._relay_sensor_is_on is None:
    return HVACAction.HEAT      # ← HVACAction 没有 HEAT 成员
```

Home Assistant 的 `HVACAction` 枚举只有 `HEATING / COOLING / DRYING / FAN / IDLE / OFF / OTHER`，**没有 `HEAT`**。
而 `BusproClimate` 在 `async_setup_entry` 中始终以 `relay_sensor=None` 实例化（`climate.py:146`），于是 `self._relay_sensor_is_on` 恒为 `None`，导致 `hvac_action` 每次被读取时都抛出 `AttributeError`。HA 在渲染地暖卡片时会调用该属性，结果地暖实体报错/不可用、日志刷屏。

**修复**：`return HVACAction.HEATING`（与同函数其它分支一致）。

---

### 2. `climate.py` 地暖 `async_set_temperature` 在 mode 未知/为 Timer 时 KeyError
文件：`custom_components/buspro/climate.py:342`

```python
preset = HDL_TO_HA_PRESET[self._mode]
```

`HDL_TO_HA_PRESET` 的键只有 `{1,2,3,4}`。但：
- 设备首次读取前（前 5 秒，`climate.py` 的 `run_from_init` sleep 5s）`self._mode` 为 `None`；
- HDL `TemperatureMode.Timer = 5`，也会落入此分支。

任一情况下 `HDL_TO_HA_PRESET[None]` / `[5]` 直接抛 `KeyError`，用户在状态回填前点调温必崩。

**修复**：用 `.get(self._mode)` 并在缺失时回退到 `normal_temperature`，或先确保 mode 有默认值。

---

### 3. `ac.py` 电源状态未知时丢弃所有控制命令（含首次 3 秒 + 空调不回状态即永久失控）
文件：`custom_components/buspro/pybuspro/devices/ac.py:96-110`

`control()` 在 `new_status is None` 时直接 `return`，跳过发送。这使 `set_mode / set_fan_speed / set_temperature / set_sweep` 在电源状态未知时全部静默失效。

后果：
- 集成加载后前 3 秒（`_call_read_current_status` 的 sleep 3s）内，**任何调节（温度/模式/风速/扫风）都被丢弃**，用户无反馈。
- 若空调模块在关机/异常态下不响应 `ReadAcStatus`（常见），`self._status` 永远为 `None` → 该空调实体**永久无法通过 HA 控制**。这是 README 所谓“安全特性”的副作用，代价是设备不可控。

**连带 Bug（更隐蔽）**：`climate.py:511-512`

```python
await self._device.set_mode(ac_mode)     # status 未知 → 被丢弃，模式没设上
if not self._device.is_on:               # is_on 仍 None → 视为关
    await self._device.turn_on()         # 仅发 status=1，mode 回退默认 COOL
```

于是用户选“制冷/制热/自动”时，空调实际被以 **COOL（制冷）** 开机——**请求的模式被丢失**。

**建议**：状态未知时不要整条丢弃，而是带着“请求的那一项变更 + 安全默认”直接发送（例如 `turn_on` 时把请求的 mode 一并带出）；并对“始终读不到状态”的情况增加显式告警/回退策略。

---

### 4. `ac.py` 发送帧的 `mode_and_fan` 字节被写死为常量 0x30
文件：`custom_components/buspro/pybuspro/devices/ac.py:41,174` 与 `control.py:77`

HDL `ControlAcStatus (0x193A)` 第 7 字节（payload 索引 7）是 **mode + fan 的组合字节**（高 4 位=模式，低 4 位=风速）。本实现：
- 把 `self._mode_and_fan` 初始化为常量 `48`(0x30)；
- 读取响应时仅把它存进 `self._mode_and_fan`（`ac.py:66`）但**从不根据 mode/fan_speed 更新它**；
- 发送时 `cc.mode_and_fan = self._mode_and_fan`（恒为 0x30 = 自动模式+自动风），同时又单独发送 `mode`(索引9) 和 `fan_speed`(索引10)。

真实 HDL 硬件多数以第 7 字节为准。若是如此，**无论 HA 选什么模式/风速，空调都被命令为“自动模式+自动风”**，即空调控制（本集成的头号新增功能）实际无效。此点需实机验证，但风险极高。

**建议**：发送时按 `mode_and_fan = (mode << 4) | fan_speed` 重新计算，或确认硬件确实读索引 9/10 并在代码注释中固定下来。

---

### 5. `universal_switch.py` / `button.py` 万能开关“开”用了 `OnOff.ON=255`
文件：`pybuspro/devices/universal_switch.py:37`、`control.py:36`、`button.py:105`、`__init__.py:177`

`OnOff` 枚举为 `OFF=0, ON=255`，但 HDL `UniversalSwitchControl (0xE01C)` 的 payload 为 `[switch_number, status]`，**status 应为 0/1**（另有 `SwitchStatusOnOff.ON=1` 枚举专为此存在，却被忽略）。

影响：
- `button.py` 的“按下”脉冲发送 `255`，模块很可能不识别 → **按钮/场景脉冲失效**；
- `__init__.py` 的 `set_universal_switch` 服务 `status==1 → set_on()` 同样发 `255`。

**修复**：改用 `SwitchStatusOnOff.ON`(=1) / `SwitchStatusOnOff.OFF`(=0)。

---

## 🟠 中等缺陷

### 6. `cover.py` curtain_module 子类型无法正确反映开/关状态
文件：`custom_components/buspro/cover.py:178-184`

HDL 窗帘反馈只有 0=停 / 1=开 / 2=关**动作态**，到达限位后通常回报 0（停），**不区分停在开还是关**。而本代码：
```python
self._attr_is_opening = self._device.is_moving and not self._device.is_closed
self._attr_is_closing = self._device.is_moving and self._device.is_closed
...
self._attr_is_closed = self._device.is_closed   # = (status == 2)
```
对 `curtain_module`（无位置估算）子类型，`is_closed` 完全依赖 `status==2`。若硬件在静止时回 0（停），则 `is_closed` 永远为 `False`（显示为开）；若回 2，则静止时也判为“正在关”。**对纯窗帘模块，关态永远不可靠。**

**建议**：在文档/配置中说明 `curtain_module` 仅支持开/关/停动作、不保证关态；或依赖 `bus_motor` 子类型做位置估算来推断开闭。

---

### 7. `sensor.py` 平台 `device_class` / 单位使用字符串而非枚举（新版 HA 兼容隐患）
文件：`custom_components/buspro/sensor.py:135-148`

`device_class` 返回 `"temperature"`/`"illuminance"` 字符串，`native_unit_of_measurement` 返回 `"°C"`/`"lux"` 字符串。新版 Home Assistant 要求 `SensorDeviceClass` 与 `UnitOfTemperature` 等枚举，字符串形式已废弃并在部分版本直接报错/丢失 long-term statistics。

**修复**：`device_class=SensorDeviceClass.TEMPERATURE`、`unit_of_measurement=UnitOfTemperature.CELSIUS`。

---

### 8. `binary_sensor.py` `device_class` 恒为 None，且 `is_on` 可能返回 None
文件：`custom_components/buspro/binary_sensor.py:138-140, 151-164`

- `device_class` 始终是 `None`，对 `motion` 子类型应设为 `BinarySensorDeviceClass.MOTION`，否则失去设备类语义。
- `is_on` 在 `CONF_MOTION` 时返回 `self._device.movement`；而 `Sensor.movement`（`pybuspro/devices/sensor.py:198`）在初始值非 0/非(0,0) 时**隐式返回 None** → 实体状态为 unknown。
- `CONF_DRY_CONTACT_1/2` 在配置流 `BINARY_SENSOR_SUBTYPES` 中提供，但 `is_on` 分支齐全，OK；不过 `device_class` 仍缺失。

---

### 9. `__init__.py` 端口默认值错误 + `connected` 永远为 True
文件：`custom_components/buspro/__init__.py:72, 139`

- `port = config_entry.data.get(CONF_PORT, 1)`：缺省应为 `6000`（`const.DEFAULT_PORT`），写成 `1` 是笔误（配置流一般会给值，属防御性缺陷）。
- `start()` 无论 UDP 套接字是否真正绑定成功都把 `self.connected = True`（`udp_client._connect` 失败只打 warning 不抛错）。于是网关不可达时所有实体仍显示 available，用户无从察觉。

---

### 10. `discovery.py` 分类映射表是死代码，且存在维护隐患
文件：`custom_components/buspro/discovery.py:110-142`

`DEVICE_TYPE_BY_RESPONSE`（按原始字节 `b"\x00\x34"` 等）和 `DEVICE_TYPE_BY_RESPONSE_NAME`（按枚举名）都被定义，但 `infer_device_type()` **完全用硬编码字符串**重新写了一遍，这两个表从未被使用。一旦枚举名变动（注意 `enums.py` 里 OperateCode 含大量被注释掉的重复定义，`0x1944` 同时出现在 `ReadFloorHeatingStatus` 和注释中的 `QUERY_DLP_FROM_SETUP_TOOL_2`），分类逻辑会与表悄悄失同步。

**建议**：让 `infer_device_type` 直接消费 `DEVICE_TYPE_BY_RESPONSE_NAME`，删除重复硬编码。

---

## 🟡 低 / 设计层面

### 11. `cover.py` 移动过程中位置不刷新 UI
`current_cover_position` 由 `_estimate_position()` 实时计算，但 `async_write_ha_state()` 只在收到电报或停止时调用。窗帘电机若运行期间不再回包，HA 界面上的进度条/百分比不会动，直到停止。`_schedule_stop` 计时结束后才刷新。属体验缺陷。

### 12. `config_flow.py` 总线扫描任务在流程取消/重入时泄漏
`async_step_scan_bus` 用 `self.hass.async_create_task` 启动扫描；若用户在进度中退出/重进 Options 流程，`self` 为新实例，旧任务继续跑但结果丢失，且无人 cancel。建议保存 task 引用并在 `async_on_close`/重入时 `cancel()`。

### 13. `discovery.py` 设备分类对“普通调光器”识别不足
`is_light` 仅当收到中间亮度值（1–254 且 ≠100）才算 dimmer，否则归为 `switch`。部分固件只回 0/100/255，导致真实调光器被识别成开关，用户需手动改类型。属已知权衡，建议在 UI 提示。

### 14. `cover.py` / 各平台使用已废弃的 `state` 属性
`cover.py` 的 `state` 属性返回 `"opening"/"closing"` 等字符串；HA 现已用 `is_opening/is_closing/is_closed` 替代，`state` 属性逐步弃用。

### 15. `config_flow.py` 缺少输入校验
`async_step_manual` 的 `CONF_PORT` 仅 `int` 无范围；`CONF_HOST` 可为空字符串。建议在 schema 加 `vol.Range` 与非空校验。

### 16. `binary_sensor.py` `CONF_MOTION` 初始 `movement` 为 None
与第 8 点同源：`Sensor.__init__` 中 `_motion_sensor=None`，首帧到达前 `is_on` 为 None。可在实体初始 `available` 或 `is_on` 中显式处理 None。

### 17. `ac.py` 乐观更新在发送前赋值
`control()` 在 `await cc.send()` 之前就把缓存状态改写为期望值（`ac.py:182-190`）。若 `send()` 因传输层未连接而静默丢弃（见第 9 点 `udp_client.send_message` 仅打日志），UI 显示与真实设备脱节。

---

## 优先修复清单（建议顺序）
1. 第 1 点 `HVACAction.HEAT` → 改为 `HEATING`（一行，立刻消除地暖崩溃）。
2. 第 2 点 `HDL_TO_HA_PRESET[self._mode]` 加 `.get` 与回退。
3. 第 5 点 万能开关 `255` → `1`（影响按钮/场景）。
4. 第 3、4 点 空调控制：状态未知时的发送策略 + `mode_and_fan` 组合字节计算（需实机核对协议）。
5. 第 7、8 点 传感器/二进制传感器枚举化，避免新版 HA 报错。
6. 余下中等/低项按需处理。

> 注：第 4、6 点涉及 HDL 协议细节，建议用真实网关/空调面板抓包核对 `0x193A` / `0xE3E0` 实际字节含义后再定稿修复。
