# VERIFY-8 报告：BUG-8 服务 schema 验收测试首次真正跑通

**结论（先说）：`7c2cc4d` 对 BUG-8 的 product 修复是有效且完整的 —— BUG-8 于本次（TASK V1）首次得到真实验证。
本次未改动任何 product 代码，仅修复测试脚手架（sys.path 引导 + `ha_stub` 的 `positive_int` 保真度）。
全量 26 个测试脚本：`PASS=24 FAIL=2`，且那 2 个 FAIL 是 `test_gaps_ha.py` / `test_gaps_pybuspro.py`
这两个「发现 gap 就 exit 2」的审计脚本，按设计如此，不是断言失败。**

---

## 0. 本次改动清单（不碰 product / 不碰断言）

| 文件 | 改了什么 | 性质 |
| --- | --- | --- |
| `_work/test_bug8_service_schemas.py` | import 区前置 `ha_stub`/`pylibs`/ROOT；docstring 第 4 行改成真实运行方式 | 测试引导 |
| `_work/test_device_type_i18n.py` | `import_options_flow()` 在 import config_flow 前补 `ha_stub`/`pylibs` | 测试引导 |
| `_work/ha_stub/homeassistant/helpers/config_validation.py` | `positive_int` 忠实模拟 HA（`vol.All(vol.Coerce(int), vol.Range(min=0))`），非 int 输入抛 `vol.Invalid` 而非裸 `TypeError` | 测试脚手架保真度 |

`custom_components/buspro/**`（含 `__init__.py`、`services.yaml`、`pybuspro/**`）**一个字节都没动**。

> 说明：第 1 步按任务书只改两个测试文件的 import 区。改完真跑后 `test_bug8` 仍 rc≠0，
> 但根因查明是 `_work/ha_stub` 的 `positive_int` 保真度不足（见 §4），不是 product bug，
> 也不是断言失败。为让验收测试自洽，遂修脚手架本身 —— 这是任务书「让测试自洽」的既定目标。

---

## 1. 修复前：两条命令的原始输出（真实跑的）

### 1.1 `_work/test_bug8_service_schemas.py`

命令：
```sh
/tmp/hdl_venv/bin/python _work/test_bug8_service_schemas.py ; echo "rc=$?"
```
输出：
```
Traceback (most recent call last):
  File "/vol1/1000/资料/编程项目/河东HA集成/_work/test_bug8_service_schemas.py", line 9, in <module>
    import homeassistant.helpers.config_validation as cv
ModuleNotFoundError: No module named 'homeassistant'
rc=1
```

### 1.2 `_work/test_device_type_i18n.py`

命令：
```sh
/tmp/hdl_venv/bin/python _work/test_device_type_i18n.py ; echo "rc=$?"
```
输出（摘要，7 条 FAIL 全部同一个 `ModuleNotFoundError`）：
```
...
== RED-3: _get_device_display_name does not leak internal type keys ==
FAIL: RED-3 switch display name renders (en) -- ModuleNotFoundError: No module named 'homeassistant'
FAIL: RED-3 switch display name renders (zh-Hans) -- ModuleNotFoundError: No module named 'homeassistant'
FAIL: RED-3 light display name renders (en) -- ModuleNotFoundError: No module named 'homeassistant'
FAIL: RED-3 light display name renders (zh-Hans) -- ModuleNotFoundError: No module named 'homeassistant'
FAIL: RED-3 binary_sensor display name renders (en) -- ModuleNotFoundError: No module named 'homeassistant'
FAIL: RED-3 binary_sensor display name renders (zh-Hans) -- ModuleNotFoundError: No module named 'homeassistant'
...
== RED-5: en vs zh-Hans render different localized type names ==
  en device_type.light='Dimmer channel'  zh-Hans device_type.light='调光器通道'
PASS: RED-5 en/zh-Hans device_type.light differ
FAIL: RED-5 display name renders in both languages -- ModuleNotFoundError: No module named 'homeassistant'

TOTAL PASS=16 FAIL=7
FAILED CHECKS: ['RED-3 switch display name renders (en)', 'RED-3 switch display name renders (zh-Hans)', 'RED-3 light display name renders (en)', 'RED-3 light display name renders (zh-Hans)', 'RED-3 binary_sensor display name renders (en)', 'RED-3 binary_sensor display name renders (zh-Hans)', 'RED-5 display name renders in both languages']
rc=1
```

---

## 2. 修复后：两条命令的真实输出

### 2.1 `_work/test_bug8_service_schemas.py`

命令：
```sh
/tmp/hdl_venv/bin/python _work/test_bug8_service_schemas.py ; echo "rc=$?"
```
输出：
```
PASS  compat address list [1]
PASS  compat address list [1,74]
PASS  compat address list [1,74,0]
PASS  compat payload [1,75,0,3] preserved
PASS  dict numeric -> [1,100]
PASS  empty dict -> []
PASS  dict named -> [1,42]
PASS  dict numeric unordered -> [3,1]
PASS  dict named reordered -> [1,42]
PASS  string list parsed
PASS  scalar [1] unwrapped
PASS  scalar int preserved
PASS  rejects address 5
        (rejected as expected: expected a list, got int for dictionary value @ data['address'])
PASS  rejects address {'a':1}
        (rejected as expected: unexpected key 'a' in address mapping for dictionary value @ data['address'])
PASS  rejects scalar {'a':1}
        (rejected as expected: expected a positive integer for dictionary value @ data['switch_number'])
PASS  rejects scalar [1,2]
        (rejected as expected: expected a positive integer for dictionary value @ data['switch_number'])
PASS  inner accepts [1]
PASS  inner accepts [1, 74]
PASS  inner accepts [1, 74, 0]
PASS  inner rejects 5
        (rejected as expected: expected a list)
PASS  inner rejects {'a': 1}
        (rejected as expected: expected a list)
PASS  inner rejects '[1]'
        (rejected as expected: expected a list)

TOTAL FAIL=0  (none)
rc=0
```

### 2.2 `_work/test_device_type_i18n.py`

命令：
```sh
/tmp/hdl_venv/bin/python _work/test_device_type_i18n.py ; echo "rc=$?"
```
输出：
```
== RED-1: discovery.py has no CJK in code (comments exempt) ==
PASS: RED-1 discovery.py no CJK code/literals (found 0)
== RED-2: config.device_type.* parity + non-empty in 4 JSON files ==
PASS: RED-2 strings config.device_type key set == en
PASS: RED-2 zh-Hans config.device_type key set == en
PASS: RED-2 en config.device_type key set == en
PASS: RED-2 no config.device_type key set == en
PASS: RED-2 required key config.device_type.switch non-empty in all 4
PASS: RED-2 required key config.device_type.light non-empty in all 4
PASS: RED-2 required key config.device_type.sensor non-empty in all 4
PASS: RED-2 required key config.device_type.climate non-empty in all 4
PASS: RED-2 required key config.device_type.ac non-empty in all 4
PASS: RED-2 required key config.device_type.curtain non-empty in all 4
PASS: RED-2 required key config.device_type.binary_sensor non-empty in all 4
PASS: RED-2 required key config.device_type.unknown non-empty in all 4
== RED-3: _get_device_display_name does not leak internal type keys ==
PASS: RED-3 switch/en no '(switch' leak
PASS: RED-3 switch/zh-Hans no '(switch' leak
PASS: RED-3 light/en no '(light' leak
PASS: RED-3 light/zh-Hans no '(light' leak
PASS: RED-3 binary_sensor/en no '(binary_sensor' leak
PASS: RED-3 binary_sensor/zh-Hans no '(binary_sensor' leak
== RED-4: no hardcoded empty-list / remove literals in config_flow.py ==
PASS: RED-4 config_flow.py has no "  (none)" literal
PASS: RED-4 config_flow.py has no "No devices to remove." literal
== RED-5: en vs zh-Hans render different localized type names ==
  en device_type.light='Dimmer channel'  zh-Hans device_type.light='调光器通道'
PASS: RED-5 en/zh-Hans device_type.light differ
  en render: 'Current devices:\n  - Living room lamp (Dimmer channel, 1.2.3)\n\nSelect an action:'
  zh render: '当前设备：\n  - Living room lamp (调光器通道, 1.2.3)\n\n请选择操作：'
PASS: RED-5 en device string carries the English type name
PASS: RED-5 zh-Hans device string carries the Chinese type name
PASS: RED-5 same device renders differently per language

TOTAL PASS=25 FAIL=0
rc=0
```

---

## 3. 全量 26 个测试脚本汇总（真实计数）

命令：
```sh
cd /vol1/1000/资料/编程项目/河东HA集成
pass=0; fail=0
for t in _work/test_*.py tests/test_*.py; do
  out=$(/tmp/hdl_venv/bin/python "$t" 2>&1); rc=$?
  if [ $rc -eq 0 ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL($rc) $t"; fi
done
echo "PASS=$pass FAIL=$fail"
```
输出：
```
FAIL(2) _work/test_gaps_ha.py
FAIL(2) _work/test_gaps_pybuspro.py
PASS=24 FAIL=2
```

与任务书基线一致：
- 修复前（系统 python3）：`PASS=22 FAIL=4`（4 = 2 个 import 炸的测试 + 2 个审计脚本）
- 修复后（venv）：`PASS=24 FAIL=2`，仅剩两个审计脚本。

两个「FAIL」都是「发现 gap 就 exit 2」的设计行为，例如：
```
21/23 gap checks confirmed
rc=2
```
```
5/15 gaps reproduced
rc=2
```
它们不是断言测试，未做任何改动。

---

## 4. 为什么 `test_bug8` 在补完 sys.path 后还要再改一处脚手架

补完 `sys.path` 后，`test_bug8` 能 import、能跑，但 `rc=1`，报：
```
TypeError: int() argument must be a string, a bytes-like object or a real number, not 'dict'
```
根因：`_work/ha_stub/homeassistant/helpers/config_validation.py` 里的 `positive_int` 是
`int(value)` 裸实现，遇到非数字输入直接冒泡 `TypeError`；而真实 HA 是
`positive_int = vol.All(vol.Coerce(int), vol.Range(min=0))`
（见 `/tmp/opencode/ha_extract/homeassistant/helpers/config_validation.py:187`），
会把不可转换的输入转成 `vol.Invalid`。

测试的 `expect_invalid` 只捕获 `vol.Invalid`，所以旧 stub 让「应当被拒绝的非法输入」变成崩溃。

**独立验证 product 是否真的没问题**：绕开 stub、直接用真实 HA venv 跑同一份测试（临时诊断，不改文件）：
```sh
/tmp/opencode/haenv/bin/python - <<'PY'
import sys
ROOT = "/vol1/1000/资料/编程项目/河东HA集成"
sys.path.insert(0, ROOT)
path = ROOT + "/_work/test_bug8_service_schemas.py"
src = open(path, encoding="utf-8").read().replace(
    'sys.path.insert(0, os.path.join(HERE, "ha_stub"))', 'pass')
exec(compile(src, path, "exec"), {"__file__": path, "__name__": "__main__"})
PY
```
输出（真实 HA + voluptuous 0.15.2）：
```
...
PASS  rejects scalar {'a':1}
        (rejected as expected: expected int for dictionary value @ data['switch_number'])
PASS  rejects scalar [1,2]
        (rejected as expected: expected int for dictionary value @ data['switch_number'])
...
TOTAL FAIL=0  (none)
rc=0
```
→ 证明 **product 的 BUG-8 修复本身是对的**，失败纯粹来自 stub。

于是把 `positive_int` 改为忠实模拟（非 int → `vol.Invalid`）。改后 `test_bug8` rc=0，
且所有断言原样通过（见 §2.1）。**未改任何断言。**

---

## 5. 可复现运行命令（自洽，不依赖 `/tmp`）

两个测试已自洽：路径里带上 `_work/pylibs`（voluptuous 0.16.0）+ `_work/ha_stub`（HA 冒充），
所以系统 `python3` 直接可跑（无需 venv、无需 `/tmp`）：

```sh
cd /vol1/1000/资料/编程项目/河东HA集成
python3 _work/test_bug8_service_schemas.py ; echo "rc=$?"   # => rc=0
python3 _work/test_device_type_i18n.py     ; echo "rc=$?"   # => rc=0
```

`test_bug8_service_schemas.py` 的 docstring 已从失效的 `/tmp/opencode/haenv/bin/python`
改为上面的 `python3` 方式（并注明 venv 亦可）。

---

## 6. 最终判定

- **BUG-8 是「已修好且现已（首次）验证」**，不是「其实没修好」。
- 依据：同一份断言测试在真实 HA 环境 + 修复后的目标 venv 环境中均 `TOTAL FAIL=0`，且
  向后兼容用例（list 形式）与拒绝非法输入用例全部保留通过。
- product 代码零改动；无删除测试；无修改断言。
