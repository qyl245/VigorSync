# VigorSync Offline Worker

离线分析 Worker，复用经锁定的 VLS RepChain 产品运行时。服务仅监听
`127.0.0.1:8030`，只接受 Core 的内部任务。

## 范围

- 从训练会话接收本机视频路径。
- 接收同一会话的实时 `events.jsonl`，按 canonical issue code 与离线问题卡关联。
- 使用共享磁盘硬链接，失败时回退为复制。
- 执行 VLS 的 `analysis_v1 -> RepChain -> issues -> feedback_v2 -> report` 流水线。
- 维护独立 SQLite 队列和任务产物。
- 生成报告 HTML、证据帧、媒体片段和完整产物清单。
- 实时-only 问题在报告中标记为“离线未形成问题卡”，不改变离线结论。

样例库、上传页面和 VLS 实验脚本未迁入本服务。

## 目录

```text
src/vigorsync_offline/     内部 FastAPI 适配层
vendor/vls/web/backend/    锁定的 VLS 后端运行时
vendor/vls/configs/        RepChain 与反馈配置
vendor/vls/models/         MediaPipe 完整姿态模型
vendor/vls/schemas/        分析输出契约
```

运行数据默认写入 `VigorSync/webdata/offline/`，包括 `app.db` 和
`jobs/<job_id>/`。

## 启动

先安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r services\offline-worker\requirements.txt
```

通常由 `scripts\mirror-control.ps1` 或桌面外壳自动启动。手工启动：

```powershell
$env:PYTHONPATH = "services\offline-worker\src"
$env:VIGORYSYNC_SESSION_ROOT = "$PWD\webdata\sessions"
$env:VLS_WEBDATA_ROOT = "$PWD\webdata\offline"
.\.venv\Scripts\python.exe -m vigorsync_offline
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8030/api/v1/health
```

## 内部任务

```http
POST /internal/v1/jobs
X-Internal-Token: vigorsync-offline-dev
Content-Type: application/json
```

```json
{
  "job_id": "job_0123456789abcdef",
  "session_id": "s_0123456789abcdef01234567",
  "source_path": "E:\\project\\Exercise\\VigorSync\\webdata\\sessions\\s_x\\input\\source.mp4",
  "exercise": "pushup",
  "view": "side_right",
  "rotation": "none",
  "realtime_events_path": "E:\\project\\Exercise\\VigorSync\\webdata\\sessions\\s_x\\live\\events.jsonl"
}
```

同一 `job_id` 重复提交会返回已有任务。任务状态与结果仍使用 VLS 的
`/api/v1/jobs/{job_id}` 和 `/api/v1/jobs/{job_id}/result`，但这些接口只应供
Core 在本机调用。

结果中的 `realtime_review` 和产物 `realtime_review.json` 记录实时/离线匹配。
没有事件文件的旧任务仍按原有流程分析。
