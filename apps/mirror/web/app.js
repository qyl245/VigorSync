const query = new URLSearchParams(window.location.search);
const ENGINE_URL = (query.get("engine") || "http://127.0.0.1:8020").replace(/\/$/, "");

const elements = {
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
  alertMessage: document.querySelector("#alert-message"),
  pauseButton: document.querySelector("#pause-button"),
  stopButton: document.querySelector("#stop-button"),
  summaryReps: document.querySelector("#summary-reps"),
  summaryDuration: document.querySelector("#summary-duration"),
  summaryConfidence: document.querySelector("#summary-confidence"),
  summaryFps: document.querySelector("#summary-fps"),
  summaryPath: document.querySelector("#summary-path"),
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

let currentSession = null;
let websocket = null;
let reconnectTimer = null;
let alertTimer = null;
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
  const response = await fetch(`${ENGINE_URL}${path}`, {
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

function showAlert(message) {
  if (!message) return;
  elements.alertMessage.textContent = message;
  elements.alert.hidden = false;
  window.clearTimeout(alertTimer);
  alertTimer = window.setTimeout(() => {
    elements.alert.hidden = true;
  }, 3200);
}

function setConnection(text, online = false) {
  elements.connectionBadge.textContent = text;
  elements.connectionBadge.classList.toggle("online", online);
}

function connectWebSocket() {
  if (!currentSession) return;
  window.clearTimeout(reconnectTimer);
  if (websocket) websocket.close();
  websocket = new WebSocket(currentSession.ws_url);
  websocket.addEventListener("open", () => setConnection("实时连接", true));
  websocket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "live.metrics") {
      updateMetrics(message.payload);
    } else if (message.type === "form.alert") {
      showAlert(message.payload.message);
    } else if (message.type === "session.state") {
      if (message.payload.status === "failed") {
        setConnection("发生错误");
        showAlert("实时引擎发生错误，请结束本次训练后重试。");
      } else if (message.payload.status === "paused") {
        elements.pauseButton.textContent = "继续";
      } else if (message.payload.status === "active") {
        elements.pauseButton.textContent = "暂停";
      }
    }
  });
  websocket.addEventListener("close", () => {
    setConnection("连接中断");
    if (currentSession) {
      reconnectTimer = window.setTimeout(connectWebSocket, 1200);
    }
  });
}

async function waitUntilReady(sessionId) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const session = await request(`/dev/v1/sessions/${sessionId}`);
    currentSession = { ...currentSession, ...session };
    if (session.status === "active") {
      elements.stream.src = `${session.stream_url}?t=${Date.now()}`;
      setConnection("实时连接", true);
      return;
    }
    if (session.status === "failed") {
      throw new Error(session.error?.message || "摄像头或姿态模型启动失败");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 250));
  }
  throw new Error("启动超时，请检查摄像头是否被其他程序占用");
}

async function startSession(event) {
  event.preventDefault();
  showSetupError("");
  setLoading(true);
  try {
    const target = Math.max(1, Number(elements.targetReps.value) || 12);
    const session = await request("/dev/v1/sessions", {
      method: "POST",
      body: JSON.stringify({
        exercise: elements.exercise.value,
        view: elements.view.value,
        target_reps: target,
      }),
    });
    currentSession = session;
    elements.setupPanel.hidden = true;
    elements.summaryPanel.hidden = true;
    elements.trainingPanel.hidden = false;
    elements.exerciseName.textContent = exerciseNames[session.exercise];
    elements.jointName.textContent = jointNames[session.exercise];
    elements.repCount.textContent = "0";
    elements.elapsedTime.textContent = "00:00";
    elements.angleValue.textContent = "--";
    elements.angleMarker.style.width = "0%";
    elements.repCluster.classList.remove("target-hit");
    elements.phaseLabel.textContent = "准备相机";
    setConnection("准备相机");
    updateMetrics(session.metrics);
    await waitUntilReady(session.session_id);
    connectWebSocket();
  } catch (error) {
    showSetupError(error.message);
    elements.trainingPanel.hidden = true;
    elements.setupPanel.hidden = false;
  } finally {
    setLoading(false);
  }
}

async function togglePause() {
  if (!currentSession) return;
  const paused = elements.pauseButton.textContent === "继续";
  const action = paused ? "resume" : "pause";
  try {
    const session = await request(
      `/dev/v1/sessions/${currentSession.session_id}/${action}`,
      { method: "POST", body: "{}" },
    );
    currentSession = { ...currentSession, ...session };
    elements.pauseButton.textContent = paused ? "暂停" : "继续";
    updateMetrics(session.metrics);
  } catch (error) {
    showAlert(error.message);
  }
}

async function stopSession() {
  if (!currentSession) return;
  elements.stopButton.disabled = true;
  elements.stopButton.textContent = "正在结束…";
  try {
    const result = await request(
      `/dev/v1/sessions/${currentSession.session_id}/stop`,
      { method: "POST", body: "{}" },
    );
    window.clearTimeout(reconnectTimer);
    if (websocket) {
      const closing = websocket;
      websocket = null;
      closing.close();
    }
    elements.stream.removeAttribute("src");
    elements.trainingPanel.hidden = true;
    elements.summaryPanel.hidden = false;
    elements.summaryReps.textContent = String(lastMetrics.reps ?? 0);
    elements.summaryDuration.textContent = formatDuration(lastMetrics.elapsed_s);
    elements.summaryConfidence.textContent = lastMetrics.confidence
      ? `${Math.round(lastMetrics.confidence * 100)}%`
      : "--";
    elements.summaryFps.textContent = lastMetrics.fps
      ? `${Number(lastMetrics.fps).toFixed(1)} FPS`
      : "--";
    elements.summaryPath.textContent = result.artifacts?.summary_path || "";
  } catch (error) {
    showAlert(error.message);
  } finally {
    currentSession = null;
    elements.stopButton.disabled = false;
    elements.stopButton.textContent = "结束训练";
  }
}

function restart() {
  window.clearTimeout(reconnectTimer);
  elements.summaryPanel.hidden = true;
  elements.setupPanel.hidden = false;
  elements.setupError.textContent = "";
  elements.pauseButton.textContent = "暂停";
}

elements.startForm.addEventListener("submit", startSession);
elements.pauseButton.addEventListener("click", togglePause);
elements.stopButton.addEventListener("click", stopSession);
elements.restartButton.addEventListener("click", restart);
