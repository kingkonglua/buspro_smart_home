# HA10 修复报告 —— 扫描导入的类型推断缺口

## 结论（第一段）

**HA10 是真 bug，但不是 gap 脚本说的那个样子。** 缺口 A（无回复模块被无脑判成 `switch`）
**完全属实且已修复**：`infer_device_type()` 的 docstring 白纸黑字写着“fall back to the
raw type-code map”，但代码里**从来没有查过 `dev.type_code`**，任何没回复已知操作码的模块
一律落到 `return DEVICE_TYPE_SWITCH`。我新增了 type code → 类型兜底表，修复前后由真实测试
证明（见下）。缺口 B（多区干接点只建 1 个实体）**也属实并已修复**，但 gap 脚本把机制说错了：
本地 `binary_sensor` 平台**本来就支持多区**（`dry_contact_1`/`dry_contact_2` 子类型 +
按 channel 的多个 `dry_contact` 实体），所以“用户没法分别用”这句是错的；真正缺的是
**扫描导入**没有按区拆分（只建 1 个实体）。此外，gap 脚本 HA10 那一项的判据只是
`"HDL_TYPE_TO_DEVICE_TYPE" not in 源码` 这种字符串匹配，本身不构成证据；而它说“dry-contact
本地完全没有”同样错误——`discovery.py:240-241` 早就有 `ReadDryContactStatusResponse → binary_sensor`。

一句话：**缺口 A、B 都是真 bug 且都已修复；gap 脚本对 B 的原因/影响描述不准确。**
（另注：任务书里“上游 const.py:319-424 是空的，0 个 `0x` 条目”这条提醒本身是错的——
`_work/upstream/.../const.py:319` 有一张完整的 18+ 条 type code 表；空的是
`_base_fengxs` 和只有 3 行的 `_base_eyesoft` 的 const.py。）

---

## 1. 修复前 FAIL（真实输出）

命令与结果原样粘贴：

```sh
$ /tmp/hdl_venv/bin/python _work/test_ha10_type_inference.py ; echo "rc=$?"
== T1: type-code fallback for silent modules (gap A) ==
PASS  T1a silent dry-contact is not a keypad
FAIL  T1b silent dry-contact not guessed as switch  -> got='switch'
FAIL  T1c silent dry-contact classified as binary_sensor  -> got='switch'
FAIL  T1d silent 6ch dimmer (0x026D) -> light
FAIL  T1e silent sensors-in-one (0x0150) -> sensor
FAIL  T1f silent curtain module (0x25E5) -> curtain
PASS  T1g silent relay (0x01AC) -> switch is now a *known* answer
PASS  T1h unknown silent type still falls back to switch
== T2: reply operate codes keep priority (regression) ==
PASS  T2a ReadAcStatusResponse -> ac
PASS  T2b ReadStatusOfCurtainSwitchResponse -> curtain
PASS  T2c ReadSensorsInOneStatusResponse -> sensor
PASS  T2d ReadDryContactStatusResponse -> binary_sensor
PASS  T2e ReadStatusOfUniversalSwitchResponse -> binary_sensor
PASS  T2f ReadFloorHeatingStatusResponse -> climate
PASS  T2g reply wins over a conflicting type code (0x0077 + AC reply)
PASS  T2h channel reply without dimmer evidence -> switch even if type is dimmer
== T3: multi-zone dry-contact import (gap B) ==
FAIL  T3a 2-zone dry contact imports 2 entities  -> got 1: ['binary_sensor_1_10_1_dry_contact']
FAIL  T3b zones map to channels 1 and 2  -> channels=[1]
FAIL  T3c SB_DRY_4Z (0x0077) imports 4 entities  -> got 1: ['binary_sensor_1_10_1_dry_contact']
PASS  T3d universal switch stays a single entity

TOTAL PASS=12 FAIL=8
FAILED: T1b silent dry-contact not guessed as switch, T1c silent dry-contact classified as binary_sensor, T1d silent 6ch dimmer (0x026D) -> light, T1e silent sensors-in-one (0x0150) -> sensor, T1f silent curtain module (0x25E5) -> curtain, T3a 2-zone dry contact imports 2 entities, T3b zones map to channels 1 and 2, T3c SB_DRY_4Z (0x0077) imports 4 entities
rc=1
```

> 注意 T1a/T1g/T1h 与全部 T2 在修复前就是 PASS，说明这个测试不是“无脑全红”：
> 它只在真正的缺口上失败，且能证明新表没有破坏“回复操作码优先”的既有判定。

---

## 2. 修复后 PASS（真实输出）

```sh
$ /tmp/hdl_venv/bin/python _work/test_ha10_type_inference.py ; echo "rc=$?"
== T1: type-code fallback for silent modules (gap A) ==
PASS  T1a silent dry-contact is not a keypad
PASS  T1b silent dry-contact not guessed as switch
PASS  T1c silent dry-contact classified as binary_sensor
PASS  T1d silent 6ch dimmer (0x026D) -> light
PASS  T1e silent sensors-in-one (0x0150) -> sensor
PASS  T1f silent curtain module (0x25E5) -> curtain
PASS  T1g silent relay (0x01AC) -> switch is now a *known* answer
PASS  T1h unknown silent type still falls back to switch
== T2: reply operate codes keep priority (regression) ==
PASS  T2a ReadAcStatusResponse -> ac
PASS  T2b ReadStatusOfCurtainSwitchResponse -> curtain
PASS  T2c ReadSensorsInOneStatusResponse -> sensor
PASS  T2d ReadDryContactStatusResponse -> binary_sensor
PASS  T2e ReadStatusOfUniversalSwitchResponse -> binary_sensor
PASS  T2f ReadFloorHeatingStatusResponse -> climate
PASS  T2g reply wins over a conflicting type code (0x0077 + AC reply)
PASS  T2h channel reply without dimmer evidence -> switch even if type is dimmer
== T3: multi-zone dry-contact import (gap B) ==
PASS  T3a 2-zone dry contact imports 2 entities
PASS  T3b zones map to channels 1 and 2
PASS  T3c SB_DRY_4Z (0x0077) imports 4 entities
PASS  T3d universal switch stays a single entity

TOTAL PASS=20 FAIL=0
rc=0
```

---

## 3. 改了什么

### 缺口 A —— `infer_device_type()` 增加 type code 兜底（`custom_components/buspro/discovery.py`）

- 新增 `HDL_TYPE_TO_DEVICE_TYPE`（type code → 扫描分类类型）。
- 在 `infer_device_type()` 里，**保留回复操作码的全部既有优先级**（AC → 窗帘 → 传感器 →
  地暖 → 干接点 → universal switch → keypad → 通道回复），只在**通道分支之后、最终
  `return DEVICE_TYPE_SWITCH` 之前**插入兜底：

  ```python
  mapped = HDL_TYPE_TO_DEVICE_TYPE.get(dev.type_code)
  if mapped is not None:
      return mapped
  ```

  因为它在所有“回复操作码”判定之后，所以只可能让“哑巴模块”更准，永远无法推翻一次
  有效的回复判定（T2g/T2h 正是守住这条）。
- 未识别 type code 仍回退 `switch`（T1h），行为不变。

### 缺口 B —— 多区干接点按区拆分（`custom_components/buspro/discovery.py` + `config_flow.py`）

- `discovery.py` 新增 `HDL_DRY_CONTACT_ZONES`（type code → 区数）与
  `dry_contact_zone_count(dev)`：优先查表，其次用扫描中观测到的最高区号
  （`_on_telegram` 新增对 `ReadDryContactStatusResponse` 的 `payload[1]` 采集，写入
  `channel_count`），最后兜底 1。
- `config_flow._import_discovered()` 的 `DEVICE_TYPE_BINARY_SENSOR` 分支：
  universal switch 仍建 1 个实体；**dry contact 改为对 `1..zones` 每区建 1 个实体**
  （`channel = zone`），命名 `HDL <addr> zoneN`。平台对 `dry_contact` 子类型用
  `switch_number = channel` 轮询 `ReadDryContactStatus`，因此每区可独立使用。

---

## 4. type code 表逐条来源

表分两块。第一块**逐条对应本地 MIT 仓库内的 `DeviceType` 枚举**
（`custom_components/buspro/pybuspro/helpers/enums.py:9-27`），来源可靠且无许可问题；
第二块是上游 `const.py` 记录的真实硬件编码（只取事实性常量，未抄任何实现/结构/注释）。

### 4a. 来自本地 `pybuspro/helpers/enums.py` 的 `DeviceType` 枚举（权威、MIT）

| type code | 枚举名 | 判定类型 | 依据 |
|---|---|---|---|
| `0x0011` | `SB_DN_6B0_10v` | `climate` | 枚举注释 “Rele varme”（加热继电器）= 地暖 |
| `0x0086` | `SB_DLP2` | `climate` | DLP 面板，枚举注释 “DLP” |
| `0x0095` | `SB_DLP` | `climate` | 同上 |
| `0x009C` | `SB_DLP_v2` | `climate` | 同上 |
| `0x0134` | `SB_CMS_12in1` | `sensor` | 枚举注释 “12i1”（12 合 1 传感器）|
| `0x0135` | `SB_CMS_8in1` | `sensor` | 枚举注释 “8i1”（8 合 1 传感器）|
| `0x0150` | `HDL_MSP07M` | `sensor` | 枚举注释 “Sensors in One” |
| `0x0260` | `SB_DN_DT0601` | `light` | 枚举注释 “6ch Dimmer” |
| `0x026D` | `HDL_MDT0601` | `light` | 枚举注释 “6ch Dimmer ny type” |
| `0x01AC` | `SB_DN_R0816` | `switch` | 枚举注释 “Rele”（继电器）|
| `0x0077` | `SB_DRY_4Z` | `binary_sensor` | 枚举注释 “Dry contact” |

### 4b. 上游 `const.py` 记录的真实硬件编码（仅事实常量）

来源文件：`_work/upstream/custom_components/ar_hdl_buspro/const.py:319-424`
（该表在注释里自述 “Codes mirror the DeviceType enum in pybuspro/helpers/enums.py”）。

| type code | 判定类型 | 依据（上游注释原文摘要）|
|---|---|---|
| `0x0073` | `binary_sensor` | “4-zone dry contact input module (reported by @marsh4200)” |
| `0x0166` | `binary_sensor` | “HDL-MS24.232 (SB-DN-DRY-24Z) 24-zone dry contact module” |
| `0x0138` | `sensor` | “CMS sensor (temp / lux / motion)” |
| `0x0148` | `sensor` | “HDL-MSP07M.4C sensors-in-one” |
| `0x25E5` | `curtain` | “curtain module (ARSmartHome site)” |
| `0x25E8` | `curtain` | “curtain module (ARSmartHome site)” |
| `0x02C9` | `curtain` | “HDL-MW02.431 2ch curtain controller” |

### 4c. 多区表 `HDL_DRY_CONTACT_ZONES` 来源

| type code | 区数 | 依据 |
|---|---|---|
| `0x0077` | 4 | 本地枚举 `SB_DRY_4Z`（4-zone dry contact，名称即 4Z）|
| `0x0073` | 4 | 上游注释 “4-zone dry contact input module” |
| `0x0166` | 24 | 上游注释 “24-zone dry contact” / “SB-DN-DRY-24Z” |

**未收录的编码（故意不猜）**：`0x0453` 逻辑模块、`0x0BE9` 安全模块、`0xFFFC/0xFFFD/0xFFFE`
软件/测试地址等没有明确可控实体语义，保持不映射（回退 `switch`），避免凭印象乱分类。

---

## 5. 缺口 B 的机制澄清（重要）

任务书引用的是 `pybuspro/devices/sensor.py:60-61` 的
`_dry_contact_1_status` / `_dry_contact_2_status`，这两个字段只在
`ReadSensorStatusResponse` / `ReadSensorsInOneStatusResponse` / 广播帧里填充——
它们属于 **sensors-in-one / CMS 多传感器模块**的两个板载干接点输入。这类模块在扫描里被
判成 `DEVICE_TYPE_SENSOR`，导入成 **1 个 sensor 实体**，走的是另一条路径，和“专用干接点
输入模块（`SB_DRY_4Z`，用 `ReadDryContactStatus` + `switch_status`）”不是一回事。

因此：
- “两区被塞进一个 binary_sensor 实体”这句**不准确**：这些区来自 sensor 路径，根本没进 binary_sensor。
- 但“多区干接点模块导入只建 1 个实体”对**专用干接点输入模块**是**真的**，且平台有
  `dry_contact_1`/`dry_contact_2` 与按 channel 的 `dry_contact` 能力，所以完全可修——已修。
- 结论：缺口 B 的**缺陷现象存在**，但 gap 脚本的**归因与影响描述错误**。

---

## 6. 回归结果

`/tmp/hdl_venv/bin/python` 跑全部 `_work/test_*.py`：

- `_work/test_ha10_type_inference.py` → **rc=0（20/20 PASS）**
- `test_device_type_i18n.py` → rc=0（25 PASS / 0 FAIL）
- `test_bug8_service_schemas.py` → rc=0（FAIL=0）
- `tests/test_panel_ac.py` → rc=0（ALL PASSED）
- 其余 `test_ac_curtain / cover_* / e2e / fix_bugs / g8_startup_burst /
  hooks_wired / i18n_* / m8 / m9_m10 / multi_gateway / payload_none /
  progress_i18n / r2 / r6 / r7_r5 / reconnect_resync / regression_deep /
  soak / travel_time_types` → **全部 rc=0**
- `test_gaps_ha.py` → rc=2、`test_gaps_pybuspro.py` → rc=2：**审计脚本按设计如此**
  （发现 gap 即 exit 2），未改动。且修复后 `test_gaps_ha.py` 已把 HA10 列进
  “NOT confirmed”（因为 `HDL_TYPE_TO_DEVICE_TYPE` / `HDL_DRY_CONTACT_ZONES` 现已存在），
  侧面印证常量型判据已满足。

---

## 7. 边界与未做

- 未改 `pybuspro/devices/device.py`（G8 地盘）。
- 未改任何现有测试断言，未删测试。
- `dev.type_code` 只在展示和兜底分类中使用；`infer_device_type` 的回复操作码优先级
  一字未动。
- universal switch 模块仍按单实体导入（任务只要求干接点多区），未扩大改动面。
