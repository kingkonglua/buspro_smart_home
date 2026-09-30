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

## 2026-08-14 修复清单(v3.0.0)

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

## 常见问题:收不到广播 / 面板、传感器状态不更新

主动点灯/开关能控制,但网关主动推送的**广播收不到**、状态一直不刷新,且日志没有明显报错——通常是 **UDP/6000 被占用**,集成静默退化到了随机临时端口(定向回包仍能收到,广播丢失)。

自查 6000 占用:

```bash
ss -lunp | grep 6000
# 或 netstat -lunp | grep 6000
```

若占用者是 HDL 官方调试软件/另一个 HA 实例等非本集成进程,关闭它后**重载 HDL Buspro 集成**即可。退化时 HA 日志会出现一条 WARNING(`Could not bind UDP port 6000 ...`,每次退化只记一次),诊断(设置 → 设备与服务 → HDL Buspro → ⋮ → 下载诊断)里的 `bound_to_default_port` 会变为 `false` 并附带 `degraded_port`。

## 支持设备

- 灯(light)
- 开关(switch)
- 传感器/二进制传感器(sensor/binary_sensor)
- 空调 + 地暖(climate)
- 窗帘/卷帘(cover)
- 按钮/场景(button)
- 通用开关(universal switch)