const query = new URLSearchParams(window.location.search);
const CORE_URL = (query.get("core") || "http://127.0.0.1:8011").replace(/\/$/, "");
const THEME_COLORS = {
  dark: "#0b0d0c",
  light: "#f3f1e8",
};

const elements = {
  homePanel: document.querySelector("#home-panel"),
  homeOpenSetup: document.querySelector("#home-open-setup"),
  setupBackHome: document.querySelector("#setup-back-home"),
  setupPanel: document.querySelector("#setup-panel"),
  trainingPanel: document.querySelector("#training-panel"),
  summaryPanel: document.querySelector("#summary-panel"),
  startForm: document.querySelector("#start-form"),
  startButton: document.querySelector("#start-button"),
  startLabel: document.querySelector("#start-label"),
  setupError: document.querySelector("#setup-error"),
  exercise: document.querySelector("#exercise"),
  view: document.querySelector("#view"),
  targetReps: document.querySelector("#target-reps"),
  stream: document.querySelector("#stream"),
  exerciseName: document.querySelector("#exercise-name"),
  connectionBadge: document.querySelector("#connection-badge"),
  elapsedTime: document.querySelector("#elapsed-time"),
  repCount: document.querySelector("#rep-count"),
  repCluster: document.querySelector(".rep-cluster"),
  targetProgressText: document.querySelector("#target-progress-text"),
  targetProgress: document.querySelector("#target-progress"),
  jointName: document.querySelector("#joint-name"),
  angleValue: document.querySelector("#angle-value"),
  angleMarker: document.querySelector("#angle-marker"),
  phaseLabel: document.querySelector("#phase-label"),
  confidenceLabel: document.querySelector("#confidence-label"),
  fpsLabel: document.querySelector("#fps-label"),
  alert: document.querySelector("#alert"),
  alertKicker: document.querySelector("#alert-kicker"),
  alertMessage: document.querySelector("#alert-message"),
  speechButton: document.querySelector("#speech-button"),
  confirmDialog: document.querySelector("#confirm-dialog"),
  confirmTitle: document.querySelector("#confirm-title"),
  confirmMessage: document.querySelector("#confirm-message"),
  confirmSafe: document.querySelector("#confirm-safe"),
  confirmDanger: document.querySelector("#confirm-danger"),
  prepareButton: document.querySelector("#prepare-button"),
  cancelButton: document.querySelector("#cancel-button"),
  pauseButton: document.querySelector("#pause-button"),
  stopButton: document.querySelector("#stop-button"),
  summaryReps: document.querySelector("#summary-reps"),
  summaryDuration: document.querySelector("#summary-duration"),
  summaryConfidence: document.querySelector("#summary-confidence"),
  summaryFps: document.querySelector("#summary-fps"),
  summaryPath: document.querySelector("#summary-path"),
  analysisStatus: document.querySelector("#analysis-status"),
  analysisStatusText: document.querySelector("#analysis-status-text"),
  analysisProgressBar: document.querySelector("#analysis-progress-bar"),
  analysisFinding: document.querySelector("#analysis-finding"),
  analysisQuality: document.querySelector("#analysis-quality"),
  analysisIssue: document.querySelector("#analysis-issue"),
  openReportButton: document.querySelector("#open-report-button"),
  latestReport: document.querySelector("#latest-report"),
  latestReportLink: document.querySelector("#latest-report-link"),
  restartButton: document.querySelector("#restart-button"),
};

const exerciseNames = {
  squat: "深蹲",
  pushup: "俯卧撑",
};

const phaseNames = {
  up: "起始",
  down: "底部",
  transition: "移动中",
  unknown: "等待姿态",
};

const analysisStageNames = {
  disabled: "离线分析已关闭",
};

let currentSession = null;
let websocket = null;
let mirrorSocket = null;
let mirrorToken = null;
let reconnectTimer = null;
let mirrorReconnectTimer = null;
let alertTimer = null;
let confirmResolver = null;
let syncingSession = false;
let summarySessionId = null;
let lastMetrics = {
  reps: 0,
  confidence: 0,
  fps: 0,
  phase: "unknown",
  paused: false,
  joint_angle: null,
  elapsed_s: 0,
};

const jointNames = {
  squat: "膝关节角度",
  pushup: "肘关节角度",
};

async function request(path, options = {}) {
  const response = await fetch(`${CORE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const body = await response.json();
      message = body.error?.message || body.detail || message;
    } catch {
      // Keep the HTTP status message.
    }
    throw new Error(message);
  }
  return response.json();
}

function liveOrigin() {
  if (!currentSession?.ws_url) return null;
  try {
    const url = new URL(currentSession.ws_url);
    url.protocol = url.protocol === "wss:" ? "https:" : "http:";
    return url.origin;
  } catch {
    return null;
  }
}

async function liveRequest(path, options = {}) {
  const origin = liveOrigin();
  if (!origin) throw new Error("实时服务地址不可用");
  const response = await fetch(`${origin}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let message = `语音服务请求失败 (${response.status})`;
    try {
      const body = await response.json();
      message = body.error?.message || body.detail || message;
    } catch {
      // Keep the HTTP status message.
    }
    throw new Error(message);
  }
  return response.json();
}

function updateSpeechStatus(status) {
  const available = Boolean(status) && status.available !== false;
  const enabled = available && status?.enabled !== false;
  elements.speechButton.disabled = !available || !currentSession?.session_id;
  elements.speechButton.dataset.enabled = String(enabled);
  elements.speechButton.setAttribute("aria-pressed", String(enabled));
  elements.speechButton.textContent = !available
    ? "语音不可用"
    : enabled
      ? "语音开启"
      : "语音已静音";
}

async function refreshSpeechStatus() {
  if (!currentSession?.session_id) return;
  const status = await liveRequest(
    `/dev/v1/sessions/${currentSession.session_id}/speech`,
  );
  updateSpeechStatus(status);
}

async function toggleSpeech() {
  if (!currentSession?.session_id || elements.speechButton.disabled) return;
  elements.speechButton.disabled = true;
  try {
    const enabled = elements.speechButton.dataset.enabled !== "true";
    const status = await liveRequest(
      `/dev/v1/sessions/${currentSession.session_id}/speech`,
      {
        method: "POST",
        body: JSON.stringify({ enabled }),
      },
    );
    updateSpeechStatus(status);
  } catch (error) {
    showAlert(error.message);
    refreshSpeechStatus().catch(() => updateSpeechStatus(null));
  }
}

async function ensureBootstrap() {
  if (mirrorToken) return;
  const bootstrap = await request("/dev/v1/bootstrap");
  mirrorToken = bootstrap.mirror_token;
}

function applyMirrorTheme(mode) {
  if (mode !== "light" && mode !== "dark") return;
  document.documentElement.dataset.theme = mode;
  document.documentElement.style.colorScheme = mode;
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  if (themeMeta) themeMeta.content = THEME_COLORS[mode];
  try {
    localStorage.setItem("vigorsync.mirror.theme", mode);
  } catch {
    // Theme still applies for this page when local storage is unavailable.
  }
}

function setLoading(loading, text = "正在打开摄像头…") {
  elements.startButton.disabled = loading;
  elements.startLabel.textContent = loading ? text : "启动实时捕捉";
}

function showSetupError(message) {
  elements.setupError.textContent = message;
}

function formatDuration(seconds) {
  const totalSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(totalSeconds / 60);
  const remainder = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function updateMetrics(metrics) {
  if (!metrics) return;
  lastMetrics = { ...lastMetrics, ...metrics };
  const reps = Number(lastMetrics.reps || 0);
  const target = currentSession?.target_reps ?? Number(elements.targetReps.value);
  elements.repCount.textContent = String(reps);
  elements.targetProgressText.textContent = `${reps} / ${target}`;
  elements.targetProgress.style.width = `${Math.min(100, (reps / Math.max(1, target)) * 100)}%`;
  elements.repCluster.classList.toggle("target-hit", reps >= target);
  elements.elapsedTime.textContent = formatDuration(lastMetrics.elapsed_s);
  elements.phaseLabel.textContent = lastMetrics.paused
    ? "已暂停"
    : phaseNames[lastMetrics.phase] || lastMetrics.phase || "等待姿态";

  const jointAngle = Number(lastMetrics.joint_angle);
  const hasAngle = lastMetrics.joint_angle !== null && Number.isFinite(jointAngle);
  elements.angleValue.textContent = hasAngle ? String(Math.round(jointAngle)) : "--";
  elements.angleMarker.style.width = hasAngle
    ? `${Math.max(0, Math.min(100, (jointAngle / 180) * 100))}%`
    : "0%";

  const confidence = Number(lastMetrics.confidence || 0);
  const fps = Number(lastMetrics.fps || 0);
  elements.confidenceLabel.textContent = confidence
    ? `置信度 ${Math.round(confidence * 100)}%`
    : "置信度 --";
  elements.fpsLabel.textContent = fps ? `推理 ${fps.toFixed(1)} FPS` : "推理 -- FPS";
}

function showAlert(input) {
  const payload =
    typeof input === "string"
      ? { message: input, severity: "medium" }
      : input || {};
  if (!payload.message) return;
  const severity = ["low", "medium", "high"].includes(payload.severity)
    ? payload.severity
    : "medium";
  elements.alert.dataset.severity = severity;
  elements.alertKicker.textContent =
    severity === "high"
      ? "FORM CORRECTION · 高优先级"
      : severity === "low"
        ? "FORM CORRECTION · 提示"
        : "FORM CORRECTION";
  elements.alertMessage.textContent = payload.message;
  elements.alert.hidden = false;
  window.clearTimeout(alertTimer);
  alertTimer = window.setTimeout(() => {
    elements.alert.hidden = true;
  }, 3200);
}

function settleConfirmation(confirmed) {
  const resolve = confirmResolver;
  confirmResolver = null;
  if (elements.confirmDialog.open) elements.confirmDialog.close();
  if (resolve) resolve(confirmed);
}

function requestConfirmation({ title, message, safeLabel, confirmLabel }) {
  if (confirmResolver) settleConfirmation(false);
  elements.confirmTitle.textContent = title;
  elements.confirmMessage.textContent = message;
  elements.confirmSafe.textContent = safeLabel;
  elements.confirmDanger.textContent = confirmLabel;
  elements.confirmDanger.disabled = false;
  return new Promise((resolve) => {
    confirmResolver = resolve;
    elements.confirmDialog.showModal();
    elements.confirmSafe.focus();
  });
}


function setConnection(text, online = false) {
  elements.connectionBadge.textContent = text;
  elements.connectionBadge.classList.toggle("online", online);
}

function applyDraftConfig(config) {
  if (!config) return;
  if (["squat", "pushup"].includes(config.exercise)) {
    elements.exercise.value = config.exercise;
  }
  if (
    [
      "front",
      "rear",
      "oblique_left",
      "oblique_right",
      "side_left",
      "side_right",
      "unknown",
    ].includes(config.view)
  ) {
    elements.view.value = config.view;
  }
  const target = Number(config.target_reps);
  if (Number.isInteger(target) && target >= 1 && target <= 500) {
    elements.targetReps.value = String(target);
  }
}

function isLiveStatus(status) {
  return ["created", "preparing", "active", "paused"].includes(status);
}

function updateStopButton(status) {
  elements.stopButton.disabled = status === "stopping";
  elements.stopButton.textContent =
    status === "stopping" ? "正在结束…" : "结束训练";
}

function showHome() {
  elements.homePanel.hidden = false;
  elements.setupPanel.hidden = true;
  elements.summaryPanel.hidden = true;
  elements.trainingPanel.hidden = true;
}

function showSetup() {
  elements.homePanel.hidden = true;
  elements.setupPanel.hidden = false;
  elements.summaryPanel.hidden = true;
  elements.trainingPanel.hidden = true;
}

function handleMirrorPage(page) {
  if (currentSession && isLiveStatus(currentSession.status)) return;
  if (page === "training") {
    showSetup();
  } else {
    showHome();
  }
}

function dispatchHomeEvent(type, payload) {
  window.dispatchEvent(
    new CustomEvent(`vigorsync:${type}`, { detail: payload }),
  );
}

function resetMetrics() {
  lastMetrics = {
    reps: 0,
    confidence: 0,
    fps: 0,
    phase: "unknown",
    paused: false,
    joint_angle: null,
    elapsed_s: 0,
  };
}

function closeLiveSocket() {
  window.clearTimeout(reconnectTimer);
  reconnectTimer = null;
  if (!websocket) return;
  const closing = websocket;
  websocket = null;
  closing.close();
}

function connectLiveSocket() {
  if (!currentSession?.ws_url || !isLiveStatus(currentSession.status)) return;
  if (
    websocket &&
    (websocket.readyState === WebSocket.OPEN ||
      websocket.readyState === WebSocket.CONNECTING) &&
    websocket.url === currentSession.ws_url
  ) {
    return;
  }
  closeLiveSocket();
  const socket = new WebSocket(currentSession.ws_url);
  websocket = socket;
  socket.addEventListener("open", () => {
    if (websocket === socket) setConnection("实时连接", true);
  });
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.session_id && message.session_id !== currentSession?.session_id) {
      return;
    }
    if (message.type === "live.metrics") {
      updateMetrics(message.payload);
    } else if (message.type === "form.alert") {
      showAlert(message.payload);
    } else if (message.type === "session.state") {
      handleSessionState(message.session_id, message.payload);
    }
  });
  socket.addEventListener("close", () => {
    if (websocket !== socket) return;
    websocket = null;
    setConnection("连接中断");
    if (currentSession && isLiveStatus(currentSession.status)) {
      reconnectTimer = window.setTimeout(connectLiveSocket, 1200);
    }
  });
}

function connectMirrorSocket() {
  if (!mirrorToken) return;
  if (
    mirrorSocket &&
    (mirrorSocket.readyState === WebSocket.OPEN ||
      mirrorSocket.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }
  window.clearTimeout(mirrorReconnectTimer);
  const wsUrl = CORE_URL.replace(/^http/, "ws") + "/api/v1/ws/mirror";
  const socket = new WebSocket(wsUrl, [
    "vigorsync.v1",
    `vigorsync.token.${mirrorToken}`,
  ]);
  mirrorSocket = socket;
  socket.addEventListener("open", () => {
    syncCurrentSession().catch(() => {});
  });
  socket.addEventListener("message", (event) => {
    handleMirrorMessage(JSON.parse(event.data));
  });
  socket.addEventListener("close", () => {
    if (mirrorSocket !== socket) return;
    mirrorSocket = null;
    mirrorReconnectTimer = window.setTimeout(connectMirrorSocket, 1200);
  });
}

function handleMirrorMessage(message) {
  if (message.type === "mirror.theme") {
    applyMirrorTheme(message.payload?.mode);
    return;
  }
  if (message.type === "mirror.page") {
    handleMirrorPage(message.payload?.page);
    return;
  }
  if (
    message.type === "mirror.layout" ||
    message.type === "pairing.state" ||
    message.type === "todo.updated" ||
    message.type === "weekly_plan.updated"
  ) {
    dispatchHomeEvent(message.type, message.payload);
    return;
  }
  if (message.type === "session.config") {
    applyDraftConfig(message.payload);
    return;
  }
  if (
    message.type === "session.dismissed" &&
    currentSession?.session_id === message.session_id
  ) {
    restart();
    return;
  }
  if (
    message.type === "session.deleted" &&
    currentSession?.session_id === message.session_id
  ) {
    restart();
    return;
  }
  if (message.type === "report.ready") {
    applyReportReady(message);
    return;
  }
  if (message.type === "session.start" && message.session_id) {
    const session = {
      status: "preparing",
      ...message.payload,
      session_id: message.session_id,
    };
    attachSession(session).catch((error) => {
      setConnection("启动异常");
      showAlert(error.message);
    });
    return;
  }
  if (
    !currentSession ||
    !message.session_id ||
    message.session_id !== currentSession.session_id
  ) {
    return;
  }
  if (message.type === "live.metrics") {
    updateMetrics(message.payload);
  } else if (message.type === "form.alert") {
    showAlert(message.payload);
  } else if (message.type === "session.state") {
    handleSessionState(message.session_id, message.payload);
  } else if (message.type === "report.progress") {
    updateAnalysisProgress(message.payload);
  }
}

function updateTrainingControls() {
  const status = currentSession?.status;
  const preparing = ["created", "preparing"].includes(status);
  const training = ["active", "paused"].includes(status);
  elements.prepareButton.hidden = !preparing;
  elements.cancelButton.hidden = !preparing;
  elements.pauseButton.hidden = !training;
  elements.stopButton.hidden = !training;
}

function handleSessionState(sessionId, payload) {
  if (!currentSession || currentSession.session_id !== sessionId) return;
  const status = payload?.status;
  if (!status) return;
  currentSession = { ...currentSession, status };
  updateTrainingControls();
  if (status === "paused") {
    elements.pauseButton.textContent = "继续";
    elements.phaseLabel.textContent = "已暂停";
  } else if (status === "active") {
    elements.pauseButton.textContent = "暂停";
    updateStopButton(status);
    setConnection("实时连接", true);
  } else if (["created", "preparing"].includes(status)) {
    updateStopButton(status);
  } else if (status === "stopping") {
    updateStopButton(status);
  } else if (status === "failed") {
    setConnection("发生错误");
    showSummary(sessionId).catch((error) => {
      restart();
      showSetupError(error.message || "训练发生错误，请重新开始。");
    });
  } else if (status === "cancelled") {
    restart();
    showHome();
  } else if (["analyzing", "completed"].includes(status)) {
    showSummary(sessionId).catch((error) => {
      setConnection("会话已结束");
      showAlert(error.message);
    });
  }
}

function updateAnalysisProgress(payload) {
  if (!payload) return;
  const progress = Math.max(0, Math.min(1, Number(payload.progress) || 0));
  elements.analysisStatusText.textContent =
    `${analysisStageNames[payload.stage] || payload.stage || "queued"} · ${Math.round(progress * 100)}%`;
  elements.analysisProgressBar.style.width = `${progress * 100}%`;
}

function applyReportReady(message) {
  const summary = message.summary || {};
  const reportUrl = message.report_url;
  if (!reportUrl) return;
  const absoluteUrl = new URL(reportUrl, `${CORE_URL}/`).href;
  elements.openReportButton.href = absoluteUrl;
  elements.openReportButton.hidden = false;
  elements.latestReportLink.href = absoluteUrl;
  elements.latestReport.hidden = false;
  elements.analysisStatusText.textContent = "已完成";
  elements.analysisProgressBar.style.width = "100%";
  elements.analysisFinding.hidden = false;
  if (currentSession?.session_id === message.session_id) {
    elements.summaryReps.textContent = String(summary.rep_count ?? 0);
  }
  elements.analysisQuality.textContent =
    `离线计数 ${summary.rep_count ?? 0} · 质量 ${summary.quality_status || "unknown"}`;
  const firstIssue = (summary.issues || [])[0];
  elements.analysisIssue.textContent =
    firstIssue?.title || "本次未发现可靠问题";
}

async function waitUntilReady(sessionId) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (currentSession?.session_id !== sessionId) return;
    const session = await request(`/dev/v1/sessions/${sessionId}`);
    if (currentSession?.session_id !== sessionId) return;
    currentSession = { ...currentSession, ...session };
    updateTrainingControls();
    if (session.status === "active") {
      setConnection("实时连接", true);
      return;
    }
    if (session.status === "paused") {
      elements.pauseButton.textContent = "继续";
      return;
    }
    if (session.status === "failed") {
      throw new Error(session.error?.message || "摄像头或姿态模型启动失败");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 250));
  }
  throw new Error("启动超时，请检查摄像头是否被其他程序占用");
}

async function attachSession(session, { waitForReady = true } = {}) {
  if (!session?.session_id) return;
  const changedSession = currentSession?.session_id !== session.session_id;
  const enteringTraining = changedSession || elements.trainingPanel.hidden;
  if (changedSession) {
    closeLiveSocket();
    resetMetrics();
    summarySessionId = null;
    currentSession = session;
  } else {
    currentSession = { ...currentSession, ...session };
  }

  elements.homePanel.hidden = true;
  elements.setupPanel.hidden = true;
  elements.summaryPanel.hidden = true;
  elements.trainingPanel.hidden = false;
  elements.exerciseName.textContent =
    exerciseNames[currentSession.exercise] || currentSession.exercise;
  elements.jointName.textContent =
    jointNames[currentSession.exercise] || "关节角度";
  applyDraftConfig(currentSession);
  if (enteringTraining) {
    elements.targetProgressText.textContent =
      `0 / ${currentSession.target_reps}`;
    elements.repCount.textContent = "0";
    elements.elapsedTime.textContent = "00:00";
    elements.angleValue.textContent = "--";
    elements.angleMarker.style.width = "0%";
    elements.phaseLabel.textContent =
      currentSession.status === "paused" ? "已暂停" : "准备相机";
  }
  elements.pauseButton.textContent =
    currentSession.status === "paused" ? "继续" : "暂停";
  updateTrainingControls();
  updateStopButton(currentSession.status);
  elements.repCluster.classList.remove("target-hit");
  refreshSpeechStatus().catch(() => updateSpeechStatus(null));

  if (elements.stream.dataset.sessionId !== currentSession.session_id) {
    if (currentSession.stream_url) {
      elements.stream.dataset.sessionId = currentSession.session_id;
      elements.stream.src = `${currentSession.stream_url}?t=${Date.now()}`;
    }
  }
  if (currentSession.status === "active") {
    setConnection("实时连接", true);
  } else {
    setConnection("准备相机");
  }
  connectLiveSocket();
  if (waitForReady && currentSession.status === "preparing") {
    await waitUntilReady(currentSession.session_id);
  }
}

async function syncCurrentSession() {
  if (!mirrorToken || syncingSession) return;
  syncingSession = true;
  try {
    const response = await request("/dev/v1/sessions");
    const active = response.items?.find((item) => isLiveStatus(item.status));
    if (active) {
      const detail = await request(`/dev/v1/sessions/${active.session_id}`);
      await attachSession(detail);
      return;
    }
    if (!currentSession) {
      const latestReport = response.items?.find((item) => item.report_url);
      if (latestReport) {
        const detail = await request(
          `/dev/v1/sessions/${latestReport.session_id}`,
        );
        applyReportReady({
          session_id: detail.session_id,
          report_url: detail.report_url,
          summary: detail.analysis?.summary || {},
        });
      }
    }
    if (currentSession && isLiveStatus(currentSession.status)) {
      const detail = await request(
        `/dev/v1/sessions/${currentSession.session_id}`,
      );
      if (!isLiveStatus(detail.status)) {
        await showSummary(detail.session_id, detail);
      }
    }
  } finally {
    syncingSession = false;
  }
}

async function startSession(event) {
  event.preventDefault();
  showSetupError("");
  setLoading(true);
  try {
    await ensureBootstrap();
    const target = Math.max(1, Number(elements.targetReps.value) || 12);
    const session = await request("/dev/v1/sessions", {
      method: "POST",
      body: JSON.stringify({
        exercise: elements.exercise.value,
        view: elements.view.value,
        target_reps: target,
      }),
    });
    await attachSession(session);
  } catch (error) {
    showSetupError(error.message);
    if (!currentSession) {
      elements.trainingPanel.hidden = true;
      elements.setupPanel.hidden = false;
    }
  } finally {
    setLoading(false);
  }
}

async function togglePause() {
  if (
    !currentSession ||
    !["active", "paused"].includes(currentSession.status)
  ) {
    return;
  }
  const paused = currentSession.status === "paused";
  const action = paused ? "resume" : "pause";
  elements.pauseButton.disabled = true;
  try {
    const session = await request(
      `/dev/v1/sessions/${currentSession.session_id}/${action}`,
      { method: "POST", body: "{}" },
    );
    currentSession = { ...currentSession, ...session };
    elements.pauseButton.textContent = paused ? "暂停" : "继续";
    updateTrainingControls();
    updateMetrics(session.metrics);
  } catch (error) {
    showAlert(error.message);
  } finally {
    elements.pauseButton.disabled = false;
  }
}

async function cancelStart() {
  if (
    !currentSession ||
    !["created", "preparing"].includes(currentSession.status)
  ) {
    return;
  }
  const sessionId = currentSession.session_id;
  const confirmed = await requestConfirmation({
    title: "取消本次启动？",
    message: "Live Engine 将停止，本次不会保存，历史与趋势也不会新增。",
    safeLabel: "继续准备",
    confirmLabel: "取消启动",
  });
  if (!confirmed || !currentSession || currentSession.session_id !== sessionId) {
    return;
  }
  if (!["created", "preparing"].includes(currentSession.status)) {
    showAlert("训练已经开始，请使用结束并保存。");
    return;
  }
  elements.cancelButton.disabled = true;
  elements.cancelButton.textContent = "正在取消…";
  try {
    await request(`/dev/v1/sessions/${sessionId}/cancel`, {
      method: "POST",
      body: "{}",
    });
    restart();
    showHome();
  } catch (error) {
    showAlert(error.message);
  } finally {
    elements.cancelButton.disabled = false;
    elements.cancelButton.textContent = "取消启动";
  }
}

async function stopSession() {
  if (
    !currentSession ||
    !["active", "paused"].includes(currentSession.status)
  ) {
    return;
  }
  const sessionId = currentSession.session_id;
  const confirmed = await requestConfirmation({
    title: "结束并保存本次训练？",
    message:
      "将停止计数，保存视频、关键点和训练摘要，并开始离线分析。确认期间训练仍在继续。",
    safeLabel: "继续训练",
    confirmLabel: "结束并保存",
  });
  if (!confirmed || !currentSession || currentSession.session_id !== sessionId) {
    return;
  }
  if (!["active", "paused"].includes(currentSession.status)) return;
  elements.stopButton.disabled = true;
  elements.stopButton.textContent = "正在结束…";
  try {
    const result = await request(`/dev/v1/sessions/${sessionId}/stop`, {
      method: "POST",
      body: "{}",
    });
    await showSummary(result.session_id, result);
  } catch (error) {
    try {
      const detail = await request(
        `/dev/v1/sessions/${sessionId}`,
      );
      if (["completed", "cancelled", "failed"].includes(detail.status)) {
        await showSummary(detail.session_id, detail);
      } else {
        showAlert(error.message);
      }
    } catch {
      showAlert(error.message);
    }
  } finally {
    elements.stopButton.disabled = false;
    updateTrainingControls();
    updateStopButton(currentSession?.status);
  }
}

async function showSummary(sessionId, detail = null) {
  if (summarySessionId === sessionId) return;
  const session =
    detail ||
    (await request(`/dev/v1/sessions/${sessionId}`));
  summarySessionId = sessionId;
  currentSession = {
    ...(currentSession?.session_id === sessionId ? currentSession : {}),
    ...session,
    status: session.status || "analyzing",
  };
  closeLiveSocket();
  elements.stream.removeAttribute("src");
  delete elements.stream.dataset.sessionId;
  elements.homePanel.hidden = true;
  elements.trainingPanel.hidden = true;
  elements.setupPanel.hidden = true;
  elements.summaryPanel.hidden = false;

  const summary = session.live_summary || {};
  const reps = summary.reps ?? lastMetrics.reps;
  const duration = summary.duration_s ?? lastMetrics.elapsed_s;
  const confidence = summary.mean_confidence ?? lastMetrics.confidence;
  const fps = summary.algorithm?.inference_fps ?? lastMetrics.fps;
  elements.summaryReps.textContent = String(reps ?? 0);
  elements.summaryDuration.textContent = formatDuration(duration);
  elements.summaryConfidence.textContent = confidence
    ? `${Math.round(Number(confidence) * 100)}%`
    : "--";
  elements.summaryFps.textContent = fps
    ? `${Number(fps).toFixed(1)} FPS`
    : "--";
  elements.summaryPath.textContent = session.artifacts?.summary_path || "";
  elements.openReportButton.hidden = true;
  elements.analysisFinding.hidden = true;
  elements.analysisStatusText.textContent =
    session.status === "failed"
      ? "训练失败"
      : session.status === "cancelled"
        ? "训练已取消"
        : analysisStageNames[session.analysis?.stage] ||
          session.analysis?.stage ||
          "queued";
  elements.analysisProgressBar.style.width =
    `${Math.round((Number(session.analysis?.progress) || 0) * 100)}%`;
  if (session.report_url) {
    applyReportReady({
      session_id: session.session_id,
      report_url: session.report_url,
      summary: session.analysis?.summary || {},
    });
  }
}

function restart() {
  if (confirmResolver) settleConfirmation(false);
  closeLiveSocket();
  currentSession = null;
  updateTrainingControls();
  summarySessionId = null;
  resetMetrics();
  elements.stream.removeAttribute("src");
  delete elements.stream.dataset.sessionId;
  showHome();
  elements.setupError.textContent = "";
  elements.pauseButton.textContent = "暂停";
  updateStopButton(null);
  updateSpeechStatus(null);
  elements.openReportButton.hidden = true;
}

elements.startForm.addEventListener("submit", startSession);
elements.speechButton.addEventListener("click", toggleSpeech);
elements.pauseButton.addEventListener("click", togglePause);
elements.cancelButton.addEventListener("click", cancelStart);
elements.confirmSafe.addEventListener("click", () => settleConfirmation(false));
elements.confirmDanger.addEventListener("click", () => settleConfirmation(true));
elements.confirmDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  settleConfirmation(false);
});
elements.confirmDialog.addEventListener("click", (event) => {
  if (event.target === elements.confirmDialog) settleConfirmation(false);
});
elements.stopButton.addEventListener("click", stopSession);
elements.restartButton.addEventListener("click", () => {
  restart();
  showSetup();
});
elements.homeOpenSetup.addEventListener("click", showSetup);
elements.setupBackHome.addEventListener("click", showHome);

async function initializeMirror() {
  try {
    await ensureBootstrap();
    try {
      applyDraftConfig(await request("/dev/v1/config"));
    } catch {
      // Keep the local defaults when the optional draft endpoint is unavailable.
    }
    connectMirrorSocket();
  } catch (error) {
    showSetupError(`Core API 不可用：${error.message}`);
  }
}

initializeMirror();
