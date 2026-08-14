# 来源与改动声明

本集成 **buspro (HDL Buspro 河东)** 基于以下 GitHub 开源项目修改而来,在此向原作者致谢:

| 上游项目 | 仓库地址 | 说明 |
|---|---|---|
| eyesoft/home_assistant_buspro | https://github.com/eyesoft/home_assistant_buspro | 原始版本(MIT License),YAML 配置 |
| fengxs2018/hdl-buspro | https://github.com/fengxs2018/hdl-buspro | UI 配置版 fork |

## 修改内容(相对上游)

- **新增**:`pybuspro/devices/ac.py` — 中央空调控制(操作码 0x1938/0x193A)
- **新增**:`pybuspro/devices/curtain.py` — 窗帘开关模块(操作码 0xE3E0)
- **新增**:`cover.py` — cover 平台(纯窗帘 + 带电机位置估算)
- **新增**:`button.py` — 按钮/场景平台
- **扩展**:`climate.py` — 地暖扩展为 地暖 + 空调 双类型
- **扩展**:`config_flow.py` — 新增空调/窗帘/按钮 UI 配置步骤
- **修复**:cover 首次操作崩溃、binary_sensor unique_id 冲突、config_flow 覆盖、空调误关机、payload 越界、服务卸载残留等 11 项

## 许可

- 上游 eyesoft/home_assistant_buspro 采用 **MIT License**(见 LICENSE 文件)
- 本仓库的修改部分亦以 MIT License 发布
- 修改与增强:kingkonglua / trim.openclaw,2026-08-14 v2.0.1