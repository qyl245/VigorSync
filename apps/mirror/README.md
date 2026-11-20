# VigorSync Mirror

无构建依赖的镜端训练页，用于 M2 的 Core 控制闭环验证。

双击 `scripts\start_mirror.cmd` 会启动实时引擎、镜端页面和 Kiosk 浏览器；
双击 `scripts\stop_mirror.cmd` 可统一关闭。需要手工分步启动时：

先启动 Core API：

```powershell
cd E:\project\Exercise\VigorSync
$env:PYTHONPATH = "services\core-api\src"
.\.venv\Scripts\python.exe -m vigorsync_core
```

再启动 Live Engine，并让它向 Core 上报：

```powershell
cd E:\project\Exercise\VigorSync
$env:PYTHONPATH = "services\live-engine\src"
$env:VIGORSYNC_CORE_URL = "http://127.0.0.1:8011"
.\.venv\Scripts\python.exe -m vigorsync_live
```

再启动镜端页面：

```powershell
cd E:\project\Exercise\VigorSync
.\.venv\Scripts\python.exe apps\mirror\server.py
```

浏览器打开 `http://127.0.0.1:8080`。页面通过 Core 的本机开发 API 创建和控制
会话，直接显示 `8020` 端口的 MJPEG，并由 Core 转发会话事件。

手机 UI 已位于 `apps/mobile/`，可完成配对、训练控制、历史查看和离线复盘。
Windows 镜端已接入本机 SAPI TTS，训练 HUD 可即时静音和恢复；语音由 Live Engine
的独立有界队列播放，不会阻塞 Pose、计数、录像或页面状态。

镜端页面默认处于跟随模式：打开页面后会自动连接 Core，手机启动训练时同步进入
实时训练 HUD，并跟随暂停、继续和结束状态；刷新页面也会恢复当前活动会话。
手机首页选择的动作、机位和目标次数会实时同步到镜端配置页；手机离开训练总结页
时镜端也返回配置页。镜端仍保留自己的训练配置入口，可独立发起和控制训练。

训练结束后镜端总结页会跟随离线任务的阶段和进度；报告生成后可打开完整复盘。
即使已经返回配置页，最近一次离线报告也会显示在配置卡片下方。

训练 HUD 包含暂停感知计时、目标次数进度、主关节角仪表、动作阶段、置信度和推理
频率。关节角由 `live.metrics.joint_angle` 提供：深蹲显示膝角，俯卧撑显示肘角。
