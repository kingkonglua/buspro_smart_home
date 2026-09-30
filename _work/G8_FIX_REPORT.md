# G8 修复报告 —— 启动读取风暴丢包 / 控制帧无 ACK 重发

- 分支：`feature/merge-v5.0.7`
- 复现测试：`_work/test_g8_startup_burst.py`（全部用假 buspro，不真发 HDL 总线包）
- 修复文件：
  - `custom_components/buspro/pybuspro/devices/device.py`（主体）
  - `custom_components/buspro/pybuspro/devices/light.py`（把控制帧接入 ACK watch，2 行）
  - `custom_components/buspro/pybuspro/devices/switch.py`（同上，2 行）
  - `_work/test_g8_startup_burst.py`（新增复现测试）
- 未改动（按要求）：`discovery.py`、`_work/test_bug8_service_schemas.py`、`_work/test_device_type_i18n.py`、任何现有测试断言。
  注：工作区里 `config_flow.py` / `discovery.py` 当时已被别的进程改动，**我没有 stage 它们**，只 stage 自己的文件（禁止 `git add -A`）。

---

## 1. 修复前 FAIL（真实输出，`rc=1`）

命令：

```sh
cd /vol1/1000/资料/编程项目/河东HA集成
/tmp/hdl_venv/bin/python _work/test_g8_startup_burst.py ; echo "rc=$?"
```

```
[A] dropped first 2 reads -> sends=1 got_status=False is_on=False brightness=0
FAIL G8-A dropped first packet: G8-A: dropped startup read was not retried (only 1 send(s); expected >= 3 after 2 lost packets)
[B] 20 startup reads -> spread=0.000s (first=7.235 last=7.235)
FAIL G8-B startup thundering herd: G8-B: startup reads fired as one thundering herd (spread=0.000s, expected > 0.2 s of jitter)
[C1] command sent, ACK dropped -> control frames 1 -> 1
FAIL G8-C control ACK resend: G8-C1: missing ACK did not trigger exactly one resend (got 0 resend(s); expected 1)

3 G8 check(s) failed
rc=1
```

三个场景对应三处「现场」：

- **A**：连丢 2 个 `_ReadStatusOfChannels` 后原实现只发 1 次就结束（`sends=1`，`is_on=False`）——实体永久卡在默认 off。
- **B**：20 个 device 的首次读取 `spread=0.000s`——全部挤在同一个 3s 时刻同帧齐发。
- **C**：丢 ACK 后控制帧没有任何重发（`1 -> 1`）——负载与 HA 状态脱节。

## 2. 修复后 PASS（真实输出，`rc=0`）

```
[A] dropped first 2 reads -> sends=3 got_status=True is_on=True brightness=100
PASS G8-A: lost startup reads retried (bounded) -> state recovered
[B] 20 startup reads -> spread=1.669s (first=6.890 last=8.559)
PASS G8-B: startup reads are jitter-staggered, not same-timeslice
[C1] command sent, ACK dropped -> control frames 1 -> 2
[C2] command sent, ACK received -> total control frames=1
PASS G8-C: control ACK resend is bounded and only on lost ACK

All G8 startup-burst checks passed
rc=0
```

（连续重复执行 3 次均 `rc=0`，场景 B 使用固定随机种子，不抖动。）

原 gap 探针 `_work/test_gaps_pybuspro.py` 复核：G8 已从「GAP CONFIRMED」变为
「NOT reproduced (local already supports)」。

---

## 3. 实现说明（参数 / 信号 / 幂等性）

### 3.1 重试次数、退避、抖动参数

`device.py` 模块级常量（测试可覆写以保证快速）：

| 常量 | 值 | 含义 |
| --- | --- | --- |
| `_CHANNEL_STATUS_INITIAL_DELAY_SECONDS` | `3.0` | 启动首读固定延迟（原行为） |
| `_CHANNEL_STATUS_INITIAL_JITTER_SECONDS` | `2.0` | 首读额外随机抖动 `uniform(0, 2)` |
| `_CHANNEL_STATUS_MAX_SENDS` | `4` | 有界发送上限 = 1 首发 + 最多 3 次重试 |
| `_CHANNEL_STATUS_RETRY_BASE_SECONDS` | `1.0` | 指数退避基数 |
| `_CHANNEL_STATUS_RETRY_MAX_SECONDS` | `4.0` | 退避封顶 |
| `_CHANNEL_STATUS_RETRY_JITTER_SECONDS` | `0.5` | 每次重试间隔再叠加 `uniform(0, 0.5)` |

- 启动路径 `run_from_init=True`：先 `sleep(3 + uniform(0,2))` 错峰，然后最多发 4 次；
  第 `k` 次重试前 `sleep(min(1.0 * 2**k, 4.0) + uniform(0, 0.5))`，即约 1s / 2s / 4s 递增并封顶。
- **停止信号复用现有分发路径**：`Light/Switch._telegram_received_cb` 收到有效读数后会调用
  `_call_device_updated()`；我在 `device.py:_call_device_updated()` 中置 `self._got_initial_status = True`。
  读循环在每次发送前后都检查该标志，一旦为真立即 return（收到首个有效读数即停）。没有新造第二套状态通道。
- 场景触发重读 `run_from_init=False`：保持原「单发尽力而为」语义，不重试、不延迟。
- **BUG-6 兜底保留**：`_call_read_current_status_of_channels()` 顶部的
  `try: asyncio.get_running_loop() except RuntimeError: ... return` 原样保留；无 loop 时直接 return，
  不会创建 task。`_call_device_updated()` 的同类兜底也保留（标志位在兜底之前置位，兜底只影响广播调度，不影响状态收集）。

### 3.2 控制帧 ACK 重发（有界）

- `device.py:Device._start_ack_watch(control)`：发送后启动一个 watch，
  `sleep(_ACK_TIMEOUT_SECONDS=0.8)` 后，若 `_awaiting_ack` 仍为真且没有更新的命令（`_ack_seq` 未变），
  就重发 **同一条** 控制帧；重发上限 `_ACK_MAX_RESENDS = 1`（最多补发 1 次，绝不无限重发）。
- `_call_device_updated()` 在收到任何被实际应用的读数/响应时把 `_awaiting_ack=False` 并取消 watch，
  因此**只有在 ACK 丢失时才重发**，已被确认的命令不会重复发送。
- 新命令会 `cancel()` 上一条 watch 并递增 `_ack_seq`，保证同一设备同一时刻只有一个待重发任务。
- `light.py:_set` / `switch.py:_set` 在 `await scc.send()` 之后调用 `self._start_ack_watch(scc)`，
  把真正发货控制帧的路径接入该机制（上游 v5.0.7 也正是这样接的）。

### 3.3 控制帧重发的幂等性论证（为什么不会让负载重复动作）

HDL 的 `SingleChannelControl`（0x0031）携带的是**绝对目标亮度/开关电平**（`channel_number`,
`channel_level`），不是「翻转/切换」语义。因此把「同一帧再发一次」等价于「把该通道再次设置到同一个绝对值」：

- 若第一次其实已生效（只是响应包丢了），第二次把通道设成**同一电平**——继电器保持、调光保持，无可见变化；
- 若第一次真的丢了，第二次正好补上，负载达到目标电平；
- 它不会像 toggle 那样在「开↔关」之间来回跳，也不会对同一动作叠加两次（不是累加/递增指令）。

同理 `SceneControl` 是「激活第 N 号场景」的绝对目标（重复激活同一场景等价），
`UniversalSwitchControl` 携带绝对 ON/OFF。再加上 watch 是**有界**（最多 1 次）且**ack-aware**（收到响应即取消），
所以既有丢包自愈能力，又不会造成重复动作或风暴。

---

## 4. 回归测试真实计数（全部 rc=0，未弄挂任何一项）

| 测试 | 结果 | rc |
| --- | --- | --- |
| `_work/test_regression_deep.py` | `Ran 43 tests ... OK` | 0 |
| `_work/test_soak.py` | `FINAL VERDICT: No unbounded growth detected.`（模拟 24h） | 0 |
| `_work/test_reconnect_resync.py` | `All reconnect/resync checks passed`（BUG-1 + BUG-2 ×2） | 0 |
| `_work/test_cover_state_machine.py` | `Ran 9 tests ... OK` | 0 |
| `_work/test_ac_curtain.py` | `Ran 24 tests ... OK` | 0 |
| `_work/test_e2e.py` | `Ran 12 tests ... OK` | 0 |
| `_work/test_g8_startup_burst.py` | `All G8 startup-burst checks passed` | 0 |

（任务书里 `test_regression_deep.py` 列了两次，实际只跑一次；另附
`test_gaps_pybuspro.py`、`test_bug8_service_schemas.py`（FAIL=0）、`test_device_type_i18n.py`（PASS=25 FAIL=0）
均通过，以确认没有波及 VERIFY-8 / HA10 那条线。）

---

## 5. 残留 / 说明

- `sensor.py` 的 `_call_read_current_status_of_sensor()` 仍是固定 `sleep(5)` 单发，未纳入本次 device.py 改动范围。
  任务书第 2 步明确要求「改 `device.py`」，且复现测试 A/B 针对的是 `_ReadStatusOfChannels`（light/switch）。
  传感器读路径若要同样加固，需要单独改 `sensor.py`（本任务 staging 命令未包含它），此处记录为已知残留、不在本次提交内。
- 为让第 2 步第 3 项「控制帧 ACK 重发」真正生效（而不是死代码），必须把 `light.py` / `switch.py` 的
  `_set` 接到 `_start_ack_watch`。这两个文件属于「你改的文件」，已随本提交一并 stage。
- `_work/` 在 `.gitignore` 中，但历史报告/测试一直是 `git add -f` 入库的；本报告的测试文件与
  `G8_FIX_REPORT.md` 同样用 `-f` 入库，以符合任务书「提交 `_work/test_g8_startup_burst.py`」的要求。
