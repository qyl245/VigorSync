# VigorSync M4：TTS 与实时纠错方案

## 1. 文档目的

本文定义 VigorSync 下一阶段的实时纠错与本地 TTS 方案，并评估以下开源项目
可借鉴的部分：

- `VLS/ai-gym-coach-main`
- `VLS/ai-workout-corrector-main`
- `VLS/exercise-pose-analyser-main`
- `VLS/exercise-form-correction-main`
- `VLS/FormAI-main`

本阶段不把语音作为独立功能追加，而是把它视为实时纠错链路的输出层。顺序必须是：

```text
关节角度
  -> 单帧问题候选
  -> 时间窗聚合与冷却
  -> 提示优先级
  -> form.alert
  -> 镜端 UI + TTS
```

## 2. 当前基础与缺口

### 2.1 已具备

- Live Engine 已有 MediaPipe Pose、关节角度平滑、动作状态机和 rep 计数。
- `form.alert` 契约已经包含 `code / severity / message / confidence`。
- 镜端已能接收实时事件并展示训练状态。
- M3 离线链路已有问题分类、置信度、证据帧和报告卡。
- `rep.completed`、`form.alert` 和 `session.state` 已写入
  `events.jsonl` 并可推送手机和镜端。

### 2.2 当前缺口

- 实时状态机目前主要产生“深度不足”告警，问题类型较少。
- 还没有时间窗聚合，不能把单帧抖动过滤成稳定问题。
- 还没有 TTS 播放线程、消息队列、优先级和冷却策略。
- 实时告警码与离线问题码尚未完全共用同一套映射。
- 手机端不需要承担音频播放；第一版 TTS 应在镜端本机播放。

## 3. 开源项目评估

### 3.1 结论总览

| 项目 | 许可证 | 最值得参考 | 建议 |
| --- | --- | --- | --- |
| `exercise-form-correction-main` | MIT | 时间聚合、置信度、关节高亮、实时纠错流水线 | 最高优先级，可有条件复用 |
| `ai-gym-coach-main` | 未发现 LICENSE | TTS 队列、优先级、冷却、静音和语音生命周期 | 只借鉴设计，不复制代码 |
| `exercise-pose-analyser-main` | MIT | 深蹲和俯卧撑规则、配置化阈值 | 可参考或合规复用 |
| `FormAI-main` | 未发现 LICENSE | 检查动作质量后再计数、非阻塞提示 | 仅参考概念 |
| `ai-workout-corrector-main` | 未发现 LICENSE | 无有效运行实现，主体代码被注释 | 不采用 |

未提供许可证的仓库不能默认视为可复制代码。可以研究其行为、参数思路和架构，
但不应直接复制源码到 VigorSync。

### 3.2 exercise-form-correction

参考文件：

- `backend/pipeline/form_evaluator.py`
- `backend/pipeline/confidence.py`
- `backend/pipeline/features.py`
- `backend/exercises/squat.py`
- `backend/exercises/pushup.py`
- `backend/tests/test_form_evaluator.py`

主要价值：

- 单帧规则只产生 violation candidate，不直接触发用户提示。
- `ViolationAggregator` 默认使用最近 6 帧中至少 4 帧成立才输出问题。
- 同类问题有独立冷却窗口，避免连续刷屏。
- violation 包含 code、severity、joints、message 和 correction。
- 通过 `joint_colors` 将问题部位交给镜端高亮。
- 置信度按动作关键关节加权，不只看全身平均可见度。
- 前后视角与侧视角分别限制可评估的问题。
- 有较多单元测试和 WebSocket 回放测试。

建议吸收：

- M/N 时间窗聚合与冷却机制。
- 关节高亮和文字纠正同时输出。
- 每个动作只启用与该视角匹配的规则。
- 低置信度只显示 UI，不朗读。

不建议直接照搬：

- HMM 自动动作分类。
- 瑜伽模式。
- 远程 Render 部署和上传接口。
- 整套前端 demo。

### 3.3 ai-gym-coach

参考文件：

- `pose_coach.py` 中的 `FAULT_MSGS`、`FeedbackEngine` 和 `Voice`
- `ios/GymCoach/Sources/SpeechCoach.swift`
- `docs/COACH.md`

主要价值：

- 每个 fault 同时定义优先级、标准短句和评分惩罚。
- TTS 在后台线程中运行，不阻塞 Pose 推理。
- 队列积压时普通纠错提示会被丢弃，不会无限排队。
- 相同提示有冷却时间，消息不变时不重复朗读。
- 支持静音、取消当前语音和队列清空。
- 普通纠错与计数里程碑使用不同优先级。
- iOS 端使用 `AVSpeechSynthesizer`，并支持与背景音乐混音和压低音量。

建议吸收：

- 有界语音队列。
- 单条普通提示策略。
- 高严重度问题优先于节奏提示和计数里程碑。
- 暂停、停止、静音时立即清空队列。
- TTS 初始化或播放失败不能影响计数、录像和报告。

因本地源码未发现 LICENSE，以上只作为设计参考，不直接复制实现。

### 3.4 exercise-pose-analyser

参考文件：

- `src/exercises/squat.py`
- `src/exercises/pushup.py`
- `config.yaml`

主要价值：

- MIT 许可证，复用边界清楚。
- 深蹲和俯卧撑规则简单、容易理解。
- 深蹲包含膝内扣、膝盖过宽、前倾和脚部角度。
- 俯卧撑包含塌腰和深度不足。
- 阈值集中在 YAML 中，便于后续标定。

限制：

- 主要依赖单帧规则，缺少稳定时间聚合。
- 直接接 TTS 容易反复播报瞬时误判。
- 规则适合作为候选来源，不应直接作为最终语音触发条件。

### 3.5 FormAI

主要价值：

- 动作检查先于 rep 计数。
- `good_form` 决定当前 rep 是否计入有效次数。
- 普通提示按消息去重；里程碑提示可以打断普通提示。
- 非阻塞 TTS 的思路清晰。

限制：

- TTS 使用 macOS `say`，不能用于 Windows 或 Raspberry Pi。
- 纠错主要为无状态单帧判断。
- 未发现许可证文件，不适合直接复制代码。

可用于确认产品原则：

- 计数与动作质量是两个概念。
- 语音提示必须去重，里程碑与普通纠错需要分级。

### 3.6 ai-workout-corrector

`app.py` 的主体功能代码目前全部被注释，页面只显示调试文本，因此不具备参考或
集成价值。README 中的功能描述不能视为可运行实现。

## 4. 目标架构

### 4.1 Live Engine 内部模块

```text
MediaPipe Pose
  -> AngleObservation
  -> Rep State Machine
  -> FormFaultDetector      单帧候选，不直接播报
  -> FaultAggregator        M/N 窗口 + 冷却
  -> CuePolicy              优先级 + 阶段门 + 限频
  -> form.alert             UI / Core / 事件日志
  -> SpeechQueue            有界异步队列
  -> SpeechBackend          SAPI / Piper / Silent
```

建议新增模块：

```text
services/live-engine/src/vigorsync_live/
├── form_rules.py          动作规则与 fault 定义
├── form_aggregator.py     时间窗聚合、冷却
├── cue_policy.py          提示优先级与语音门控
└── speech.py              TTS 抽象、队列和本地后端
```

### 4.2 线程与性能边界

- Pose 推理和 TTS 必须运行在不同线程。
- Pose 线程只向有界队列提交提示，不做音频初始化或网络请求。
- 队列满时丢弃低优先级普通提示，保留高严重度提示。
- TTS 正常运行时，Pose 循环新增开销目标低于 5 ms p95。
- TTS 失败时自动切换为 silent backend，训练继续。

### 4.3 各端职责

- Live Engine：检测、聚合、告警、TTS 播放。
- 镜端：接收 `form.alert`，突出问题关节并显示文字。
- Core：记录告警事件，向手机广播摘要。
- 手机：显示问题状态，不承担第一版音频播放。
- Offline Worker：使用同一问题 taxonomy 做赛后详细复盘。

## 5. 第一版纠错规则

### 5.1 深蹲

| 问题码 | 条件方向 | 建议短句 | 初始优先级 |
| --- | --- | --- | --- |
| `squat_depth_insufficient` | rep 底端角度高于深度阈值 | 再低一点 | 1 |
| `squat_knee_valgus` | 膝相对踝向身体中线偏移 | 膝盖向外 | 0 |
| `squat_torso_lean_excess` | 侧视角躯干偏离垂直线过大 | 胸口抬起 | 1 |
| `squat_asymmetry` | 左右膝角度差持续过大 | 两侧用力均匀 | 2 |

实施顺序：

1. 保留当前深度不足规则。
2. 增加膝内扣。
3. 增加侧视前倾。
4. 左右不对称先只显示，不进入第一版 TTS。

### 5.2 俯卧撑

| 问题码 | 条件方向 | 建议短句 | 初始优先级 |
| --- | --- | --- | --- |
| `pushup_depth_insufficient` | rep 底端肘角高于深度阈值 | 再低一点 | 1 |
| `pushup_hip_sag` | 髋明显低于肩踝连线 | 收紧核心 | 0 |
| `pushup_hip_pike` | 髋明显高于肩踝连线 | 髋部放低 | 1 |
| `pushup_elbow_flare` | 上臂相对躯干外展过大 | 手肘内收 | 0 |
| `pushup_asymmetry` | 左右肘角差持续过大 | 两侧用力均匀 | 2 |

侧视身体线使用髋部相对肩踝连线的投影垂直偏移，并按身体线像素长度归一化。
当前 `pushup_hip_sag` 阈值为 `0.065`，`pushup_hip_pike` 阈值为 `-0.08`；
单帧候选仍需经过默认 `4/6` 时间窗聚合才输出提示。

所有位置规则优先使用髋部原点、躯干长度归一化坐标，避免摄像头距离变化直接改变
阈值。不能长期依赖原始像素坐标或固定 0.05 图像距离。

### 5.3 视角门控

- 正面深蹲：膝内扣、左右膝不对称。
- 侧面深蹲：深度、躯干前倾。
- 侧面俯卧撑：深度、塌腰、臀部过高、手肘外张。
- 正面或背面俯卧撑：手肘外张和左右不对称；手肘外张在侧视角不启用。
- 双人或多姿态输入不进入实时纠错，保持第一版单用户约束。

## 6. 问题聚合与提示策略

### 6.1 M/N 聚合

每个问题码维护最近 `N` 帧状态，至少 `M` 帧成立才进入提示候选。

建议默认值：

```text
M = 4
N = 6
```

如果对应关节可见度低于动作要求，当前帧既不算成立，也不应打破已有的短暂成立窗口；
窗口应记录为“不可评估”，防止 Pose 抖动造成虚假通过或虚假告警。

### 6.2 冷却

- 同一问题码：首次触发后冷却 4–6 秒。
- 全局普通语音：两次之间至少间隔 2.5–3 秒。
- 高严重度问题可以缩短全局间隔，但同码仍不得连续刷屏。
- UI 高亮可以比语音更早出现和更晚消失。

### 6.3 优先级

建议优先级从高到低：

1. 安全与严重姿态风险。
2. 膝内扣、塌腰、手肘严重外张。
3. 深度不足、前倾。
4. 节奏和不一致。
5. rep 数量里程碑和组结束。

普通 rep 数不需要逐个朗读。建议只播报：

- 集合开始。
- 达到目标次数。
- 每 5 或 10 次的里程碑。
- 集合结束。
- 新记录或疲劳提示。

### 6.4 置信度

- `confidence >= 0.75`：可显示并进入语音候选。
- `0.55 <= confidence < 0.75`：只显示，不朗读。
- `confidence < 0.55`：不显示问题结论，只提示调整机位或光线。

### 6.5 生命周期

- 会话暂停：清空普通语音队列。
- 会话停止：立即静音并结束播放线程。
- 切换动作：重置聚合窗口、冷却和待播消息。
- 用户静音：保留 UI 告警，停止所有语音。

## 7. TTS 后端

### 7.1 统一接口

```python
class SpeechBackend(Protocol):
    def speak(self, text: str) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...
```

第一版后端：

- `Pyttsx3SpeechBackend`：Windows 开发机使用 SAPI。
- `SilentSpeechBackend`：TTS 不可用或用户静音。

Pi 阶段后端：

- `PiperSpeechBackend`：离线、本地、中文音质优于 espeak。

### 7.2 为什么不直接用浏览器 Web Speech

- 镜端 App 的 WebView/浏览器实现存在平台差异。
- 浏览器语音可能依赖网络或系统语音包。
- 难以保证延迟、取消、静音和启动时行为一致。
- TTS 属于 Live Engine 状态机输出，放在 Python 服务内更容易测试和恢复。

### 7.3 语音内容

- 普通纠错保持 4–10 个汉字。
- 一次最多一句，不拼接多个问题。
- 不使用羞辱、医学诊断或绝对化表达。
- 提示消失后不重复播报“已恢复”，除非进入新阶段。

## 8. 与 M3 报告的协作

实时与离线必须使用同一问题 taxonomy：

- 相同 `issue_code`。
- 相同严重度语义。
- 相同的标准短句和详细解释映射。
- 实时使用短句，离线报告使用完整原因、建议和证据帧。

第一版允许实时只覆盖高频、稳定问题，离线覆盖更多实验性指标，但两边不应给同一个
问题使用不同名称。

建议新增共享配置：

```text
configs/issues/
├── squat.json
└── pushup.json
```

每个问题包含：

```json
{
  "code": "pushup_hip_sag",
  "severity": "high",
  "joints": ["left_hip", "right_hip"],
  "short_cue": "收紧核心",
  "detail": "髋部低于肩踝连线",
  "correction": "收紧核心并抬起髋部",
  "cooldown_s": 5
}
```

## 9. 分阶段实施

### 9.1 M4.1：实时纠错基础

- 新增 form rules 和聚合器。
- 深蹲支持深度、膝内扣和侧视前倾。
- 俯卧撑支持深度、塌腰、臀部过高和手肘外张。
- 将稳定问题输出为现有 `form.alert`。
- 镜端显示文字并高亮问题关节。

实现记录（2026-11-17）：

- `form_rules.py` 提供逐帧候选规则。
- `form_aggregator.py` 提供 `4/6` 时间窗、4 秒冷却和 1.5 秒高亮保持。
- `form.alert` 新增 `joints` 字段。
- Live Engine MJPEG 会在骨骼图上直接高亮问题关节。
- 镜端告警卡按 low/medium/high 分级显示。
- 固定视频回放脚本复用同一套规则和聚合器。
- Live Engine 当前共 25 项测试通过。

验收：

- 交替单帧误判不触发告警。
- 持续问题在 M/N 窗口后只触发一次。
- 冷却期间不重复触发。
- 不影响现有 rep 计数回归。

### 9.2 M4.2：Windows 本地 TTS

- 增加 `SpeechQueue`、`CuePolicy` 和 pyttsx3/SAPI 后端。
- 支持静音、停止、会话结束清理。
- 普通问题 3–5 秒内最多播报一次。
- 里程碑只在 5/10 次数或目标达成时播报。

验收：

- TTS 不阻塞 Pose 线程。
- 队列上限固定，积压时丢弃低优先级提示。
- TTS 初始化失败时训练和计数继续。
- 静音后没有残留语音。

### 9.3 M4.3：Pi 离线语音

- 增加 Piper backend。
- 中文短句资源随应用锁定版本。
- 启动时检查音频设备和语音资源。
- 与服务自启动、停止和异常恢复整合。

验收：

- 无网络可用。
- 冷启动后首次播报延迟可接受。
- TTS 与 Pose 并行时仍满足性能预算。

### 9.4 M4.4：离线一致性

- 实时与离线共用 issue code 和文案配置。
- 报告问题卡引用实时出现过的同一问题。
- 增加实时/离线问题码一致性测试。

## 10. 测试与指标

### 10.1 单元测试

- 交替成立不触发。
- M/N 达标触发一次。
- 冷却抑制重复。
- 低置信度不进入语音。
- 静音和暂停清空队列。
- 队列满时只保留高优先级消息。
- TTS backend 异常不抛出到 Pose 循环。

### 10.2 固定视频回放

- 深蹲正面和侧面。
- 俯卧撑侧面左右机位。
- 含起身、走近摄像头和停止录制的尾段。
- 每个规则记录首次触发、重复触发次数和误报位置。

### 10.3 验收指标

| 指标 | 目标 |
| --- | --- |
| 稳定问题重复播报 | 同一问题 5 秒内最多 1 次 |
| 普通提示全局间隔 | 不少于 2.5 秒 |
| TTS 对 Pose FPS 影响 | 无可见下降，新增 p95 开销 < 5 ms |
| TTS 故障 | 不终止训练、不丢录像和事件 |
| 告警码一致性 | 实时/离线共用配置验证通过 |
| 误报控制 | 固定回放中瞬时单帧误判不输出 |

## 11. 风险与控制

| 风险 | 影响 | 控制 |
| --- | --- | --- |
| 规则过密 | 用户被语音持续打断 | M/N、冷却、单条普通提示 |
| 机位变化 | 归一化阈值失真 | 髋相对/躯干归一化、视角门控 |
| 低置信 Pose | 错误纠正 | 置信度和关节权重门控，低置信只显示 |
| TTS 阻塞 | 掉帧、录像不稳定 | 独立线程、有界队列、silent fallback |
| Pi 中文音质差 | 听感不可接受 | 使用 Piper，不在 Pi 依赖 espeak 作为正式方案 |
| 实时与离线不一致 | 同一问题两种结论 | 共用 issue taxonomy 和标准文案 |

## 12. 本阶段不做

- 不接 LLM 教练。
- 不做语音识别和开放麦克风对话。
- 不做多人识别。
- 不在手机上远程串流镜端 TTS 音频。
- 不用 TTS 朗读每一个 rep。
- 不复制未授权仓库的实现代码。

## 13. 最终建议

1. 以 `exercise-form-correction` 的聚合与置信度设计为第一参考。
2. 以 `ai-gym-coach` 的 TTS 队列、优先级和冷却为第二参考。
3. 以 `exercise-pose-analyser` 的 MIT 规则作为阈值补充。
4. `FormAI` 只保留“质量先行、语音分级”的产品原则。
5. 不采用 `ai-workout-corrector`。
6. 第一版先完成深蹲和俯卧撑的少量高频问题，不做大而全的自动纠错。
