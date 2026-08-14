# HDL Buspro (河东) Home Assistant 集成

基于 [eyesoft/home_assistant_buspro](https://github.com/eyesoft/home_assistant_buspro) 与 [fengxs2018/hdl-buspro](https://github.com/fengxs2018/hdl-buspro) 改造的增强版。

本地 UDP 网关通信,无云依赖。

## 主要增强(相对上游)

### 🌬️ 空调(新增,操作码 0x1938/0x193A)
- `pybuspro/devices/ac.py` — 中央空调控制器模块
- `climate.py` 扩展为 地暖 + 空调 双类型
- 支持:HVAC 模式(制冷/制热/送风/自动/除湿)、风速、扫风、温度
- 安全特性:状态未知时跳过控制命令,避免误关机

### 🪟 窗帘(新增,操作码 0xE3E0)
- `pybuspro/devices/curtain.py` — 窗帘开关模块
- `cover.py` — cover 平台,支持纯窗帘模块 + 带电机(行程时间估算位置)

### 🧩 其他
- `button.py` — 按钮/场景平台
- `config_flow.py` — 新增空调/窗帘/按钮的 UI 配置步骤

## 2026-08-14 修复清单(v2.0.1)

- 修复 cover 窗帘首次操作崩溃(None + 位置估算)
- 修复 binary_sensor 多传感器 unique_id 冲突
- 修复 config_flow 重复设备静默覆盖
- 修复空调状态未知时误发关机命令
- 修复 pybuspro payload 越界/None 比较崩溃
- 修复服务卸载后未注销残留
- 其他 5 项健壮性改进

## 安装

1. 将 `custom_components/buspro/` 复制到 HA 的 `custom_components/` 目录
2. 重启 Home Assistant
3. 配置 → 设备与服务 → 添加集成 → 搜索 "HDL Buspro"

## 支持设备

- 灯(light)
- 开关(switch)
- 传感器/二进制传感器(sensor/binary_sensor)
- 空调 + 地暖(climate)
- 窗帘/卷帘(cover)
- 按钮/场景(button)
- 通用开关(universal switch)