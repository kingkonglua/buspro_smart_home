# 河东 HA 集成 — 网关发现 + 总线扫描 变更总结

## 新增功能

### 1. 网关自动发现（ConfigFlow）
- 添加集成时，先自动扫描局域网所有 HDL 网关
- UDP/6000 广播探测，3 秒窗口
- UI 上显示下拉框，用户选一个 IP
- 保留手动输入 IP 入口（fallback）

### 2. 总线设备扫描（OptionsFlow）
- 网关添加后，可在 Options 里选"扫描总线"
- 往总线发 8 种读请求，覆盖所有设备类型
- **自动识别 8 种设备类型**：
  - `switch`：继电器/调光器
  - `light`：带调光证据的通道
  - `curtain`：窗帘模块 ← 我们的
  - `ac`：空调 ← 我们的
  - `climate`：地暖/温控
  - `sensor`：传感器 / 多功能传感器
  - `binary_sensor`：干接点 / 万能开关
  - `keypad`：跳过（不导入）
- 扫描结果展示为复选框，勾选即可导入
- 导入时自动填充地址、通道数，用户只需改名

## 修改文件清单

| 文件 | 类型 | 改动 |
|------|------|------|
| `gateway_discovery.py` | **新增** (270行) | UDP 网关自动发现 |
| `discovery.py` | **新增** (520行) | BusScanner 总线扫描 + 设备分类 |
| `config_flow.py` | **改造** (875行) | 加 gateway/scan_bus 步骤，保留原所有流程 |
| `const.py` | **新增2项** | DEVICE_TYPE_AC, DEVICE_TYPE_CURTAIN |
| `pybuspro/helpers/enums.py` | **补全** (472行) | 新增 10 个操作码（DetectAddress/IsDeviceOnline/ReadAcStatusResponse 等） |
| `manifest.json` | **修改** | 添加 `"dependencies": ["network"]` |

## 设备分类逻辑

```
操作码返回 → 设备类型映射（discovery.py: infer_device_type）

0x1939 ReadAcStatusResponse         → ac（空调）
0xE3E3 ReadStatusOfCurtainSwitchResponse → curtain（窗帘）
0x0034 ReadStatusOfChannelsResponse  → light（有调光证据）/ switch
0x1646 ReadSensorStatusResponse      → sensor
0x1605 ReadSensorsInOneStatusResponse → sensor
0x1945 ReadFloorHeatingStatusResponse → climate（地暖）
0x15CF ReadDryContactStatusResponse  → binary_sensor
0xE019 ReadStatusOfUniversalSwitchResponse → binary_sensor
0x000F "read device info"           → unknown（保留，后续补充）

keypad/面板 → 跳过不导入
```

## 与 marsh4200 的差异

- marsh 只识别 5 种设备类型（light/switch/climate/sensor/binary_sensor）
- **我们的版本额外识别空调（ac）和窗帘（curtain）**，marsh 无法做到
- marsh 依赖 marsh 自己的 pybuspro 封装；我们用我们自己的传输层
- marsh 有 diagnostics.py / services.yaml，这次没做（可选，不影响核心功能）

## 安装到 HA

```bash
# 方法1：HACS 直接加自定义仓库（待发布到 GitHub）
# 方法2：手动复制 custom_components/buspro/ 到 HA 的 custom_components/

# 当前文件位置：
# /vol1/1000/资料/编程项目/河东HA集成/custom_components/buspro/
```

## 后续建议

1. **补 strings.json 和 translations** —— 新步骤还没有中文字符串（目前用英文 fallback）
2. **发布到 GitHub** —— 方便通过 HACS 安装
3. **diagnostics.py** —— 可选，方便排查问题
4. **测试总线扫描** —— 需要实际连接 HDL 网关验证