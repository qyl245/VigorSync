# VigorSync Mirror M1

无构建依赖的最小镜端训练页，用于 M1 本地闭环验证。

双击 `scripts\start_mirror.cmd` 会启动实时引擎、镜端页面和 Kiosk 浏览器；
双击 `scripts\stop_mirror.cmd` 可统一关闭。需要手工分步启动时：

先启动 Live Engine：

```powershell
cd E:\project\Exercise\VigorSync
$env:PYTHONPATH = "services\live-engine\src"
.\.venv\Scripts\python.exe -m vigorsync_live
```

再启动镜端页面：

```powershell
cd E:\project\Exercise\VigorSync
.\.venv\Scripts\python.exe apps\mirror\server.py
```

浏览器打开 `http://127.0.0.1:8080`。页面通过本机开发 API 创建 Live Engine
会话，并直接显示 `8020` 端口的 MJPEG 和 WebSocket 数据。

M1 页面暂不包含手机控制、Core API、TTS 和离线报告。后续接 Core 时，只需把
开发 API 的会话创建与命令替换成 Core WebSocket 指令，训练画面和指标组件可继续使用。

训练 HUD 包含暂停感知计时、目标次数进度、主关节角仪表、动作阶段、置信度和推理
频率。关节角由 `live.metrics.joint_angle` 提供：深蹲显示膝角，俯卧撑显示肘角。
