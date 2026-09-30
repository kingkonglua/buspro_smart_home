# 河东 buspro ↔ marsh4200/ar_hdl_buspro v5.0.7 整合报告

- 日期：2026-09-30
- 合并分支：`feature/merge-v5.0.7`（基线 `wip/2026-08-27-work` @ `59b70e9`）
- 上游参考：`_work/upstream/custom_components/ar_hdl_buspro/`（v5.0.7）
- 本地集成：`custom_components/buspro/`（domain 保持 `buspro`）
- 净变更：23 文件，+1475 / −259

---

## 0. 重要结论（先读）

1. **本地 pybuspro 早已是三层结构**（`core/` + `transport/` + `devices/`），并非任务书假设的扁平包。
   阶段 1 因此是对齐到 v5.0.7，而非新建目录。
2. 合并采取**保守策略**：只移植自包含、低风险的底层与新增能力，
   **不改动**既有 light / switch / climate(floor_heating/ac) / sensor / binary_sensor / cover / button 的行为。
3. 上游是**闭源专有**集成（`licensing.py` + const 头部版权声明），本地是 MIT。
   本次**未引入** `licensing.py`，也未整体照搬上游 HA 层；仅移植协议/传输实现。**许可合规性需自行确认（见风险 R1）**。

---

## 1. 逐阶段 commit

| 阶段 | commit | 说明 |
|------|--------|------|
| 0 | `59b70e9`（基线，wip 分支） | `.gitignore` 忽略 `_work/`；基线已推送 GitHub |
| 1 | `10fc431` | pybuspro 底层对齐 v5.0.7 |
| 2 | `69294e9` | panel_ac / scene / entity 基类 |
| 3 | `d5245b4` | config_flow + 三份翻译同步 |
| 4 | `0726d9e` | diagnostics / 死代码 / 任务清理 / manifest 3.0.0 / 测试 |
| 5 | 本文件 | 报告 |

> `wip/2026-08-27-work`（含 8/27 抢救成果）已推送 origin，未丢失。

---

## 2. 功能差异矩阵

### 2.1 底层 pybuspro（阶段 1）

| 文件 | 动作 | 内容 |
|------|------|------|
| `core/telegram.py` | 采用上游 | 类型注解/文档；`__eq__` 增加 `isinstance` 保护 |
| `helpers/telegram_helper.py` | 采用上游 | 出站头部 source-IP 改为可配置 `advertised_ip`（缺省回退 `192.168.1.15`，与旧版一致）；`operate_code` 为空保护；日志化 |
| `helpers/enums.py` | 合并 | **保留**本地 `CurtainAction/AcMode/AcFanSpeed/ReadDeviceInfo` + 10 个补充码；**新增**上游 `0xE441 / 0xE3E7-8 / 0xDB00-1 / 0xE3D8-DB / 0x1630` |
| `transport/udp_client.py` | 采用上游 | `SO_REUSEADDR/SO_REUSEPORT`、端口占用回退临时端口、异常安全、`connection_lost` 通知 |
| `transport/network_interface.py` | 采用上游（去授权） | 源 IP 允许列表过滤（`allowed_source_ips`），拒绝他网关遥测；**移除**上游 licensing 门槛，改为始终放行 |
| `buspro.py` | 采用上游（去授权） | 设备回调改为**仅按 `source_address` 匹配**；新增 `_notify_connection_lost` / `allowed_source_ips` / `dropped_source_ips` / `advertised_ip` / `on_connection_lost`；保留 `buspro.log` 日志名 |
| `devices/device.py` | 局部增强 | 回调异常保护、`network_interface` 空值保护、`unregister` 幂等 |
| `devices/sensor.py` | 增强 | 解析上游新增的 `0x1630`（温度/lux，字段整体前移一格；motion **刻意不解析**） |

**保留的自研资产（未退化）**：`devices/ac.py`（0x1938/0x193A）、`devices/curtain.py`（0xE3E0）、
`button.py`、`cover.py`、`discovery.py`、`gateway_discovery.py`、`zh-Hans.json`，以及 8/27 修复的全部 bug。

### 2.2 HA 平台（阶段 2）

| 能力 | 动作 | 说明 |
|------|------|------|
| 面板空调 `panel_ac` | **新增** | 移植上游 `pybuspro/devices/panel_ac.py`（0xE3D8-DB，一帧一字段）；HA 侧新增 climate 子类型 `ac_panel` + `BusproPanelACClimate` |
| 场景 `scene` | **新增** | 新增 HA scene 平台，把 HDL `(area, scene)` 暴露为 HA 场景实体；`const` 新增 `DEVICE_TYPE_SCENE / CONF_AREA_NUMBER / CONF_SCENE_NUMBER` |
| 实体基类 | **新增** | `entity.py`：`BusproEntityMixin` / `BusproEntity`（设备回调、available、name/unique_id 自动接线） |
| light/switch/climate(地暖,直连AC)/sensor/binary_sensor/cover/button | **未改** | 保持现有实现与行为；基类只供新平台使用，避免回归 |

### 2.3 配置 / 翻译（阶段 3）

| 项 | 动作 |
|----|------|
| `config_flow.py` | 新增 `add_scene` 步骤；`add_climate` 增加温度通道以支持 `ac_panel`；`DEVICE_TYPES` 增加 `scene`；**保留**本地 gateway 自动发现 / 总线扫描 / 手填兜底 |
| `strings.json` / `translations/en.json` / `translations/zh-Hans.json` | 三份键位**完全对齐**（脚本校验通过），补齐 `add_cover/add_button/add_scene/add_climate(subtype,ac_number,channel)`、`gateway_choice` |

### 2.4 质量（阶段 4）

| 项 | 动作 |
|----|------|
| `diagnostics.py` | **新增**：entry 信息 + 网关连接/源 IP 过滤状态，`host`/`advertised_ip` 脱敏 |
| 死代码 | 删除 `DIRECTED_PHASE_TYPICAL_SECONDS` 及从未引用的 `DEVICE_TYPE_BY_RESPONSE` / `DEVICE_TYPE_BY_RESPONSE_NAME`（分类统一在 `infer_device_type`） |
| 扫描任务 | 注册 `async_on_remove(self._cancel_scan_task)`，流程取消/重入时 cancel，防任务泄漏 |
| `manifest.json` | version `1.1.0 → 3.0.0`，文档/issue 链接指向实际仓库 |
| `tests/test_panel_ac.py` | 移植上游面板空调测试，本地**全部通过**（24/24 PASS） |

---

## 3. 明确未完成 / 有风险的部分（不粉饰）

### 未移植（上游有、本地无）
- **`licensing.py` 授权体系**（专有，刻意不引入）。本地无门槛、无试用/激活步骤。
- **上游 `config_flow.py` 全部步骤**（围绕授权与 selector 设备管理，domain 不同）。本地保留自有流程。
- **上游 HA 层大改版**（`climate.py` 586 行 AC_IR/AC_PANEL 协议、`sensor.py` 670 行、`entity.py` 依赖 gateway+授权）。
  因此本地仍**缺少**以下上游能力：
  - `AC_IR`：经 IR 发射模块（HDL-MIRC04.40）控制空调（本地是直连空调模块另一套协议）；
  - 湿度传感器 `SENSOR_KIND_HUMIDITY`、面板温度 `0xE3E7/0xE3E8` 的 HA 传感器接入（操作码已加，**HA 平台未启用**）；
  - `motion_byte_index` / `motion_uv_switch`（运动字节/紫外线开关）、`hw_kind`（dlp/12in1/8in1/panel/sensors_in_one）细分；
  - cover 的 `relay_pair` 模式与「重定位前重校准」；
  - `universal_switch` 作为独立 HA `switch` 设备类型（本地以 binary_sensor/button 表达）。
- 上游 `entity.py` 的 `via_device_id` 设备注册接线未采用（本地当前不建 device registry）。

### 风险点
- **R1 许可合规**：上游代码头部声明专有；本次移植了 `telegram.py`、`telegram_helper.py`、
  `udp_client.py`、`network_interface.py`、`panel_ac.py` 等实现。发布前请确认授权/署名（仓库已有 NOTICE.md，建议补充来源）。
- **R2 回调匹配语义变化**：`buspro.py` 由「source 或 target 匹配」改为「仅 source 匹配」。
  这是上游的修正（避免把发给设备的下行命令当作设备自身状态解码），但属行为变更，**建议实机回归** light/switch/climate。
- **R3 `advertised_ip` 未启用**：默认仍回退 `192.168.1.15`。上游通过它改善跨网段/经网关转发的广播接收；本地未实现本机 IP 探测。若发现「轮询读正常但收不到总线广播」，需补此逻辑。
- **R4 panel_ac 协议字段**（字段 3/4/5/6/7）来自上游抓包（Enviro 面板），本地**未实机验证**；swing/压缩机等未映射。
- **R5 scene 平台**为新增，未经实机验证；HDL 场景的 `(area, scene)` 目标设备语义需按现场确认。
- **R6 本地遗留 bug 未全修**（见 `BUG_REVIEW.md`）：#11 窗帘移动中位置不刷新、#6 `curtain_module` 关态不可靠 等仍存在，本次未纳入。
- **R7 测试覆盖有限**：仅面板空调有自动化测试；其余平台无测试，靠静态编译与人工对照。

### 非阻塞的协议存疑（已记录）
- `0x1630` 的 motion 字节位置在不同硬件上未统一（上游亦未定论），故本地不解析 motion，避免误触发。
- `0xE3E7/0xE3E8`（面板温度）字节布局来自上游单点抓包，需更多样本确认。

---

## 4. HA 实机部署步骤

1. **备份**现有集成：
   ```bash
   cp -r /config/custom_components/buspro /config/custom_components/buspro.bak.$(date +%F)
   ```
2. **替换**：把本仓库 `custom_components/buspro/` 覆盖到 `/config/custom_components/buspro/`。
3. **清缓存**：
   ```bash
   find /config/custom_components/buspro -name __pycache__ -type d -exec rm -rf {} +
   ```
4. **重启 Home Assistant**（manifest `version` 变更，建议整机重启而非仅重载集成）。
5. **配置沿用**：domain 仍为 `buspro`，已有配置项（host/port + devices）**无需重建**；
   旧实体 `unique_id` 未变，历史数据保留。
6. **新增功能入口**：
   - 面板空调：集成选项 → Add Device → Climate → subtype 选 `ac_panel`，填面板槽位号（ac_number）与温度通道（channel）。
   - 场景：集成选项 → Add Device → Scene，填目标设备地址与 `area_number / scene_number`。
7. **诊断**：设置 → 设备与服务 → 该集成 → 下载诊断。
8. **调试日志**（`configuration.yaml`）：
   ```yaml
   logger:
     logs:
       buspro.log: debug
       buspro.telegram: debug
   ```
9. **回滚**：
   ```bash
   cp -r /config/custom_components/buspro.bak.<日期>/* /config/custom_components/buspro/
   ```
   或从 Git：`git checkout 59b70e9 -- custom_components/buspro`。

---

## 5. 校验记录

- `python3 -m compileall custom_components/buspro tests` → 通过
- 全部 `*.json` `json.load` → 通过
- `python3 tests/test_panel_ac.py` → 24/24 PASS
- `strings.json` / `en.json` / `zh-Hans.json` 键位脚本校验 → 完全对齐
