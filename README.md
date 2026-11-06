# Watchdog-Perf

Windows / macOS 长时进程性能监控与回溯工具。持续采集指定进程（或系统全局）的 CPU 与内存数据，并采用**逐行追加 + 立即 flush** 的方式写入按监控会话滚动的 CSV，尽量降低死机或强制结束进程时的数据丢失；之后可离线加载历史文件生成可缩放、可平移的交互 HTML 图表。

桌面端采用现代圆角界面，提供侧栏导航、运行概览、图形化目标管理、完整配置编辑和
系统明暗主题适配。界面使用 CustomTkinter，打包产物已内置该依赖。

## macOS 开箱即用版

普通使用者不需要安装 Python 或依赖：下载与 Mac 处理器匹配的压缩包，解压后把
`WatchdogPerf.app` 拖入“应用程序”，双击即可使用。

- Apple Silicon（M1/M2/M3/M4 等）：`WatchdogPerf-macOS-arm64.zip`
- Intel Mac：`WatchdogPerf-macOS-x86_64.zip`
- 第一次启动自动创建 `~/Library/Application Support/WatchdogPerf/config.json`
- CSV、日志、备份与报告默认写到
  `~/Library/Application Support/WatchdogPerf/data/`
- GUI 可通过“应用/可执行文件、PID、进程名”三种方式添加监控目标，macOS
  可浏览选择 `.app`，Windows 可浏览选择 `.exe`
- 采样、CSV 目录、备份、结束条件与钉钉通知均可在 GUI 中修改并保存回
  `config.json`；应用稍后启动或重启也能重新发现

未使用 Apple Developer ID 公证的内部测试包，首次需在 Finder 中按住 Control 点按应用，
选择“打开”。面向普通用户分发时应使用下文的签名/公证步骤，公证后的包可以正常双击打开。

### 在 macOS 上构建 `.app`

PyInstaller 不能从 Windows 交叉编译 macOS 应用。项目提供了两种构建方式：

```bash
# 任意 Mac 本机一键构建、测试并打包当前 CPU 架构
bash scripts/build_macos.sh
```

也可以在 GitHub Actions 手动运行 `Build macOS app`，一次生成 Apple Silicon 和 Intel
两个构件。当前 GitHub 托管运行器中 `macos-15` 是 arm64，`macos-15-intel` 是
x86_64。构建产物已经内置 Python、psutil、Tk/Tcl、matplotlib 和离线 ECharts。

正式对外分发前，在已安装 `Developer ID Application` 证书的 Mac 上执行：

```bash
export CODESIGN_IDENTITY='Developer ID Application: Your Name (TEAMID)'
export APPLE_ID='you@example.com'
export APPLE_TEAM_ID='TEAMID'
export APPLE_APP_PASSWORD='xxxx-xxxx-xxxx-xxxx'
bash scripts/notarize_macos.sh dist/WatchdogPerf.app
```

这会启用 Hardened Runtime、提交 Apple 公证、装订公证票据并输出最终 zip。应用不启用
App Sandbox，因为它需要读取当前用户启动的其他进程；通常不需要管理员权限，但 macOS
可能拒绝读取其他用户或受系统保护进程的详细路径，此时这些进程会被跳过，整机监控不受影响。

## 快速开始

依赖为 `psutil` 和 `matplotlib`；HTML 报告使用浏览器端的 ECharts，库已内嵌，离线也可打开：

```shell
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

按可执行文件完整路径采集（进程之后才启动也会被自动发现；可多次指定）：

```powershell
python -m watchdog_perf monitor --exe "D:\Apps\FanxiangCloud\ai-manager.exe" --headless
```

macOS 示例（`.app` 配置会自动匹配包内 `Contents/MacOS/` 下的实际可执行文件）：

```bash
python -m watchdog_perf monitor --process "Google Chrome" --headless
python -m watchdog_perf monitor --exe "/Applications/Google Chrome.app" --headless
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

每一条进程/子进程数据都会独立记录，并在 `extra` 中写入该进程实际 `exe_path` 以及
`root_pid`、`root_name`、`parent_pid`、`depth`。同一 exe 路径即使被短任务反复启动、
每次 PID 都不同，绘图时也会合并成一条进程曲线，而不是一次任务一段曲线。

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
同名的不同 exe 路径会按 `exe_path` 区分；旧版 CSV 未记录路径时，绘图退化为按进程名合并。

打开 GUI（添加应用/可执行文件、PID 或进程名，编辑全部配置，选择 CSV 保存目录，
监控、停止、加载历史 CSV 和导出报告均可在同一窗口完成）：

```powershell
python -m watchdog_perf monitor
# 或
python -m watchdog_perf gui
```

## 回溯绘图

指定日期文件：

```powershell
python -m watchdog_perf plot data\Monitor_2026-09-08_001.csv
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
python -m watchdog_perf plot data\Monitor_2026-09-08_001.csv --save report.html
```

无窗口导出静态图片（仍使用 matplotlib 渲染 PNG/SVG/PDF）：

```powershell
python -m watchdog_perf plot data\Monitor_2026-09-08_001.csv --save report.png
```

交互 HTML 采用 ECharts 版式：滚动图例可筛选进程，鼠标悬浮/点击可查看具体数值，图表内部支持缩放并带时间轴滑块。每个 exe/整机在两个图表中保持同一种颜色；同一 exe 多次启动的 PID 不再拆成多条。GUI 里加载 CSV 与生成 HTML 都在后台线程执行，不会暂停正在运行的监控。

报告默认将图例放在右侧竖排；X 轴锁定到 CSV 数据真实首尾时间，CPU 轴固定为 0-100%，
内存轴从 0 到本机物理内存总量（优先使用 CSV 中记录的 `memory_total_mb`）。

CSV 中的进程 CPU 保持核心口径：占满 1 个逻辑核心记 100%，多线程进程可以大于 100%。绘图和 GUI 会把进程值除以逻辑核数，显示成“占整机容量的百分比”（范围 0-100），方便把各进程与整机放在同一把轴上。当指定进程目标时，每次样本还会记录一行 `SYSTEM`（pid=-1）作为真正的整机占用，该行始终是 0-100%；GUI 状态栏和图中的“整机占用”都优先显示这一行。加载历史版本未带 `SYSTEM` 行的进程 CSV 时，绘图会自动补一条“监控目标合计 (整机当量)”曲线，把同一样本内所有进程的核心 CPU% 求和后除以逻辑核数并封顶到 100%。

## 数据文件

每次启动监控生成一个文件：`data/Monitor_YYYY-MM-DD_001.csv`（UTF-8 with BOM），
其中日期是本次启动日期，`001` 是当天第几次启动。一次监控运行期间即使跨过 0 点，
数据仍持续追加到同一个文件；当天再次启动监控会创建 `002`、`003` 等新文件。
字段如下：

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
exe_path=D:\apps\worker.exe;root_pid=12345;root_name=app.exe;parent_pid=23456;depth=1
```

表示 PID 为 `23456` 的子进程属于根进程 `app.exe[12345]` 的一级后代。
`exe_path` 是新版新增字段，用于把同一 exe 的多次调用/PID 在回溯图中合并展示。

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
    "data_dir": "data",
    "backup_every_rows": 10000,
    "backup_keep": 3
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

- 每个样本采集后立即 `append + flush`，默认每行执行一次 `os.fsync`；停止时也强制落盘。
- 每行写入完成即落盘，不依赖内存缓存；一次监控启动对应一个会话文件，跨 0 点也不会切换文件。
- 达到 `backup_every_rows` 行时，在 `data/backups/` 生成当前会话的完整 CSV 快照；默认保留最近 `backup_keep` 份，超过数量后自动清理最旧快照。备份目录不会参与 `plot` 的批量加载。
- 目标进程消失只记录一次 `status=1` 终止标记，不刷屏；进程重启后自动恢复采样。
- 磁盘剩余低于 100 MB（可用 `--disk-limit-mb` 调整）时停止采集并写 `ERROR_DISK_FULL.txt`，避免影响系统稳定性。
- 每 5 分钟在 `monitor.log` 记录监控进程自身 RSS，便于观察本工具是否有内存增长。

## 建议的验收对照

```powershell
python -m watchdog_perf monitor --process "你的程序名.exe" --headless --interval 1
```

运行 2 小时后执行：

```powershell
python -m watchdog_perf plot data\Monitor_2026-09-10_001.csv --save report.png
```

若监控程序或电脑蓝屏/强制结束，已 flush 的历史行保留；重新运行 `monitor` 会在当天创建新的会话 CSV，
用 `plot` 仍可一次加载当天全部会话文件。若主 CSV 损坏，可手动加载 `data\backups\` 中最近的完整快照。

## 目录说明

```text
watchdog_perf/
  storage.py   按监控会话原子建文件、逐行 flush
  metrics.py   CPU 整机当量换算等公共统计
  monitor.py   进程发现/重启跟踪、CPU 内存采样、主循环
  plotting.py  CSV 解析、统计、matplotlib 静态导出
  html_report.py  ECharts 交互 HTML 报告
  gui.py       Tkinter 界面
  cli.py       monitor / gui / plot 命令入口
tests/         核心存储与绘图回归测试
```

## 桌面应用打包

Windows 目录版（包含 Python 与全部依赖，无需用户安装运行环境）：

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean WatchdogPerf-Windows.spec
```

生成目录为 `dist/WatchdogPerf/`。必须完整分发该目录，入口为
`dist/WatchdogPerf/WatchdogPerf.exe`。Windows 和 macOS 的应用图标分别来自
`assets/watchdogperf.ico` 与 `assets/watchdogperf.icns`。

推送 `v*` 标签后，GitHub Actions 会自动运行 `Build Windows app` 和
`Build macOS app`：生成 Windows x64、macOS Apple Silicon 和 macOS Intel 三种
无需 Python 的压缩包，可在对应 Actions 运行页底部的 Artifacts 下载。
