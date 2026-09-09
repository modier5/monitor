# Watchdog-Perf

Windows 长时进程性能监控与回溯工具。持续采集指定进程（或系统全局）的 CPU 与内存数据，并采用**逐行追加 + 立即 flush** 的方式写入按天滚动的 CSV，尽量降低蓝屏或强制杀进程时的数据丢失；之后可离线加载历史文件生成可缩放、可平移的交互 HTML 图表。

## 快速开始

依赖为 `psutil` 和 `matplotlib`；HTML 报告使用浏览器端的 ECharts（CDN 加载）：

```powershell
pip install -r requirements.txt
```

后台采集指定进程（窗口需保持开启；需要无人值守长期运行时可注册为计划任务或服务）：

```powershell
python -m watchdog_perf monitor --process chrome.exe --headless --interval 1
```

后台采集多个目标（支持通配符，进程重启后会自动重新绑定）：

```powershell
python -m watchdog_perf monitor --process "python*" --process chrome.exe --headless
```

按 PID 采集：

```powershell
python -m watchdog_perf monitor --pid 12345 --headless
```

按 exe 完整路径采集（进程之后才启动也会被自动发现；可多次指定）：

```powershell
python -m watchdog_perf monitor --exe "D:\Apps\FanxiangCloud\ai-manager.exe" --headless
```

不指定任何进程时默认监控系统全局 CPU 与已用内存。

监控目标是“根进程 + 整棵子进程树”。下面命令把 `chrome.exe` 主进程及其所有后代都算作同一组，CPU 条件按整机逻辑核心归一化后判断：

```powershell
python -m watchdog_perf monitor --process chrome.exe --headless
```

同时监控多个根进程/多棵进程树：

```powershell
python -m watchdog_perf monitor --process app.exe --process worker*.exe --pid 12345 --headless
```

每一条子进程数据都会独立记录，并在 `extra` 中写入 `root_pid`、`root_name`、`parent_pid`、`depth`，绘图时也会在图例中标注该进程属于哪棵根。

### 按“应用”配置：进程可能稍后才出现

长时任务里常见的模式是：工具启动时目标还没有出现，之后某个应用才拉起服务进程，
服务进程再稍后拉起 `llama*.exe` 之类的计算进程。`monitoring.apps` 就是为这种场景准备的应用级配置：

```json
{
  "monitoring": {
    "mode": "tree",
    "include_children": true,
    "apps": [
      {
        "name": "FanxiangCloud 示例",
        "roots": ["FanxiangCloud*.exe", "ai-manager.exe"]
      }
    ]
  }
}
```

这里的 `roots` 使用任务管理器中的**映像名称（exe 名）/通配符**，不是“描述”列；
`name` 只是通知与日志里的应用别名。规则如下：

如果只凭 exe 名仍然会误匹配同名的其他程序，或不想依赖“FanxiangCloud 根进程”的名字，
可直接在 `monitoring.exe_paths` 写完整路径：

```json
{
  "monitoring": {
    "mode": "tree",
    "exe_paths": [
      "D:\\Program Files\\FanxiangCloud\\ai-manager.exe",
      "D:\\Program Files\\FanxiangCloud\\llama-server.exe"
    ]
  }
}
```

`exe_paths` 与 `processes`/`apps` 可以并存；每个路径是独立根进程，只要进程的实际
`exe` 路径与配置一致，后续启动就会被发现，其子进程树同样动态纳入。

- 任一根进程后续出现，都会被自动发现并开始记录，无需重启监控；
- 只要某个根进程仍在运行，它后续新产生的整棵子进程都会动态加入，包括晚出现的
  `llama*.exe`；
- 子进程中途退出时记录一条 `process_exit`，不会删除历史曲线；退出后再次启动会自动续采；
- 如果“llama”不一定挂在上述根进程下面，可把它也加入 `roots`，工具会避免重复计数：

```json
{
  "name": "FanxiangCloud 示例",
  "roots": ["FanxiangCloud*.exe", "ai-manager.exe", "llama*.exe"]
}
```

如果多个目标分别属于几棵互不相干的进程树，也把它们都写进 `roots`，每棵树的进程会按各自
`root_pid`/`root_name`/`depth` 独立记录，通知汇总里逐进程列出 CPU 均值和内存峰值。

打开 GUI（监控、停止、加载历史 CSV 生成 HTML、导出 PNG/SVG 均可在同一窗口完成）：

```powershell
python -m watchdog_perf monitor
# 或
python -m watchdog_perf gui
```

## 回溯绘图

指定日期文件：

```powershell
python -m watchdog_perf plot data\Monitor_2026-09-08.csv
```

也可以直接给日期、目录或通配符：

```powershell
python -m watchdog_perf plot 2026-09-08
python -m watchdog_perf plot data
python -m watchdog_perf plot "data\Monitor_2026-09-*.csv"
```

默认会在 `data/reports/` 生成 ECharts 交互 HTML 并用浏览器打开：页面包含卡片摘要、CPU/内存两个独立图表面板，以及不依赖 JS 的静态统计表。

保存交互 HTML 报告：

```powershell
python -m watchdog_perf plot data\Monitor_2026-09-08.csv --save report.html
```

无窗口导出静态图片（仍使用 matplotlib 渲染 PNG/SVG/PDF）：

```powershell
python -m watchdog_perf plot data\Monitor_2026-09-08.csv --save report.png
```

交互 HTML 采用 ECharts 版式：滚动图例可筛选进程，鼠标悬浮/点击可查看具体数值，图表内部支持缩放并带时间轴滑块。每个进程/整机在两个图表中保持同一种颜色。GUI 里加载 CSV 与生成 HTML 都在后台线程执行，不会暂停正在运行的监控。

报告默认将图例放在右侧竖排；X 轴锁定到 CSV 数据真实首尾时间，CPU 轴固定为 0-100%，
内存轴从 0 到本机物理内存总量（优先使用 CSV 中记录的 `memory_total_mb`）。

CSV 中的进程 CPU 保持核心口径：占满 1 个逻辑核心记 100%，多线程进程可以大于 100%。绘图和 GUI 会把进程值除以逻辑核数，显示成“占整机容量的百分比”（范围 0-100），方便把各进程与整机放在同一把轴上。当指定进程目标时，每次样本还会记录一行 `SYSTEM`（pid=-1）作为真正的整机占用，该行始终是 0-100%；GUI 状态栏和图中的“整机占用”都优先显示这一行。加载历史版本未带 `SYSTEM` 行的进程 CSV 时，绘图会自动补一条“监控目标合计 (整机当量)”曲线，把同一样本内所有进程的核心 CPU% 求和后除以逻辑核数并封顶到 100%。

## 数据文件

每个本地日生成一个文件：`data/Monitor_YYYY-MM-DD.csv`（UTF-8 with BOM），字段如下：

| 字段 | 说明 |
| --- | --- |
| `timestamp` | 本地时间，毫秒精度 |
| `process_name` | 进程名；系统/整机行为 `SYSTEM` |
| `pid` | 进程 ID；系统/整机行为 `-1` |
| `cpu_percent` | 进程核心 CPU 占比（%）；`SYSTEM` 行为整机 CPU，范围 0-100 |
| `memory_mb` | 进程 Working Set / 系统已用内存（MB） |
| `status` | `0` 正常；`1` 进程未找到/退出；`2` 采集失败 |
| `extra` | 状态补充（如 `process_exit`、系统内存总量） |

系统模式额外的总内存/内存占用率等写进 `extra`，便于之后二次分析。

进程树模式时，同一时间点除了每个进程一行外，还会额外写入一行 `SYSTEM`（pid=-1），因此绘图可以同时展示各 EXE/进程曲线与整机占用。较旧版本生成的进程 CSV 没有这行，绘图会自动计算“监控目标合计”作为补偿。

进程树模式每行额外示例：

```text
root_pid=12345;root_name=app.exe;parent_pid=23456;depth=1
```

表示 PID 为 `23456` 的子进程属于根进程 `app.exe[12345]` 的一级后代。

## 配置文件

长时参数建议放在 JSON 配置中，命令行参数会覆盖配置文件：

```powershell
python -m watchdog_perf init-config
python -m watchdog_perf monitor --headless --config config.json
```

常用结构：

```json
{
  "monitoring": {
    "mode": "tree",
    "processes": ["app.exe", "worker*.exe"],
    "exe_paths": [],
    "pids": [12345],
    "include_children": true,
    "interval_sec": 1.0,
    "data_dir": "data"
  },
  "end_condition": {
    "enabled": true,
    "combine": "all",
    "sustained_sec": 300.0,
    "action": "notify_and_stop",
    "targets_gone": {
      "enabled": true,
      "grace_sec": 30.0
    },
    "rules": [
      {"metric": "cpu_machine_percent", "operator": ">=", "value": 85.0},
      {"metric": "memory_mb", "operator": ">=", "value": 4096.0, "enabled": false}
    ]
  },
  "notify": {
    "dingtalk": {
      "enabled": true,
      "webhook": "https://oapi.dingtalk.com/robot/send?access_token=...",
      "secret": "SEC..."
    }
  }
}
```

`combine` 为 `all` 表示规则同时满足才结束，`or` 表示任一规则满足即可。`cpu_machine_percent` 是把所有被监控进程的核心 CPU% 合计后除以逻辑核心数，换算成“占整机容量的百分比”，范围 0-100；`memory_mb` 是所有被监控进程 Working Set 的合计。条件连续满足 `sustained_sec` 秒后触发；`action` 可设为 `notify_and_stop` 或 `notify_only`，后者使用 `cooldown_sec` 防抖。

除指标规则外，`targets_gone` 提供“任务跑完自动收工”的结束条件：只要监控工具曾经发现过目标，
之后连续 `grace_sec` 秒没有任何目标进程存活，就发送汇总并（在 `action` 为 `notify_and_stop`
时）自动停止。目标从未出现过时不会误报，工具会持续等待后续启动；系统全局模式下不使用该条件。

钉钉消息会在触发时发送运行时段汇总：监控时长、样本数、整机当量 CPU 均值/峰值、内存合计均值/峰值，以及按进程排序的 CPU/内存明细。配置文件 `config.json` 已放本地并加入 `.gitignore`，`config.example.json` 是带占位符的模板。

## 可靠性设计

- 每个样本采集后立即 `append + flush`，默认每行执行一次 `os.fsync`；停止/跨天时也强制落盘。
- 每行写入完成即关闭不依赖内存缓存；重启后检测并截断异常中断留下的不完整末行，再继续向当天文件追加，不会重复表头。
- 0 点自动关闭旧文件并创建新文件；旧文件永久保留。
- 目标进程消失只记录一次 `status=1` 终止标记，不刷屏；进程重启后自动恢复采样。
- 磁盘剩余低于 100 MB（可用 `--disk-limit-mb` 调整）时停止采集并写 `ERROR_DISK_FULL.txt`，避免影响系统稳定性。
- 每 5 分钟在 `monitor.log` 记录监控进程自身 RSS，便于观察本工具是否有内存增长。

## 建议的验收对照

```powershell
python -m watchdog_perf monitor --process "你的程序名.exe" --headless --interval 1
```

运行 2 小时后执行：

```powershell
python -m watchdog_perf plot data\Monitor_当前日期.csv --save report.png
```

若监控程序或电脑蓝屏/强制结束，重新运行 `monitor` 会直接继续追加当天 CSV；原有行数不丢，用 `plot` 可看到完整曲线与终止点。

## 目录说明

```text
watchdog_perf/
  storage.py   按天滚动、逐行 flush、断行修复
  metrics.py   CPU 整机当量换算等公共统计
  monitor.py   进程发现/重启跟踪、CPU 内存采样、主循环
  plotting.py  CSV 解析、统计、matplotlib 静态导出
  html_report.py  ECharts 交互 HTML 报告
  gui.py       Tkinter 界面
  cli.py       monitor / gui / plot 命令入口
tests/         核心存储与绘图回归测试
```

若需要单文件 EXE，可在安装 `pyinstaller` 后执行：

```powershell
pyinstaller --onefile --name WatchdogPerf --collect-data matplotlib watchdog_perf_app.py
```
