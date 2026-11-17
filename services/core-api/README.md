# VigorSync Core API M3

Core API 管理手机配对、会话状态、Live Engine 命令、幂等请求和实时 WebSocket
广播，默认监听 `0.0.0.0:8011`，供同一局域网内的手机配对访问。

## 启动

```powershell
$env:PYTHONPATH = "services\core-api\src"
.\.venv\Scripts\python.exe -m vigorsync_core
```

Live Engine 需要设置：

```powershell
$env:VIGORSYNC_CORE_URL = "http://127.0.0.1:8011"
```

桌面外壳和 `mirror-control.ps1` 会自动启动并配置三服务。

## 已实现

- SQLite 会话、事件、配对 token 与幂等键。
- 配对码换取 Bearer token。
- 创建、启动、暂停、恢复、停止和取消会话。
- 手机可调用 `POST /api/v1/sessions/start` 一次完成创建和启动。
- 完整删除已结束会话及其本机产物。
- Live Engine 内部状态上报和 WebSocket 广播。
- 手机和镜端 WebSocket 快照/事件通道。
- 本机开发接口 `/dev/v1/*`，供 M2 镜端阶段联调。

停止训练后 Core 会创建离线 job，轮询本机 Offline Worker，并把分析阶段、进度、
摘要和报告地址同步给手机与镜端。

手机 WebSocket 使用 `Authorization: Bearer <pairing_token>` 握手，不把 token
放入 URL。镜端 WebSocket 使用 `vigorsync.v1` 和
`vigorsync.token.<mirror_token>` 子协议完成本地认证。

## 测试

```powershell
$env:PYTHONPATH = "services\core-api\src"
.\.venv\Scripts\python.exe -m unittest discover -s services\core-api\tests -v
```
