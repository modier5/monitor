# Watchdog-Perf

Windows / macOS 长时进程性能监控与回溯工具。持续采集指定进程（或系统全局）的 CPU 与内存数据，并采用**逐行追加 + 立即 flush** 的方式写入按监控会话滚动的 CSV，尽量降低死机或强制结束进程时的数据丢失；之后可离线加载历史文件生成可缩放、可平移的交互 HTML 图表。

桌面端采用侧栏 + 内容区的现代界面：浅色模式下侧栏与内容区都是浅色（白色导航栏、
浅灰画布），深色模式下整体切换为深色；圆角面板、翡翠绿强调色、平台原生字体
（Windows 用微软雅黑 UI，macOS 用苹方 PingFang SC）。**外观可在应用内切换**
（侧栏底部「浅色 / 深色 / 跟随系统」，默认跟随系统），选择会写回 `config.json`。
界面使用 CustomTkinter，打包产物已内置该依赖。

## 下载与安装

从 [Releases](https://github.com/modier5/monitor/releases) 下载对应系统的压缩包，
**解压一次即可**（压缩包内直接就是程序目录，不是“压缩包里再套一个压缩包”）：

- Windows x64：`WatchdogPerf-Windows-x64.zip` → 解压得到 `WatchdogPerf/`，双击里面的
  `WatchdogPerf.exe`（无需安装 Python）
- Apple Silicon（M1/M2/M3/M4 等）：`WatchdogPerf-macOS-arm64.zip` → 解压得到
  `WatchdogPerf.app`，拖入“应用程序”即可
- Intel Mac：`WatchdogPerf-macOS-x86_64.zip`

> 仓库的 Actions → Artifacts 里也能下载，但 GitHub 会给所有产物再套一层 zip；
> 面向使用者分发请用 Releases 页面的附件。

## macOS 开箱即用版

普通使用者不需要安装 Python 或依赖：下载与 Mac 处理器匹配的压缩包，解压后把
`WatchdogPerf.app` 拖入“应用程序”，双击即可使用。

- Apple Silicon（M1/M2/M3/M4 等）：`WatchdogPerf-macOS-arm64.zip`
- Intel Mac：`WatchdogPerf-macOS-x86_64.zip`
- 第一次启动自动创建 `~/Library/Application Support/WatchdogPerf/config.json`
- CSV、日志、备份与报告默认写到
  `~/Library/Application Support/WatchdogPerf/data/`
- GUI 在 macOS 显示“macOS 应用 (.app)”并提供两个按钮：“选择 .app”用于选择整个应用包，
  “包内程序…”会在选定应用后直接进入其 `Contents/MacOS` 目录，以便选择实际 Unix
  可执行文件，不需要手工复制路径。Windows 显示“Windows 程序 (.exe)”，
  直接选择可执行文件；两端都支持进程名和 PID
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

## 图形界面使用

桌面端左侧是深色导航栏，右侧内容区随系统切换明暗主题，五个页面分别是：

- **运行概览**：显示本次监控的整机 CPU、目标内存、样本数量、输出文件，以及打开目录、
  加载历史 CSV、导出报告的快捷入口；
- **监控目标**：添加 Windows 程序、macOS 应用、进程名或 PID，列表会显示每个目标的类型和路径；
- **采集与存储**：设置采样间隔、是否包含子进程、CSV 保存目录、落盘和备份策略；
- **结束条件**：设置 CPU/内存阈值、规则组合方式、持续时间、目标退出条件和触发动作；
- **通知设置**：配置钉钉机器人 Webhook、Secret 和请求超时。

侧栏底部有「浅色 / 深色 / 跟随系统」外观开关，默认跟随系统；切换后会立刻生效并
写入 `config.json` 的 `appearance` 字段，下次启动保持上次的选择。

修改后点击“保存配置”会立即写入当前用户的 `config.json`；点击“保存并开始”会先校验并
保存全部界面设置，再启动监控。Windows 桌面包默认保存到：

```text
%LOCALAPPDATA%\WatchdogPerf\config.json
```

macOS 桌面包默认保存到：

```text
~/Library/Application Support/WatchdogPerf/config.json
```

### Windows 和 macOS 应该选择什么目标

| 平台 | GUI 选项 | 推荐选择 | 示例 |
|---|---|---|---|
| Windows | Windows 程序 (.exe) | 程序实际的 `.exe` 文件 | `C:\Program Files\Demo\Demo.exe` |
| macOS | macOS 应用 (.app) | Finder 里看到的 `.app` 应用包（推荐） | `/Applications/Google Chrome.app` |
| macOS | 包内程序… | 精确选择 `.app/Contents/MacOS` 中的 Unix 可执行文件 | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |
| 两个平台 | 进程名 | 活动监视器或任务管理器中的进程名 | `Google Chrome` / `ffmpeg.exe` |
| 两个平台 | PID | 当前正在运行的进程数字 PID | `12345` |

macOS 的 `.app` 在文件系统中是“应用程序包目录”，不是单个 exe。真正运行的主程序通常位于：

```text
/Applications/应用名.app/Contents/MacOS/可执行文件
```

**推荐做法**：在“监控目标”页点“选择 .app”，弹出的 macOS 系统选择器可以直接选中 `应用程序`
文件夹里的 `.app`，不需要右键“显示包内容”。WatchdogPerf 会读取 `Contents/Info.plist`
中的 `CFBundleExecutable`，把 `.app` 与包内 `Contents/MacOS/` 下的实际进程对应起来；
启用“包含子进程”后，该应用启动的辅助进程、渲染进程和工作进程也会继续被跟踪。

**精确选择包内程序**：点击“包内程序…”，先选择一个 `.app`，随后 WatchdogPerf 会弹出自己的
程序列表，列出该应用 `Contents/MacOS` 目录中的全部文件（主程序排在最前，并标注“主程序”）。
这是刻意绕开 macOS 文件选择器的限制：系统面板把 `.app` 当作单个文件，**无法进入包内部**，
所以“进入应用选程序”由应用自己完成，不受系统版本影响。选中后完整路径会自动填入目标输入框。
两种写法都支持：

- 填 `.app`：按“包内主程序”匹配，应用升级、重启后仍然有效；
- 填包内可执行文件：按精确路径匹配，同样会在进程重启后自动重新绑定。

只有当某个应用把真正干活的进程交给**不属于它**的独立服务时（不在它的子进程树里），才需要
额外把那个进程名也加进目标，或使用 `monitoring.apps` 配置。

下列情况更适合选择“进程名”而不是 `.app`：

- 命令行工具，例如 `ffmpeg`、`python3`；
- 没有 `.app` 外壳的后台服务；
- 一个 `.app` 只负责启动另一个完全独立、且不属于其子进程树的服务；
- 希望使用通配符匹配会反复启动的同类进程。

> 手工复制路径的方法：在 Finder 中右键 `.app` →“显示包内容”→ `Contents/MacOS`，选中
> 目标文件后按 `Option + Command + C`，即可复制完整 POSIX 路径；不过通常直接使用 GUI 的
> “包内程序…”更方便。

配置文件中的字段仍命名为 `monitoring.exe_paths`，这是为了兼容旧版配置；在 macOS 上该字段
同样可以保存 `.app` 路径或包内可执行文件路径，并不表示用户必须寻找 exe 文件。

## 快速开始

依赖为 `psutil`、`matplotlib` 和 `CustomTkinter`；HTML 报告使用浏览器端的 ECharts，库已内嵌，离线也可打开：

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
# 也可以直接指定包内的 Unix 可执行文件
python -m watchdog_perf monitor --exe "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless
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
**报告与桌面端使用同一套视觉**（同样的翡翠绿配色、圆角卡片、微软雅黑 UI / 苹方字体），
并随浏览器/系统的浅色或深色外观自动切换；静态 PNG/SVG/PDF 导出也用同一套调色板，
因此同一个进程在 GUI、HTML 报告和导出的图片里颜色保持一致。

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
  gui.py       CustomTkinter 界面
  ui_icons.py  运行时绘制的矢量图标与品牌标记
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
无需 Python 的压缩包。

- **GitHub Releases（推荐，单层压缩包）**：打 tag 时两个工作流会把三个压缩包作为
  Release 附件自动上传，使用者下载后解压一次即可；Windows 包内是 `WatchdogPerf/`
  目录，macOS 包内是 `WatchdogPerf.app`。
- **Actions → Artifacts（CI 自用）**：GitHub 会给每个产物再套一层 zip。Windows 产物
  已改为上传解压后的目录，所以只需解压一次就能得到 `WatchdogPerf/`；macOS 因为
  `.app` 内含符号链接，必须保留 `ditto` 生成的 zip，请优先使用 Releases。
