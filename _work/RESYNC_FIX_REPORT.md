# RESYNC_FIX_REPORT — BUG-1（重连后实体状态不刷新） + BUG-2（Light/Switch read_status 未实现）

日期：2026-10-01
项目根：`/vol1/1000/资料/编程项目/河东HA集成`
组件根：`custom_components/buspro`

---

## 1. 问题与位置

### BUG-1：网关重连成功后实体状态永久停留在断线前旧值
- 位置：`custom_components/buspro/__init__.py::_reconnect_loop()`
- 修复前（原始第 578-588 行附近）：重连成功后只做
  `_async_refresh_source_filter()` + `_async_refresh_advertised_ip()` +
  `connected = True` + 日志，**没有任何设备状态重读**。
- 实体 `available`（`light.py:109`、`binary_sensor.py:140`、`entity.py:84` 等）
  在 `connected=True` 后立即变回 available，但底层 `Device` 对象仍持有断线前
  的 `_brightness` / 温度 / 湿度等旧值，直到有人现场操作才更新。
- `start()` / `_reconnect_loop()` 都调用 `hdl.start(state_updater=False)`，
  `pybuspro/buspro.py:110-112` 表明只有 `state_updater=True` 才会起周期性
  `StateUpdater`，所以系统没有轮询兜底。

### BUG-2：Light / Switch 的 `read_status()` 抛 `NotImplementedError`
- 位置：
  - `custom_components/buspro/pybuspro/devices/light.py:52`（原始）
  - `custom_components/buspro/pybuspro/devices/switch.py:42`（原始）
- 二者 `__init__` 已经在调用 `self._call_read_current_status_of_channels(run_from_init=True)`，
  `_telegram_received_cb` 也已处理 `OperateCode.ReadStatusOfChannelsResponse`，
  说明设备本就支持 0xE0 通道状态读取，`read_status` 抛异常是**未完成**。
- 上游参考实现（`_work/upstream/custom_components/ar_hdl_buspro/pybuspro/devices/light.py:65-78`、
  `switch.py:58-71`）以及上游实体基类 `entity.py:200-231` 的做法是：
  `read_status()` 直接发 `_ReadStatusOfChannels`，重连时由实体对
  `_resync_device` 调用一次 `await read_status()`。

---

## 2. 改法

### BUG-2（先修，BUG-1 的 resync 依赖它）
`light.py` / `switch.py` 的 `read_status()` 实现为发送真实的 0xE0 请求：

```python
async def read_status(self):
    reader = _ReadStatusOfChannels(self._buspro)
    reader.subnet_id, reader.device_id = self._device_address
    await reader.send()
```

说明：任务书建议写成 `self._call_read_current_status_of_channels(run_from_init=True)`。
这里改为**直接 await 同一个 `_ReadStatusOfChannels` 控制**，原因是：
1. 上游/探针 `_work/test_gaps_pybuspro.py` G10 的参考实现就是直接 send；
2. `_call_read_current_status_of_channels(run_from_init=True)` 是
   fire-and-forget 的 `asyncio.create_task`，且 `run_from_init=True` 会
   **先 sleep 3 秒**才发送。重连 resync 需要“立即重读”，而且要求
   “重读抛异常不得让重连流程崩掉、必须能被 try/except 捕获”——任务里的异常
   无法被重连流程的 try/except 捕捉。直接 await 的 send 复用了同一套
   `_ReadStatusOfChannels` 机制，同时满足可等待、可捕获、无 3 秒延迟。

### BUG-1
`custom_components/buspro/__init__.py::_reconnect_loop()`，在
`self.connected = True` 与 “Reconnected …” 日志之后新增一次性 resync 调用
（第 589-596 行），并新增两个方法：

- `BusproModule._async_resync_devices()`（第 599 行起）：遍历
  `self.hdl._telegram_received_cbs`，从回调 bound method 的 `__self__` 取出
  每个已注册 `Device`，按对象去重，逐台重读；每台设备用独立 try/except 包住并
  `_LOGGER.debug`，任何一台失败都不影响其余设备、也不影响重连成功。
- `BusproModule._async_resync_device(device)`（第 628 行起）：优先 `await
  device.read_status()`；没有 `read_status` 的设备（如 Sensor、floor-heating
  climate）回退调用基类 `device._call_read_current_status_of_channels()`，
  复用既有机制，不新增第二个轮询路径。

约束满足：
- 复用 `Device._call_read_current_status_of_channels()` / `_ReadStatusOfChannels`，
  未新增常驻轮询线程或定时任务（每次重连一次性 pass，任务都是短生命周期）；
- resync 失败被 try/except 捕获并 `_LOGGER.debug`，重连仍算成功；
- 未改动 `_notify_connection_lost()` 的事件行为。

白名单内改动：
- `custom_components/buspro/__init__.py`
- `custom_components/buspro/pybuspro/devices/light.py`
- `custom_components/buspro/pybuspro/devices/switch.py`
- 新增 `_work/test_reconnect_resync.py`、本报告

---

## 3. 修复前真实 FAIL 输出

命令：`python3 _work/test_reconnect_resync.py`（原始代码，未修复）

```
FAIL BUG-1 reconnect resync: BUG-1: devices were NOT re-read after reconnect: ['(1, 10)-1', '(1, 12)-1'] (calls={})
FAIL BUG-2 read_status: BUG-2: Light.read_status() raised NotImplementedError: NotImplementedError()

2 check(s) failed
```

退出码：`1`

---

## 4. 修复后真实 PASS 输出

命令：`python3 _work/test_reconnect_resync.py`

```
PASS BUG-1: resync re-read every registered device; failure tolerated (calls={'(1, 10)-1': 1, '(1, 12)-1': 1}, connected=True)
PASS BUG-2: Light.read_status() -> ReadStatusOfChannels telegram for (1, 20)
PASS BUG-2: Switch.read_status() -> ReadStatusOfChannels telegram for (1, 21)

All reconnect/resync checks passed
```

退出码：`0`

测试覆盖：
- 构造假 `hdl`/transport，调用真实的 `BusproModule._reconnect_loop()`；
- 断言重连后每个已注册 Light 都恰好收到一次 `read_status()` 调用；
- 中间插入一个 `read_status` 抛 `RuntimeError` 的设备，断言后续设备仍被重读、
  且 `module.connected is True`；
- 断言 `Light/Switch.read_status()` 发出的 telegram `operate_code ==
  OperateCode.ReadStatusOfChannels`。

---

## 5. 回归输出（全部通过）

```
python3 -m py_compile $(find custom_components/buspro -name "*.py")   -> OK
python3 _work/verify_step_translations.py        -> [4/4] PASS — 0 缺口            EXIT=0
python3 _work/verify_final_regression.py         -> 13/13 checks passed             EXIT=0
python3 _work/verify_imports.py                  -> own-code errors: 0              EXIT=0
python3 _work/test_soak.py                       -> No unbounded growth detected    EXIT=0
python3 _work/test_e2e.py                        -> (S7 cases)                       EXIT=0
python3 _work/test_r2_setup_retry.py             -> RESULT: ALL_PASS                 EXIT=0
python3 _work/test_r6_port_fallback.py           -> RESULT: ALL_PASS                 EXIT=0
python3 _work/test_r7_r5_verify.py               -> RESULT: ALL_PASS                 EXIT=0
python3 _work/test_hooks_wired.py                -> Ran 4 tests ... OK               EXIT=0
python3 _work/test_m8_multigateway_lifecycle.py  -> Ran 3 tests ... OK               EXIT=0
python3 _work/test_multi_gateway.py              -> Ran 5 tests ... OK               EXIT=0
python3 _work/test_payload_none.py               -> Ran 5 tests ... OK               EXIT=0
python3 _work/test_m9_m10.py                     -> Ran 15 tests ... OK              EXIT=0
python3 _work/test_travel_time_types.py          -> Ran 16 tests ... OK              EXIT=0
python3 _work/test_regression_deep.py            -> Ran 43 tests ... OK              EXIT=0
python3 _work/test_ac_curtain.py                 -> Ran 24 tests ... OK              EXIT=0
python3 _work/test_cover_drift.py                -> Ran 3 tests ... OK               EXIT=0
python3 _work/test_cover_state_machine.py        -> Ran 9 tests ... OK               EXIT=0
python3 _work/test_cover_stop_flag.py            -> Ran 1 test ... OK                EXIT=0
python3 _work/test_fix_bugs.py                   -> Ran 27 tests ... OK              EXIT=0
python3 tests/test_panel_ac.py                   -> ALL PASSED                       EXIT=0
```

回归命令全部退出码 0（含 `test_soak.py`：2.80 s，simulated 24 h，No unbounded
growth detected，证明未引入常驻任务泄漏）。

已知基线（非回归，不作为验收）：
```
python3 _work/test_gaps_pybuspro.py  -> 5/15 gaps reproduced   EXIT=2
    G10 "Light/Switch read_status() resync missing" 现为 [NO GAP ...]（已修复）
python3 _work/test_gaps_ha.py        -> 21/23 gap checks confirmed   EXIT=2
```

---

## 6. 剩余风险

- resync 通过 `hdl._telegram_received_cbs` 中回调的 `__self__` 枚举设备，
  依赖设备用 bound method 注册（现有全部设备均如此）。若未来出现非 bound
  method 的回调，该设备会被跳过；但由于包在 try/except 内，不会导致重连失败。
- Sensor / floor-heating climate 没有 `read_status`，resync 走基类
  `_call_read_current_status_of_channels()`（fire-and-forget 任务）；这些设备
  本身还有各自的读取逻辑，重连时能拿到状态，但不在本任务强验证范围。
- `UniversalSwitch.read_status()`（`universal_switch.py:47`）仍是
  `NotImplementedError`，不在白名单内未修改；若该类被注册，resync 会调用它、
  抛错被捕获并 debug 记录，重连不受影响。
- resync 是“每个重连事件一次”的一次性 pass，无周期轮询；若网关持续抖动，
  每次重连都会重读一次，属预期行为。
