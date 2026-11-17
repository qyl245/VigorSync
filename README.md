# VigorSync

面向家庭健身镜的产品工作区。M0 方案与契约已冻结，M1 本机实时闭环和 M2
手机控制闭环已落地。

## 工作区定位

- 本目录是产品化主工作区。
- `../VLS/` 下的 `calimirror`、`calimiro_app`、`app`、`WorkoutTracker` 作为源码与算法参考，不在本目录之外继续扩展产品功能。
- 现有项目不会被整体合并成一个进程，而是按镜端、移动端、核心服务、实时引擎和离线分析拆分为独立模块。
- 第一版只支持深蹲和俯卧撑，单用户、单镜端、本地局域网部署。

## 目标产品

```text
手机 App 发起训练
  -> 镜端实时显示摄像头、骨架、计数和提示
  -> 训练结束自动进入离线分析
  -> 手机查看训练摘要、问题卡和趋势
  -> 桌面端查看详细回放报告
```

实时层只做计数、动作阶段和明显错误提示；精细评估、低置信拒评、问题定位和报告以离线 RepChain 结果为准。

## 文档

- [系统构建方案](docs/01-系统构建方案.md)
- [接口与数据契约](docs/02-接口与数据契约.md)
- [实施路线与验收](docs/03-实施路线与验收.md)
- [OpenAPI 契约](packages/contracts/openapi.yaml)
- [会话状态机](packages/contracts/session-state-machine.json)

## 契约校验

```bash
cd VigorSync
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-contracts.txt
.venv/bin/python scripts/validate_contracts.py
.venv/bin/python -m unittest discover -s tests/contracts -v
```

也可以运行 `make check`，它会优先使用项目内的 `.venv`，其次使用工作区父目录的 `.venv`。校验过程完全离线。Windows 下请将 `.venv/bin/python` 替换为 `.venv\Scripts\python.exe`。

## M1 本机运行

首次安装 M1 依赖：

```powershell
cd VigorSync
.\.venv\Scripts\python.exe -m pip install -r services\live-engine\requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-contracts.txt
```

启动实时引擎：

```powershell
$env:PYTHONPATH = "services\live-engine\src"
.\.venv\Scripts\python.exe -m vigorsync_live
```

另开终端启动镜端页面：

```powershell
.\.venv\Scripts\python.exe apps\mirror\server.py
```

浏览器打开 `http://127.0.0.1:8080`。默认摄像头索引为 `0`，可通过
`VIGORSYNC_CAMERA_INDEX` 切换；完整配置和开发/契约接口见
[Live Engine 说明](services/live-engine/README.md)。

也可以直接双击一键启动/关闭：

```text
scripts\start_mirror.cmd
scripts\stop_mirror.cmd
scripts\status_mirror.cmd
```

一键启动会同时启动 Live Engine、镜端静态服务和专用 Kiosk 浏览器；关闭脚本只终止
该组脚本创建的服务和浏览器。运行日志及 PID 状态保存在 `webdata/runtime/`。
使用 `mirror-control.ps1 -Windowed` 可改为普通窗口，`-NoBrowser` 只启动服务。

轻量桌面外壳：

```text
dist\VigorSyncLauncher\VigorSyncLauncher.exe
```

双击启动原生 WebView2 窗口，点击窗口 `X` 时优雅停止训练并释放后台服务。开发环境
也可双击 `scripts\start_desktop.cmd`。重新构建启动器：

```powershell
.\scripts\build_launcher.ps1
```

M1 离线检查（不占用摄像头）：

```powershell
.\scripts\check_m1.ps1
```

## M2 手机端运行

一键启动 Core API、Live Engine 和镜端页面：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\mirror-control.ps1 -Action start -NoBrowser
```

Android 模拟器运行：

```powershell
cd apps\mobile
D:\flutter\bin\flutter.bat run -d emulator-5554
```

Core 默认监听 `0.0.0.0:8011`。模拟器填写 `http://10.0.2.2:8011`，实体手机填写
电脑局域网 IPv4 地址；开发配对码默认为 `482913`。App 的配对、构建、测试和
目录说明见 [手机端说明](apps/mobile/README.md)。

M2 回归检查：

```powershell
.\scripts\check_m2.ps1
.\scripts\check_m2.ps1 -BuildApk
```

## 计划目录

代码实现开始后按以下结构落地：

```text
VigorSync/
├── apps/
│   ├── mirror/          # calimirror 派生的大屏镜端
│   ├── mobile/          # calimiro_app 派生的 Flutter 伴侣端
│   └── report-web/      # VLS/app 的 Vue 报告前端
├── services/
│   ├── core-api/        # 会话、配对、历史、任务编排
│   ├── live-engine/     # 摄像头、Pose、计数、TTS、录像
│   └── offline-worker/  # RepChain、评估、问题卡、报告
├── packages/
│   └── contracts/       # OpenAPI、JSON Schema、事件定义
├── deployments/         # systemd、Docker Compose、反向代理
├── scripts/             # 开发、迁移、压测和验收脚本
├── tests/               # 跨服务端到端与回放测试
└── docs/
```

## 关键决策

1. 镜端使用 `calimirror` 作为 Kiosk 外壳，但不复用其现有训练业务逻辑。
2. 实时引擎由新增的 Python 服务负责，摄像头由实时引擎独占。
3. 镜端通过本地 MJPEG 和 WebSocket 获取画面与状态，第一版不搬运原始摄像头帧到手机。
4. 手机 App 主要负责控制、实时摘要、历史和报告，不承担镜端主推理。
5. 离线分析复用 `VLS/app` 的 FastAPI、RepChain、问题卡和 Vue 报告能力。
6. `WorkoutTracker` 仅作为可选离线分类/分割组件，不进入实时关键路径。

## 当前状态

- 阶段：M0、M1 已完成，M2 手机控制闭环已在 Android AVD 验收
- M1 包含摄像头采集、MediaPipe Pose、深蹲/俯卧撑计数、骨架 MJPEG、实时 WebSocket、本地录像与关键点/事件落盘、最小镜端页面
- 提供约 8 MB 的轻量桌面启动器，使用 WebView2 原生窗口并通过 `X` 完成优雅退出
- 镜端页面提供暂停感知计时、目标次数进度、主关节角和实时推理状态
- 镜端可跟随手机启动、暂停、继续和结束训练，页面刷新后自动恢复当前会话
- 手机首页的动作、机位和目标次数会实时同步到镜端，离开训练总结时镜端同步返回
- 手机端支持持久化深色/浅色主题切换，并为深蹲、俯卧撑使用独立姿态图标
- 手机历史支持永久删除已结束训练，并同步删除 Core 记录和本机训练产物
- 手机端将 Core 的 UTC 时间转换为设备本地时间，并以中文状态、机位和时长展示
- 启动链路已合并创建/启动请求并复用 Core 到 Live Engine 的 HTTP 连接；AVD
  点击到 `active` 实测约 `3.6 s`
- 实时推理默认限制为 20 FPS，只处理最新帧；可通过 `VIGORSYNC_INFERENCE_FPS` 调整
- MJPEG 预览和骨架合成默认限制为 15 FPS；可通过 `VIGORSYNC_PREVIEW_FPS` 调整
- 下一步：实体 Android 手机回归，然后进入 M3 离线分析闭环

M2 已完成 AVD 验收：Core API 已实现会话、配对、幂等命令、SQLite 持久化以及
手机/镜端 WebSocket；镜端训练页已通过 Core 受控，Flutter 手机端已完成配对、
训练控制、实时状态和历史页，停止后进入 `analyzing` 等待 M3。
