# R7 + R5 修复报告

commit message：`fix(R7+R5): 诊断脱敏内网 IP 列表 + unique_id 加网关前缀`

## 1. 结论

- **R7 已修**：`diagnostics.async_get_config_entry_diagnostics` 返回结构不再包含任何明文内网
  IPv4（`allowed_source_ips` / `dropped_source_ips` 列表、`advertised_ip`、peer 地址，以及
  `entry.unique_id` 里的 `host:port`）。列表保持“序列”形状，元素逐项脱敏为 `**REDACTED**`。
- **R5 已修**：所有平台实体的 `unique_id` 加上了所属网关的 `config_entry.entry_id` 前缀
  （经 `BusproModule.entry_id` 获得），跨网关同址实体不再互相去重；同一 entry 内幂等。

---

## 2. 改动清单

| 文件 | 行数 | 内容 |
|---|---|---|
| `custom_components/buspro/diagnostics.py` | +40 / -1 | 新增 `_redact_ips()` 递归 IPv4 脱敏；新增 `GATEWAY_SEQUENCE_REDACT`；`_gateway_info` 对源 IP 列表逐项脱敏；最终返回值再过一遍全路径脱敏 |
| `custom_components/buspro/const.py` | +21 | 新增 `gateway_scoped_unique_id(module, local_id)` 辅助函数 |
| `light.py` / `switch.py` / `cover.py` / `climate.py`（2 处） | 各 +5~7 | `unique_id` 用网关前缀包裹 `device_identifier` |
| `sensor.py` | +8 | `unique_id` 用网关前缀包裹 `"{identifier}-{sensor_type}"` |
| `binary_sensor.py` | +5 | 同上（含 channel 后缀） |
| `button.py` | +3 | `_attr_unique_id` 用网关前缀包裹传入的 `device_key` |
| `scene.py` | +5 | `_attr_unique_id` 用网关前缀包裹 `buspro_{unique_key}` |
| `entity.py` | +4 | `BusproEntityMixin` 里 `_attr_unique_id` 用网关前缀包裹（覆盖 `BusproEntity` / `BusproPanelACClimate`） |
| `_work/test_r7_r5_verify.py` | +394（新增） | 硬性验收测试 |

### 为什么选 `config_entry.entry_id` 而不是 `host`

- `entry_id` 是 HA 生成的 UUID，**全局唯一且持久**（重启/重载不变）；`host` 两台网关可能是
  同一 IP 的不同端口，或被替换的新网关，前缀会撞。
- `entry_id` 不泄漏任何网络拓扑信息到 unique_id 字符串（虽然 unique_id 一般不展示）。
- 参数 `module.entry_id` 由 `__init__.py:async_setup_entry` 写入，等于 `config_entry.entry_id`。

---

## 3. R7：列表/set 的处理（贴测试证明）

`async_redact_data` 只按 **dict 的 key** 整体替换值；对 list/set 的**成员**不会脱敏，
而把 `allowed_source_ips` 直接加进 `GATEWAY_REDACT` 会把整个列表压成一个标量字符串，
丢掉“序列”形状。因此采用逐项脱敏（`_redact_ips`），并在最终返回值上再做一次递归全路径兜底：

```python
GATEWAY_SEQUENCE_REDACT = ("allowed_source_ips", "dropped_source_ips")
...
for key in GATEWAY_SEQUENCE_REDACT:
    if key in gateway:
        gateway[key] = _redact_ips(gateway[key])
return async_redact_data(gateway, GATEWAY_REDACT)
...
return _redact_ips(diag)   # 覆盖 entry.unique_id 等所有路径
```

测试对 `gateways` 与单数 `gateway` **两条路径**都取到了键（共 3 处），并断言整个返回 dict 无
明文 IP；`dropped_source_ips_is_sequence` 断言脱敏后仍是 list。以下为真实输出。

### 修复前

```
$ python3 _work/test_r7_r5_verify.py
== R7: diagnostics must not leak internal IPs ==
FAIL: diagnostics_redacts_allowed_source_ips  allowed=[['10.0.0.7', '192.168.1.15'], ['10.0.0.7', '192.168.1.15'], ['10.0.0.7', '192.168.1.15']]
FAIL: diagnostics_redacts_dropped_source_ips  dropped=[['192.168.1.99'], ['192.168.1.99'], ['192.168.1.99']]
PASS: diagnostics_redacts_dropped_source_ips_is_sequence
== R5: unique_id is gateway-scoped ==
FAIL: unique_id_differs_across_gateways  A=['(1, 10)-1', ...] B=['(1, 10)-1', ...]
PASS: unique_id_stable_within_one_gateway

RESULT: 3 FAILURE(S): diagnostics_redacts_allowed_source_ips, diagnostics_redacts_dropped_source_ips, unique_id_differs_across_gateways
EXIT=1
```

### 修复后

```
$ python3 _work/test_r7_r5_verify.py
== R7: diagnostics must not leak internal IPs ==
PASS: diagnostics_redacts_allowed_source_ips
PASS: diagnostics_redacts_dropped_source_ips
PASS: diagnostics_redacts_dropped_source_ips_is_sequence
== R5: unique_id is gateway-scoped ==
PASS: unique_id_differs_across_gateways
PASS: unique_id_stable_within_one_gateway

RESULT: ALL_PASS
EXIT=0
```

---

## 4. R5：向后兼容代价（重要）

**代价**：已有用户的实体 `unique_id` 全部从 `1.10.1` 变成了 `<entry_id>-1.10.1`。
HA 实体注册表以 unique_id 为主键，升级后旧记录**不会被自动替换**，会残留为
“已注册但无法匹配”的实体——通常表现为**原实体变成 unavailable/僵尸实体**，同时新生成了
一套带网关前缀的实体（名称/区域需要重新配置）。

**本轮不做迁移**（按要求）。后续建议（不在本轮范围）：

1. 在集成里加 `async_migrate_entry`，或一次性 registry 迁移：读取旧 unique_id 记录，
   重命名为 `f"{entry_id}-{old}"`，保留 entity_id / 名称 / 区域；
2. 或发版说明，让用户删除旧集成条目后重新添加（代价最小但体验差）。

因为前缀是 `entry_id`（持久），迁移映射是确定的：`new = entry_id + "-" + old`。

---

## 5. 自检真实输出

主工作区（包含并发 agent 改动）与 **仅含本次改动的 staged 树** 上均验证通过：

```
$ python3 -m compileall -q custom_components/buspro && echo COMPILE_OK
COMPILE_OK

$ python3 _work/test_r7_r5_verify.py
RESULT: ALL_PASS

$ python3 _work/verify_ha_stub.py
TOTAL=39  PASS=39  STUB_ARTIFACT=0  OWN_ERROR=0

$ python3 _work/deploy_rehearsal.py
RESULT: OK

$ python3 _work/verify_step_translations.py
EXIT 0

$ for t in test_e2e test_fix_bugs test_regression_deep test_payload_none test_travel_time_types; do ...; done
test_e2e OK
test_fix_bugs OK
test_regression_deep OK
test_payload_none OK
test_travel_time_types OK

$ python3 _work/verify_final_regression.py
13/13 checks passed
FINAL_OK
```

额外回归（防 R5 影响多网关/平台）：

```
test_multi_gateway OK
test_m8_multigateway_lifecycle OK
test_ac_curtain OK
test_cover_drift OK
test_cover_state_machine OK
test_cover_stop_flag OK
test_hooks_wired OK
```

---

## 6. 剩余风险 / 非阻塞疑问

1. **同网关内 light 与 switch 配成同一 (subnet, device, channel) 仍会撞**
   （例如都是 `1.10.1`）：本轮按任务只加“网关前缀”，未把设备类型/平台纳入 local_id。
   建议后续把 local_id 改为 `f"{device_type}-{device_identifier}"`，但那会再次改变
   unique_id、放大第 4 节的迁移代价。记为 P3 待办。
2. **并发工作区**：本仓库同时有其它 agent（entity.py/climate.py 的 `BusproEntityMixin`
   重构、pybuspro `dropped_source_ips` 队列化等）在改同一批文件。本次提交**只 stage 了
   R7/R5 的改动**（通过从 HEAD 重建 + `git update-index` 精确落盘），未包含
   `__init__.py` / `pybuspro/` / 并发重构，避免踩到 R2 专线。工作区里其它 agent 的
   unstaged 改动保持原样。
3. `_redact_ips` 会脱敏任何形如 dotted-quad 的字符串，包括 `entry.unique_id` 的
   `host` 部分。这是有意的（属 R7“所有路径”）；不影响诊断可读性（端口/计数仍在）。
4. 测试文件与报告位于 `_work/`（`.gitignore`），用 `git add -f` 强制入库。
