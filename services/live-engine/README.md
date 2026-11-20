# VigorSync Live Engine M1

本服务负责 M1 的完整镜端实时链路：

```text
本机摄像头 -> 采集线程 -> MediaPipe Pose -> 角度/状态机
                                  -> MJPEG + WebSocket
                                  -> 录像 + landmarks.jsonl + events.jsonl
```

## 启动

在 VigorSync 根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r services\live-engine\requirements.txt
$env:PYTHONPATH = "services\live-engine\src"
.\.venv\Scripts\python.exe -m vigorsync_live
```

## 实时纠错

Live Engine 在 rep 状态机之外运行逐帧动作规则：

- 深蹲正面：膝内扣。
- 深蹲侧面：躯干前倾。
- 俯卧撑侧面：塌腰、臀部过高。
- 俯卧撑正面：手肘外张。

规则先产生候选，再由 6 帧窗口内至少 4 帧、4 秒问题码冷却的聚合器转换为
稳定的 `form.alert`。告警携带 `joints`，MJPEG 骨骼图会直接高亮相应关节，
镜端同步显示分级纠正短句。低可见度帧不会记为“无问题”，避免 Pose 抖动破坏窗口。

## Windows 本地语音

Windows 默认启用 `System.Speech` + SAPI 本地语音，并优先选择简体中文系统语音。
短句会预生成为本地 WAV，再通过 Windows 系统播放器输出，绕过 SAPI 直接播放
在某些驱动上“返回成功但无声”的问题。语音运行在独立线程中，不阻塞 Pose、录像或
WebSocket：

- 普通纠错要求置信度不低于 `0.75`。
- 同一问题码至少间隔 5 秒，普通提示全局至少间隔 3 秒。
- 只播报 5 次、10 次和目标达成里程碑。
- 队列默认最多 4 条，满队列优先保留高严重度纠错。
- 暂停或结束训练会停止当前语音并清空待播队列。
- 固定纠错和里程碑短句会在会话启动时后台预热并缓存。
- SAPI 初始化、WAV 合成或播放失败时自动停止语音功能，训练和计数继续。

可用环境变量：

```powershell
$env:VIGORSYNC_TTS_ENABLED = "1"
$env:VIGORSYNC_TTS_VOICE = "Huihui"
$env:VIGORSYNC_TTS_RATE = "170"
$env:VIGORSYNC_TTS_VOLUME = "1.0"
$env:VIGORSYNC_TTS_QUEUE_SIZE = "4"
$env:VIGORSYNC_TTS_MIN_CONFIDENCE = "0.75"
```

镜端训练 HUD 可即时静音和恢复；开发接口也可控制：

```http
GET  /dev/v1/sessions/{session_id}/speech
POST /dev/v1/sessions/{session_id}/speech

{"enabled": false}
```

服务默认只监听 `127.0.0.1:8020`。健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8020/api/v1/health
```

镜端最小页面见 `apps/mirror/`。页面在 M1 使用 `/dev/v1/sessions` 本机开发接口；
正式接入 Core 后使用 OpenAPI 中的内部会话接口。

## 摄像头配置

通过环境变量选择摄像头：

```powershell
$env:VIGORSYNC_CAMERA_INDEX = "1"
$env:VIGORSYNC_CAMERA_WIDTH = "640"
$env:VIGORSYNC_CAMERA_HEIGHT = "480"
$env:VIGORSYNC_CAMERA_FPS = "30"
```

Windows 默认使用索引 `1`，对应当前项目使用的 USB 彩色摄像头。若 Windows 重新枚举
设备导致索引变化，可用 `VIGORSYNC_CAMERA_INDEX` 覆盖。

默认模型复杂度为 `0`，即 MediaPipe Pose Lite，并使用 CPU 推理。Windows 默认使用
首帧更快的 DirectShow；`VIGORSYNC_CAMERA_BACKEND` 可设为 `auto`、`dshow` 或 `msmf`。
`VIGORSYNC_INFERENCE_FPS` 默认限制为 `20`，推理线程只处理最新帧，超过预算的旧帧会
被跳过。
MJPEG 与骨架合成默认限制为 `15 FPS`，可通过 `VIGORSYNC_PREVIEW_FPS` 调整。

运行中的会话详情 `GET /dev/v1/sessions/{session_id}` 会返回
`startup_timings`，包含摄像头打开、首帧、Pose 初始化、首次推理和进入 `active`
的毫秒数。Windows 本机实测 DirectShow 首帧约 `2.56–2.94 s`，MSMF 约
`6.30–9.55 s`，因此当前继续使用 DirectShow。

## 实时语义

- 深蹲使用膝角，俯卧撑使用肘角。
- 侧视优先同侧关节，正视使用左右均值。
- 右视俯卧撑使用单独的回顶与最短周期参数，避免右侧肘角偏低导致多个 rep 被合并。
- 左视俯卧撑使用 1.1 秒 rep 冷却，过滤回顶后 0.8–1.0 秒内的二次反弹。
- 侧视俯卧撑增加肩到踝的姿态有效性门槛；起身、站立或走近镜头时即使肘角变化，
  也不会开始新的 rep 周期。若一帧关键点不可见，则回退到肩髋轴判断。
- 状态机使用进入/退出双阈值、最短相位时间、置信度过滤、冷却时间和状态迁移防重复。
- 实时结果是训练过程中的 provisional 计数；最终报告仍以离线 RepChain 为准。
- 当前实时提示覆盖深蹲膝内扣/前倾、俯卧撑塌腰/臀高/手肘外张，以及动作深度。

## 会话产物

默认写入：

```text
webdata/sessions/<session_id>/
├── input/source.mp4
├── input/landmarks.jsonl
├── live/events.jsonl
└── live/summary.json
```

`source.mp4` 保存未叠加 UI 的相机画面；MJPEG 预览是在另一条推理链上绘制骨架与指标。

## 开发接口

仅监听回环的开发接口：

```http
POST /dev/v1/sessions
GET  /dev/v1/sessions/{session_id}
POST /dev/v1/sessions/{session_id}/pause
POST /dev/v1/sessions/{session_id}/resume
POST /dev/v1/sessions/{session_id}/stop
GET  /dev/v1/sessions/{session_id}/speech
POST /dev/v1/sessions/{session_id}/speech
```

契约接口：

```http
POST /internal/v1/sessions/{session_id}/start
POST /internal/v1/sessions/{session_id}/pause
POST /internal/v1/sessions/{session_id}/resume
POST /internal/v1/sessions/{session_id}/stop
GET  /internal/v1/sessions/{session_id}/speech
POST /internal/v1/sessions/{session_id}/speech
GET  /live/{session_id}/stream.mjpg
WS   /ws/live/{session_id}
```

若配置 `VIGORSYNC_INTERNAL_TOKEN`，内部接口必须携带 `X-Internal-Token`。

## 验证

```powershell
$env:PYTHONPATH = "services/live-engine/src"
.\.venv\Scripts\python.exe -m unittest discover -s services\live-engine\tests -v
```

摄像头单帧冒烟：

```powershell
$env:PYTHONPATH = "services\live-engine/src"
.\.venv\Scripts\python.exe services\live-engine\scripts\smoke_camera.py --exercise squat --view side_left
```

固定视频 Pose 冒烟：

```powershell
$env:PYTHONPATH = "services\live-engine/src"
.\.venv\Scripts\python.exe services\live-engine\scripts\replay_video.py --video ..\VLS\video\source1.mp4 --exercise squat --view side_left --max-frames 120
```
