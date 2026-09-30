# I18N no.json 修复报告

日期：2026-10-01
项目：`/vol1/1000/资料/编程项目/河东HA集成`
集成：`custom_components/buspro/`

## 1. 结论

`translations/no.json` 不仅缺 `services` 段，实际全量递归 diff 还发现 **整个 `options.step`
段也缺失**（上游 watchdog 声称「options.step 齐了」是错的）。本次一并在 `no.json`
中补齐，均为挪威语 Bokmål。其余语言基准文件未改动。

- 修复前：`_work/test_i18n_parity.py` → **FAIL，139 处断言失败，exit=1**
- 修复后：`_work/test_i18n_parity.py` → **PASS，exit=0**
- 全量 `py_compile` 回归：无 FAIL

## 2. 改动文件

| 文件 | 操作 | 说明 |
| --- | --- | --- |
| `custom_components/buspro/translations/no.json` | 修改 | 新增顶层 `services`（3 服务）+ `options.step`（15 step） |
| `_work/test_i18n_parity.py` | 新增 | i18n 键位一致性回归测试（断言 A–E） |

`git diff --stat`：

```
 custom_components/buspro/translations/no.json | 180 ++++++++++++++++++++++++++
 1 file changed, 180 insertions(+)
```

> 未改动 `strings.json` / `zh-Hans.json` / `en.json`，也未改动任何 `.py` 业务代码。
> `_work/` 被 `.gitignore` 忽略（`.gitignore:10:_work/`），沿用历史 `git add -f` 纳入。

## 3. 每处改动前后对比

### 3.1 新增顶层 `services`（修复前完全不存在）

修复前（no.json 顶层只有两个 key）：

```json
{
  "config": { ... },
  "options": { ... }
}
```

修复后新增（节选，结构逐字对齐 `strings.json`，value 为挪威语）：

```json
  "services": {
    "activate_scene": {
      "name": "Aktiver scene",
      "description": "Aktiver en scene på HDL Buspro-bussen: ...",
      "fields": {
        "address": {
          "name": "Enhetsadresse",
          "description": "Målenhetens adresse, format [undernett-ID, enhets-ID], f.eks. [1, 42]."
        },
        "scene_address": {
          "name": "Sceneadresse",
          "description": "Område- og scenenummeret som skal aktiveres, format [områdenummer, scenenummer], f.eks. [1, 1]."
        }
      }
    },
    "send_message": {
      "name": "Send melding",
      "description": "Send en rå HDL Buspro-styremelding ...",
      "fields": {
        "address": { "name": "Enhetsadresse", "description": "..." },
        "operate_code": { "name": "Operasjonskode", "description": "HDL Buspro-operasjonskode, format [høy byte, lav byte], f.eks. [1, 1]." },
        "payload": { "name": "Nyttelast", "description": "Nyttelastbyttene som skal sendes, f.eks. [1, 100]." }
      }
    },
    "set_universal_switch": {
      "name": "Sett universell bryter",
      "description": "Slå den universelle bryterutgangen på eller av: status 1 slår på, 0 slår av.",
      "fields": {
        "address": { "name": "Enhetsadresse", "description": "..." },
        "switch_number": { "name": "Bryternummer", "description": "Nummeret på den universelle bryteren som skal styres (1–255)." },
        "status": { "name": "Status", "description": "Ønsket tilstand: 1 betyr på, 0 betyr av." }
      }
    }
  }
```

### 3.2 新增 `options.step`（修复前完全不存在）

修复前 `options` 只有 `progress` / `error` / `abort`，没有 `step`。
修复后补齐 15 个 step：`init`, `add_device`, `add_light`, `add_switch`,
`add_binary_sensor`, `add_sensor`, `add_cover`, `add_button`, `add_climate`,
`add_scene`, `remove_device`, `scan_bus`, `scan_bus_no_devices`, `scan_bus_devices`。
（节选）

```json
    "step": {
      "init": {
        "title": "Integrasjonshandlinger",
        "description": "Velg en handling:",
        "data": { "action": "Handlingstype" }
      },
      "add_cover": {
        "title": "Legg til gardin",
        "description": "Skriv inn adresse, kanal og navn for gardinenheten.",
        "data": {
          "subnet_id": "Undernett-ID",
          "device_id": "Enhets-ID",
          "channel": "Kanal / gardinnummer",
          "subtype": "Gardintype",
          "travel_time": "Kjøretid i sekunder (for posisjonsestimering)",
          "name": "Navn (valgfritt)"
        }
      },
      "scan_bus_no_devices": {
        "title": "Ingen enheter funnet",
        "description": "Busskanningen ble fullført, men fant ingen enheter. Sjekk gateway-konfigurasjonen og bussforbindelsen."
      },
      ...
    },
```

**注意**：`options.step` 的 key 集合与 `strings.json` 完全一致，`no.json` 全量 path
diff 已无任何缺失。

## 4. 校验脚本

`_work/test_i18n_parity.py`，可重复运行：

```bash
cd /vol1/1000/资料/编程项目/河东HA集成
python3 _work/test_i18n_parity.py; echo "exit=$?"
```

断言：

- **A**：4 个 JSON 全部 `json.load` 成功。
- **B**：每个 translation 顶层 key 集合 == `strings.json`（不许多、不少）。
- **C**：递归比对，translation 每个 path 在 `strings.json` 中存在；`services` 下
  逐 path 完全一致（missing 与 extra 都必须为空）。
- **D**：`config.abort` key 集合 ⊇ `config_flow.py` 中所有
  `async_abort(reason="...")` 提取出的 reason。
- **E**：`config.step` 覆盖 `config_flow.py` 中所有会调用
  `async_show_form` / `async_show_progress_done` 的 step。

## 5. FAIL / PASS 真实输出

### 5.1 修复前 FAIL（真实 stdout，exit=1）

> 获取方式：`git stash push -- custom_components/buspro/translations/no.json` 后运行，
> 再 `git stash pop` 恢复。完整日志见 `_work/i18n_fail_before.log`。

```
== A. json.load all 4 files ==
  OK   custom_components/buspro/strings.json
  OK   custom_components/buspro/translations/zh-Hans.json
  OK   custom_components/buspro/translations/en.json
  OK   custom_components/buspro/translations/no.json
== B. top-level key set parity ==
  OK   custom_components/buspro/translations/zh-Hans.json top keys == ['config', 'options', 'services']
  OK   custom_components/buspro/translations/en.json top keys == ['config', 'options', 'services']
FAIL: B: custom_components/buspro/translations/no.json top keys mismatch; missing=['services'] extra=[]
== C. recursive path parity ==
  OK   custom_components/buspro/translations/zh-Hans.json has every strings.json path
  OK   custom_components/buspro/translations/zh-Hans.json services paths == strings.json
  OK   custom_components/buspro/translations/en.json has every strings.json path
  OK   custom_components/buspro/translations/en.json services paths == strings.json
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_binary_sensor
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_binary_sensor.data
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_binary_sensor.data.channel
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_binary_sensor.data.device_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_binary_sensor.data.name
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_binary_sensor.data.subnet_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_binary_sensor.data.subtype
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_binary_sensor.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_binary_sensor.title
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_button
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_button.data
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_button.data.channel
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_button.data.device_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_button.data.name
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_button.data.subnet_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_button.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_button.title
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_climate
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_climate.data
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_climate.data.ac_number
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_climate.data.channel
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_climate.data.device_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_climate.data.name
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_climate.data.subnet_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_climate.data.subtype
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_climate.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_climate.title
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_cover
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_cover.data
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_cover.data.channel
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_cover.data.device_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_cover.data.name
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_cover.data.subnet_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_cover.data.subtype
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_cover.data.travel_time
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_cover.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_cover.title
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_device
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_device.data
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_device.data.device_type
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_device.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_device.title
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_light
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_light.data
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_light.data.channel
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_light.data.device_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_light.data.name
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_light.data.subnet_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_light.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_light.title
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_scene
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_scene.data
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_scene.data.area_number
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_scene.data.device_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_scene.data.name
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_scene.data.scene_number
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_scene.data.subnet_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_scene.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_scene.title
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_sensor
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_sensor.data
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_sensor.data.device_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_sensor.data.name
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_sensor.data.subnet_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_sensor.data.subtype
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_sensor.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_sensor.title
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_switch
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_switch.data
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_switch.data.channel
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_switch.data.device_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_switch.data.name
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_switch.data.subnet_id
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_switch.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.add_switch.title
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.init
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.init.data
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.init.data.action
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.init.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.init.title
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.remove_device
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.remove_device.data
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.remove_device.data.device
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.remove_device.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.remove_device.title
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus.data
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus.data.scan_duration
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus.title
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus_devices
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus_devices.data
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus_devices.data.devices
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus_devices.data.sensor_subtypes
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus_devices.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus_devices.title
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus_no_devices
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus_no_devices.description
FAIL: C: custom_components/buspro/translations/no.json missing path: options.step.scan_bus_no_devices.title
FAIL: C: custom_components/buspro/translations/no.json missing path: services
FAIL: C: custom_components/buspro/translations/no.json missing path: services.activate_scene
FAIL: C: custom_components/buspro/translations/no.json missing path: services.activate_scene.description
FAIL: C: custom_components/buspro/translations/no.json missing path: services.activate_scene.fields
FAIL: C: custom_components/buspro/translations/no.json missing path: services.activate_scene.fields.address
FAIL: C: custom_components/buspro/translations/no.json missing path: services.activate_scene.fields.address.description
FAIL: C: custom_components/buspro/translations/no.json missing path: services.activate_scene.fields.address.name
FAIL: C: custom_components/buspro/translations/no.json missing path: services.activate_scene.fields.scene_address
FAIL: C: custom_components/buspro/translations/no.json missing path: services.activate_scene.fields.scene_address.description
FAIL: C: custom_components/buspro/translations/no.json missing path: services.activate_scene.fields.scene_address.name
FAIL: C: custom_components/buspro/translations/no.json missing path: services.activate_scene.name
FAIL: C: custom_components/buspro/translations/no.json missing path: services.send_message
FAIL: C: custom_components/buspro/translations/no.json missing path: services.send_message.description
FAIL: C: custom_components/buspro/translations/no.json missing path: services.send_message.fields
FAIL: C: custom_components/buspro/translations/no.json missing path: services.send_message.fields.address
FAIL: C: custom_components/buspro/translations/no.json missing path: services.send_message.fields.address.description
FAIL: C: custom_components/buspro/translations/no.json missing path: services.send_message.fields.address.name
FAIL: C: custom_components/buspro/translations/no.json missing path: services.send_message.fields.operate_code
FAIL: C: custom_components/buspro/translations/no.json missing path: services.send_message.fields.operate_code.description
FAIL: C: custom_components/buspro/translations/no.json missing path: services.send_message.fields.operate_code.name
FAIL: C: custom_components/buspro/translations/no.json missing path: services.send_message.fields.payload
FAIL: C: custom_components/buspro/translations/no.json missing path: services.send_message.fields.payload.description
FAIL: C: custom_components/buspro/translations/no.json missing path: services.send_message.fields.payload.name
FAIL: C: custom_components/buspro/translations/no.json missing path: services.send_message.name
FAIL: C: custom_components/buspro/translations/no.json missing path: services.set_universal_switch
FAIL: C: custom_components/buspro/translations/no.json missing path: services.set_universal_switch.description
FAIL: C: custom_components/buspro/translations/no.json missing path: services.set_universal_switch.fields
FAIL: C: custom_components/buspro/translations/no.json missing path: services.set_universal_switch.fields.address
FAIL: C: custom_components/buspro/translations/no.json missing path: services.set_universal_switch.fields.address.description
FAIL: C: custom_components/buspro/translations/no.json missing path: services.set_universal_switch.fields.address.name
FAIL: C: custom_components/buspro/translations/no.json missing path: services.set_universal_switch.fields.status
FAIL: C: custom_components/buspro/translations/no.json missing path: services.set_universal_switch.fields.status.description
FAIL: C: custom_components/buspro/translations/no.json missing path: services.set_universal_switch.fields.status.name
FAIL: C: custom_components/buspro/translations/no.json missing path: services.set_universal_switch.fields.switch_number
FAIL: C: custom_components/buspro/translations/no.json missing path: services.set_universal_switch.fields.switch_number.description
FAIL: C: custom_components/buspro/translations/no.json missing path: services.set_universal_switch.fields.switch_number.name
FAIL: C: custom_components/buspro/translations/no.json missing path: services.set_universal_switch.name
FAIL: C: custom_components/buspro/translations/no.json services path mismatch; missing=['services', 'services.activate_scene', 'services.activate_scene.description', 'services.activate_scene.fields', 'services.activate_scene.fields.address', 'services.activate_scene.fields.address.description', 'services.activate_scene.fields.address.name', 'services.activate_scene.fields.scene_address', 'services.activate_scene.fields.scene_address.description', 'services.activate_scene.fields.scene_address.name', 'services.activate_scene.name', 'services.send_message', 'services.send_message.description', 'services.send_message.fields', 'services.send_message.fields.address', 'services.send_message.fields.address.description', 'services.send_message.fields.address.name', 'services.send_message.fields.operate_code', 'services.send_message.fields.operate_code.description', 'services.send_message.fields.operate_code.name', 'services.send_message.fields.payload', 'services.send_message.fields.payload.description', 'services.send_message.fields.payload.name', 'services.send_message.name', 'services.set_universal_switch', 'services.set_universal_switch.description', 'services.set_universal_switch.fields', 'services.set_universal_switch.fields.address', 'services.set_universal_switch.fields.address.description', 'services.set_universal_switch.fields.address.name', 'services.set_universal_switch.fields.status', 'services.set_universal_switch.fields.status.description', 'services.set_universal_switch.fields.status.name', 'services.set_universal_switch.fields.switch_number', 'services.set_universal_switch.fields.switch_number.description', 'services.set_universal_switch.fields.switch_number.name', 'services.set_universal_switch.name'] extra=[]
== D. config.abort vs async_abort reasons ==
  OK   config.abort covers ['gateway_unavailable', 'no_devices_found', 'scan_failed']
== E. config.step covers async_show_form/progress_done steps ==
  OK   config.step covers ['add_binary_sensor', 'add_button', 'add_climate', 'add_cover', 'add_device', 'add_light', 'add_scene', 'add_sensor', 'add_switch', 'init', 'manual', 'remove_device', 'scan_bus', 'scan_bus_devices', 'user']

RESULT: FAIL (139 failures)
exit=1
```

### 5.2 修复后 PASS（真实 stdout，exit=0）

> 完整日志见 `_work/i18n_pass_after.log`。

```
== A. json.load all 4 files ==
  OK   custom_components/buspro/strings.json
  OK   custom_components/buspro/translations/zh-Hans.json
  OK   custom_components/buspro/translations/en.json
  OK   custom_components/buspro/translations/no.json
== B. top-level key set parity ==
  OK   custom_components/buspro/translations/zh-Hans.json top keys == ['config', 'options', 'services']
  OK   custom_components/buspro/translations/en.json top keys == ['config', 'options', 'services']
  OK   custom_components/buspro/translations/no.json top keys == ['config', 'options', 'services']
== C. recursive path parity ==
  OK   custom_components/buspro/translations/zh-Hans.json has every strings.json path
  OK   custom_components/buspro/translations/zh-Hans.json services paths == strings.json
  OK   custom_components/buspro/translations/en.json has every strings.json path
  OK   custom_components/buspro/translations/en.json services paths == strings.json
  OK   custom_components/buspro/translations/no.json has every strings.json path
  OK   custom_components/buspro/translations/no.json services paths == strings.json
== D. config.abort vs async_abort reasons ==
  OK   config.abort covers ['gateway_unavailable', 'no_devices_found', 'scan_failed']
== E. config.step covers async_show_form/progress_done steps ==
  OK   config.step covers ['add_binary_sensor', 'add_button', 'add_climate', 'add_cover', 'add_device', 'add_light', 'add_scene', 'add_sensor', 'add_switch', 'init', 'manual', 'remove_device', 'scan_bus', 'scan_bus_devices', 'user']

RESULT: PASS (all parity assertions satisfied)
exit=0
```

## 6. 全量回归

```bash
cd /vol1/1000/资料/编程项目/河东HA集成
for f in custom_components/buspro/**/*.py custom_components/buspro/*.py; do
  python3 -m py_compile "$f" || echo "FAIL $f"
done
```

输出：无 `FAIL`（仅 `compile loop done`），`json.tool` 校验 `no.json` 通过。

## 7. 遗留问题（未顺手修改）

1. **`config_flow.py` 中存在中英混杂的硬编码文案**，例如
   `config_flow.py:292` 的 `"scan_bus": "扫描总线发现设备"` 与
   `config_flow.py:291/293/294` 的英文选项。这些是 schema 选项的显示文本，
   不受 `translations/*.json` 覆盖（selector 选项值通常应由 translations 提供），
   会导致任何语言下都出现中文/英文混排。不属于本次 i18n 键位缺口，按约束未改。
2. **`services.yaml` 已存在**，但上游是否在每个 translation 中保持
   `strings.json` 的 `services` 结构，本次仅通过脚本核对了 3 个语言；后续新增服务时
   应把 `_work/test_i18n_parity.py` 纳入 CI 防止再次出现「谎报已补齐」。
3. `config_flow.py` 的 abort reason 未包含 `already_configured` 字面量
   （由 `_abort_if_unique_id_configured()` 隐式产生），`strings.json` 已提供该 key；
   脚本 D 只校验显式 `async_abort(reason=...)`，如后续改为显式传参需同步。
