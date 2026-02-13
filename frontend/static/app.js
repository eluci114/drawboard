/**
 * Drawboard — 공유 캔버스 클라이언트
 * WebSocket으로 실시간 동기화, REST로 AI 그리기 요청
 * 줌/팬 기능 포함
 */

const CANVAS_W = 15000;
const CANVAS_H = 8000;

const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
const canvasViewport = document.getElementById("canvasViewport");
const canvasContainer = document.getElementById("canvasContainer");
const cursorLayer = document.getElementById("cursorLayer");
const wsStatus = document.getElementById("wsStatus");
const btnAiStart = document.getElementById("btnAiStart");
const btnAgentStart = document.getElementById("btnAgentStart");
const btnSendMessage = document.getElementById("btnSendMessage");
const agentIdEl = document.getElementById("agentId");
const agentRegisterNameEl = document.getElementById("agentRegisterName");
const btnAgentRegister = document.getElementById("btnAgentRegister");
const agentIssuedHint = document.getElementById("agentIssuedHint");
const openclawBaseUrlEl = document.getElementById("openclawBaseUrl");
const aiMessageEl = document.getElementById("aiMessage");
const aiNameEl = document.getElementById("aiName");
const aiProviderEl = document.getElementById("aiProvider");
const aiModelEl = document.getElementById("aiModel");
const apiKeyEl = document.getElementById("apiKey");
const apiKeyLabel = document.getElementById("apiKeyLabel");
const apiKeyRow = document.getElementById("apiKeyRow");
const askStatus = document.getElementById("askStatus");
const askPromptEl = document.getElementById("askPrompt");
const btnAskDraw = document.getElementById("btnAskDraw");
const aiListEl = document.getElementById("aiList");
const btnZoomIn = document.getElementById("btnZoomIn");
const btnZoomOut = document.getElementById("btnZoomOut");
const btnZoomFit = document.getElementById("btnZoomFit");
const btnResetView = document.getElementById("btnResetView");
const btnGuideClose = document.getElementById("btnGuideClose");
const btnGuideOpen = document.getElementById("btnGuideOpen");
const guideModal = document.getElementById("guideModal");
const zoomLevel = document.getElementById("zoomLevel");
const autoFollow = document.getElementById("autoFollow");

let ws = null;
let events = [];
let reconnectTimer = null;
/** AI 커서 상태: ai_id -> { ai_name, x, y } */
let aiCursors = {};
let aiListUpdateTimer = null;

// 줌/팬 상태
let zoom = 1.0;
let panX = 0;
let panY = 0;
let isDragging = false;
let dragStartX = 0;
let dragStartY = 0;
let myAiName = ""; // 내 AI 이름 추적

function getWsUrl() {
  const base = window.location.origin.replace(/^http/, "ws");
  return `${base}/ws`;
}

function setWsStatus(connected) {
  if (connected) {
    wsStatus.textContent = "🟢 실시간 연결됨 • 다른 사용자와 동기화 중";
  } else {
    wsStatus.textContent = "🔴 연결 끊김 • 재연결 시도 중… 새로고침해 보세요.";
  }
  wsStatus.className = "status " + (connected ? "connected" : "disconnected");
}

function setCursor(aiId, aiName, x, y) {
  aiCursors[aiId] = { ai_name: aiName, x, y };
  let el = cursorLayer.querySelector(`[data-ai-id="${aiId}"]`);
  if (!el) {
    el = document.createElement("div");
    el.className = "ai-cursor";
    el.setAttribute("data-ai-id", aiId);
    el.setAttribute("title", aiName);
    cursorLayer.appendChild(el);
  }
  el.style.left = Math.round(x) + "px";
  el.style.top = Math.round(y) + "px";
  el.textContent = (aiName || "AI").slice(0, 1).toUpperCase();
  scheduleAiListUpdate();
}

function removeCursors(aiIds) {
  if (!Array.isArray(aiIds)) return;
  aiIds.forEach((id) => {
    delete aiCursors[id];
    const el = cursorLayer.querySelector(`[data-ai-id="${id}"]`);
    if (el) el.remove();
  });
  updateAiStartButtonState();
  scheduleAiListUpdate();
}

function updateAiStartButtonState() {
  const hasAi = Object.keys(aiCursors).length > 0;
  btnAiStart.disabled = hasAi;
  if (btnAgentStart) btnAgentStart.disabled = hasAi;
  const hint = document.getElementById("aiStartHint");
  if (hint) {
    hint.textContent = hasAi ? "실행 중입니다. 한 번에 한 AI만 참여 가능합니다." : "";
    hint.className = "hint ai-start-hint " + (hasAi ? "show" : "");
  }
}

function updateAiList() {
  if (!aiListEl) return;
  aiListEl.innerHTML = "";
  const entries = Object.entries(aiCursors)
    .map(([aiId, c]) => ({ aiId, ...c }))
    .sort((a, b) => (a.ai_name || "").localeCompare(b.ai_name || "", "ko"));

  if (entries.length === 0) {
    const li = document.createElement("li");
    li.style.opacity = "0.8";
    li.innerHTML = "현재 실행 중인 AI가 없습니다.<br /><span class=\"hint-inline\">봇을 참여 주소(/bot)로 보내면 여기 표시됩니다.</span>";
    aiListEl.appendChild(li);
    return;
  }

  for (const c of entries) {
    const li = document.createElement("li");
    li.style.cursor = "pointer";
    const name = c.ai_name || c.aiId;
    const aiId = c.aiId;
    li.innerHTML = `<span class="ai-name">${escapeHtml(name)}</span><span class="time">x=${Math.round(c.x)}, y=${Math.round(c.y)}</span><button type="button" class="ai-list-leave" title="해당 에이전트 나가기">나가기</button>`;
    const leaveBtn = li.querySelector(".ai-list-leave");
    if (leaveBtn) {
      leaveBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        leaveAi(aiId);
      });
    }
    li.addEventListener("click", () => {
      centerOnPoint(c.x, c.y, 1.0);
    });
    aiListEl.appendChild(li);
  }
}

async function leaveAi(aiId) {
  if (!aiId) return;
  if (askStatus && askStatus.textContent && askStatus.textContent.includes("캔버스에 참여했습니다")) {
    askStatus.textContent = "";
    askStatus.className = "status-small";
  }
  try {
    const r = await fetch("/api/ai/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ai_id: aiId }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      console.warn("나가기 실패:", err.detail || r.status);
    }
  } catch (e) {
    console.warn("나가기 요청 실패:", e);
  }
}

function scheduleAiListUpdate() {
  if (!aiListEl) return;
  if (aiListUpdateTimer) return;
  aiListUpdateTimer = setTimeout(() => {
    aiListUpdateTimer = null;
    updateAiList();
  }, 200);
}

function applyCursorsMap(cursorsMap) {
  if (!cursorsMap || typeof cursorsMap !== "object") return;
  const ids = new Set(Object.keys(aiCursors));
  Object.entries(cursorsMap).forEach(([aiId, c]) => {
    if (c && typeof c.x === "number" && typeof c.y === "number") {
      setCursor(aiId, c.ai_name || c.name || aiId, c.x, c.y);
      ids.delete(aiId);
    }
  });
  ids.forEach((id) => {
    delete aiCursors[id];
    const el = cursorLayer.querySelector(`[data-ai-id="${id}"]`);
    if (el) el.remove();
  });
  updateAiStartButtonState();
  updateAiList();
}

/** 최소 줌: 이보다 작아지면 흰 캔버스 바깥(회색)이 보임. 뷰포트를 흰 캔버스가 꽉 채우는 배율 */
function getMinZoom() {
  const rect = canvasViewport.getBoundingClientRect();
  const vw = rect.width;
  const vh = rect.height;
  if (vw <= 0 || vh <= 0) return 0.1;
  const scaleToFillW = vw / CANVAS_W;
  const scaleToFillH = vh / CANVAS_H;
  const exact = Math.max(scaleToFillW, scaleToFillH);
  return exact * 1.02;
}

function updateCanvasTransform() {
  const rect = canvasViewport.getBoundingClientRect();
  const vw = rect.width;
  const vh = rect.height;
  const minZoom = getMinZoom();
  zoom = Math.max(minZoom, Math.min(5, zoom));

  const scaledW = CANVAS_W * zoom;
  const scaledH = CANVAS_H * zoom;
  // 팬 범위: 화면에 항상 캔버스만 보이도록 (회색 영역 노출 방지)
  const maxPanX = 0;
  const minPanX = Math.min(0, vw - scaledW);
  const maxPanY = 0;
  const minPanY = Math.min(0, vh - scaledH);
  panX = Math.max(minPanX, Math.min(maxPanX, panX));
  panY = Math.max(minPanY, Math.min(maxPanY, panY));

  canvasContainer.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
  canvasContainer.style.transformOrigin = "0 0";
  zoomLevel.textContent = Math.round(zoom * 100) + "%";
}

function zoomAtPoint(clientX, clientY, delta) {
  const rect = canvasViewport.getBoundingClientRect();
  const x = (clientX - rect.left - panX) / zoom;
  const y = (clientY - rect.top - panY) / zoom;
  
  const zoomFactor = delta > 0 ? 1.1 : 0.9;
  const newZoom = Math.max(getMinZoom(), Math.min(5, zoom * zoomFactor));
  
  panX = clientX - rect.left - x * newZoom;
  panY = clientY - rect.top - y * newZoom;
  zoom = newZoom;
  updateCanvasTransform();
}

function centerOnPoint(x, y, targetZoom = null) {
  const rect = canvasViewport.getBoundingClientRect();
  const containerWidth = rect.width;
  const containerHeight = rect.height;
  
  if (targetZoom !== null) {
    zoom = Math.max(getMinZoom(), Math.min(5, targetZoom));
  }
  
  panX = containerWidth / 2 - x * zoom;
  panY = containerHeight / 2 - y * zoom;
  
  updateCanvasTransform();
}

function getActionCenter(action) {
  switch (action.type) {
    case "line":
      return { x: (action.x1 + action.x2) / 2, y: (action.y1 + action.y2) / 2 };
    case "circle":
      return { x: action.x, y: action.y };
    case "rect":
      return { x: action.x + action.w / 2, y: action.y + action.h / 2 };
    case "path":
      if (action.points && action.points.length > 0) {
        const sum = action.points.reduce((acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y }), { x: 0, y: 0 });
        return { x: sum.x / action.points.length, y: sum.y / action.points.length };
      }
      return { x: CANVAS_W / 2, y: CANVAS_H / 2 };
    default:
      return { x: CANVAS_W / 2, y: CANVAS_H / 2 };
  }
}

function drawAction(action) {
  if (!ctx) return;
  switch (action.type) {
    case "line":
      ctx.strokeStyle = action.color || "#000000";
      ctx.lineWidth = (action.width ?? 2) * 2;
      ctx.beginPath();
      ctx.moveTo(action.x1, action.y1);
      ctx.lineTo(action.x2, action.y2);
      ctx.stroke();
      break;
    case "circle":
      ctx.strokeStyle = action.color || "#000000";
      ctx.lineWidth = (action.width ?? 2) * 2;
      ctx.beginPath();
      ctx.arc(action.x, action.y, action.r, 0, Math.PI * 2);
      if (action.fill) {
        ctx.fillStyle = action.color || "#000000";
        ctx.fill();
      }
      ctx.stroke();
      break;
    case "rect":
      ctx.strokeStyle = action.color || "#000000";
      ctx.lineWidth = (action.width ?? 2) * 2;
      if (action.fill) {
        ctx.fillStyle = action.color || "#000000";
        ctx.fillRect(action.x, action.y, action.w, action.h);
      }
      ctx.strokeRect(action.x, action.y, action.w, action.h);
      break;
    case "path":
      if (!action.points || action.points.length < 2) break;
      ctx.strokeStyle = action.color || "#000000";
      ctx.lineWidth = (action.width ?? 2) * 2;
      ctx.beginPath();
      ctx.moveTo(action.points[0].x, action.points[0].y);
      for (let i = 1; i < action.points.length; i++) {
        ctx.lineTo(action.points[i].x, action.points[i].y);
      }
      if (action.close) ctx.closePath();
      ctx.stroke();
      break;
    case "clear":
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);
      break;
    default:
      break;
  }
}

function redraw() {
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);
  for (const ev of events) {
    if (ev.action && ev.action.type === "clear") {
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);
    } else if (ev.action) {
      drawAction(ev.action);
    }
  }
  // 캔버스 중앙에 이동 확인용 검은 점 (항상 표시)
  const cx = CANVAS_W / 2;
  const cy = CANVAS_H / 2;
  ctx.fillStyle = "#000000";
  ctx.beginPath();
  ctx.arc(cx, cy, 6, 0, Math.PI * 2);
  ctx.fill();
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function showDrawNotification(aiName) {
  const canvasWrap = document.querySelector(".canvas-wrap");
  if (canvasWrap) {
    canvasWrap.style.animation = "none";
    setTimeout(() => {
      canvasWrap.style.animation = "pulse-border 0.5s ease";
    }, 10);
  }
}

// CSS 애니메이션 추가
const style = document.createElement("style");
style.textContent = `
  @keyframes pulse-border {
    0% { box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4); }
    50% { box-shadow: 0 8px 32px rgba(107, 140, 255, 0.6); }
    100% { box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4); }
  }
`;
document.head.appendChild(style);

// 줌/팬 이벤트 — 뷰포트 중앙 기준 줌(꼭짓점 위치 고정), 화면은 고정되고 캔버스만 이동
canvasViewport.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvasViewport.getBoundingClientRect();
  const centerX = rect.left + rect.width / 2;
  const centerY = rect.top + rect.height / 2;
  zoomAtPoint(centerX, centerY, -e.deltaY);
}, { passive: false });

canvasViewport.addEventListener("mousedown", (e) => {
  if (e.button === 0) {
    isDragging = true;
    dragStartX = e.clientX - panX;
    dragStartY = e.clientY - panY;
    canvasViewport.style.cursor = "grabbing";
  }
});

canvasViewport.addEventListener("mousemove", (e) => {
  if (isDragging) {
    panX = e.clientX - dragStartX;
    panY = e.clientY - dragStartY;
    updateCanvasTransform();
  }
});

canvasViewport.addEventListener("mouseup", () => {
  isDragging = false;
  canvasViewport.style.cursor = "grab";
});

canvasViewport.addEventListener("mouseleave", () => {
  isDragging = false;
  canvasViewport.style.cursor = "grab";
});

// 줌 버튼
btnZoomIn.addEventListener("click", () => {
  const rect = canvasViewport.getBoundingClientRect();
  zoomAtPoint(rect.left + rect.width / 2, rect.top + rect.height / 2, -1);
});

btnZoomOut.addEventListener("click", () => {
  const rect = canvasViewport.getBoundingClientRect();
  zoomAtPoint(rect.left + rect.width / 2, rect.top + rect.height / 2, 1);
});

btnZoomFit.addEventListener("click", () => {
  zoom = getMinZoom();
  centerOnPoint(CANVAS_W / 2, CANVAS_H / 2);
});

btnResetView.addEventListener("click", () => {
  zoom = Math.max(getMinZoom(), 1.0);
  panX = 0;
  panY = 0;
  updateCanvasTransform();
});

function closeGuideModal() {
  if (guideModal) guideModal.classList.remove("open");
}
if (btnGuideOpen && guideModal) {
  btnGuideOpen.addEventListener("click", () => guideModal.classList.add("open"));
}
if (btnGuideClose && guideModal) {
  btnGuideClose.addEventListener("click", closeGuideModal);
}
const guideBackdrop = document.getElementById("guideBackdrop");
if (guideBackdrop && guideModal) {
  guideBackdrop.addEventListener("click", closeGuideModal);
}

function connect() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  ws = new WebSocket(getWsUrl());

  ws.onopen = () => {
    setWsStatus(true);
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "sync" && Array.isArray(msg.events)) {
        events = msg.events;
        redraw();
      } else if (msg.type === "cursors" && msg.cursors) {
        applyCursorsMap(msg.cursors);
      } else if (msg.type === "cursor") {
        const id = msg.ai_id || msg.ai_name;
        if (id != null && typeof msg.x === "number" && typeof msg.y === "number") {
          setCursor(id, msg.ai_name || id, msg.x, msg.y);
          updateAiStartButtonState();
          if (autoFollow.checked && msg.ai_name === myAiName) {
            centerOnPoint(msg.x, msg.y, null);
          }
        }
      } else if (msg.type === "cursor_remove" && Array.isArray(msg.ai_ids)) {
        removeCursors(msg.ai_ids);
        if (msg.ai_ids.length > 0) myAiName = "";
        if (askStatus && askStatus.textContent && askStatus.textContent.includes("캔버스에 참여했습니다")) {
          askStatus.textContent = "";
          askStatus.className = "status-small";
        }
      } else if (msg.type === "ai_error") {
        // 내 AI로 보이는 에러는 상태창에 노출
        if (msg.ai_name && msg.ai_name === myAiName) {
          askStatus.textContent = "AI 오류: " + (msg.detail || "알 수 없음");
          askStatus.className = "status-small error show";
        }
      } else if (msg.type === "draw" && msg.event) {
        events.push(msg.event);
        drawAction(msg.event.action);
        const aiName = msg.event.ai_name || "Anonymous";
        showDrawNotification(aiName);
        if (autoFollow.checked && aiName === myAiName && msg.event.action) {
          const center = getActionCenter(msg.event.action);
          centerOnPoint(center.x, center.y, null);
        }
      } else if (msg.type === "clear") {
        events = [];
        redraw();
      }
    } catch (err) {
      console.warn("WS message parse error", err);
    }
  };

  ws.onclose = () => {
    setWsStatus(false);
    if (!reconnectTimer) {
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, 2000);
    }
  };

  ws.onerror = () => {}
}

async function fetchCanvas() {
  try {
    const r = await fetch("/api/canvas");
    const data = await r.json();
    if (Array.isArray(data.events)) {
      events = data.events;
      redraw();
    }
  } catch (err) {
    console.warn("Fetch canvas error", err);
  }
}

// 프로바이더별 모델 목록 (value = API 모델 ID)
const AI_MODELS = {
  openai: [
    { value: "gpt-4o-mini", label: "GPT-4o Mini" },
    { value: "gpt-4o", label: "GPT-4o" },
    { value: "gpt-4-turbo", label: "GPT-4 Turbo" },
  ],
  gemini: [
    { value: "gemini-2.0-flash", label: "Gemini 2.0 Flash" },
    { value: "gemini-2.5-flash", label: "Gemini 2.5 Flash" },
    { value: "gemini-3-flash-preview", label: "Gemini 3 Flash (Preview)" },
  ],
  claude: [
    { value: "claude-3-5-haiku-20241022", label: "Claude 3.5 Haiku" },
    { value: "claude-3-5-sonnet-20241022", label: "Claude 3.5 Sonnet" },
  ],
  perplexity: [
    { value: "llama-3.1-sonar-small-128k-online", label: "Sonar Small" },
    { value: "llama-3.1-sonar-large-128k-online", label: "Sonar Large" },
  ],
  openclaw: [
    { value: "openclaw:main", label: "main" },
    { value: "agent:main", label: "agent:main" },
  ],
};

function updateAiModelOptions() {
  if (!aiModelEl) return;
  const provider = aiProviderEl.value;
  const list = AI_MODELS[provider] || AI_MODELS.openai;
  aiModelEl.innerHTML = "";
  list.forEach(({ value, label }) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    aiModelEl.appendChild(opt);
  });
}

// AI 선택에 따라 API 키 필드 + 모델 목록 업데이트
function updateApiKeyField() {
  const provider = aiProviderEl.value;
  const labels = {
    openai: ["OpenAI API 키 (선택, 없으면 서버 기본값)", "sk-..."],
    gemini: ["Gemini API 키 (선택, 없으면 서버 기본값)", "AI..."],
    claude: ["Anthropic API 키 (선택, 없으면 서버 기본값)", "sk-ant-..."],
    perplexity: ["Perplexity API 키 (선택, 없으면 서버 기본값)", "pplx-..."],
    openclaw: ["OpenClaw Bearer 토큰 (선택, Gateway 인증 시)", ""],
  };
  const [label, placeholder] = labels[provider] || labels.openai;
  apiKeyLabel.textContent = label;
  apiKeyEl.placeholder = placeholder;
  updateAiModelOptions();
}

aiProviderEl.addEventListener("change", updateApiKeyField);
updateApiKeyField();

// 한 번에 그리기 (그림 주제 → /api/ask)
if (btnAskDraw && askPromptEl) {
  btnAskDraw.addEventListener("click", async () => {
    const prompt = (askPromptEl.value || "").trim();
    if (!prompt) {
      if (askStatus) {
        askStatus.textContent = "그림 주제를 입력하세요.";
        askStatus.className = "status-small error show";
      }
      return;
    }
    const prevBtnText = btnAskDraw.textContent;
    btnAskDraw.disabled = true;
    btnAskDraw.textContent = "그리는 중…";
    if (askStatus) {
      askStatus.textContent = "그리는 중…";
      askStatus.className = "status-small show";
    }
    try {
      const r = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt,
          ai_name: aiNameEl ? aiNameEl.value.trim() || "AI" : "AI",
          ai_provider: aiProviderEl ? aiProviderEl.value : "openai",
          api_key: apiKeyEl && apiKeyEl.value.trim() ? apiKeyEl.value.trim() : null,
          canvas_events: events,
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        askStatus.textContent = (data.detail || r.statusText) || "그리기 실패";
        askStatus.className = "status-small error show";
        return;
      }
      askStatus.textContent = "✅ " + (data.count ?? 0) + "개 그렸습니다.";
      askStatus.className = "status-small success show";
    } catch (err) {
      askStatus.textContent = "연결 오류: " + err.message;
      askStatus.className = "status-small error show";
    } finally {
      btnAskDraw.disabled = false;
      btnAskDraw.textContent = prevBtnText;
    }
  });
}

// 수동: 에이전트 ID 발급
if (btnAgentRegister) {
  btnAgentRegister.addEventListener("click", async () => {
    const name = (agentRegisterNameEl && agentRegisterNameEl.value.trim()) || "My Agent";
    btnAgentRegister.disabled = true;
    if (agentIssuedHint) {
      agentIssuedHint.style.display = "none";
      agentIssuedHint.textContent = "";
    }
    if (askStatus) {
      askStatus.textContent = "에이전트 ID 발급 중…";
      askStatus.className = "status-small show";
    }
    try {
      const r = await fetch("/api/agent/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        askStatus.textContent = (data.detail || r.statusText) || "발급 실패";
        askStatus.className = "status-small error show";
        return;
      }
      const aid = data.agent_id || "";
      if (agentIdEl) agentIdEl.value = aid;
      if (agentIssuedHint) {
        agentIssuedHint.textContent = "✅ 발급됨. Agent ID 칸에 채워졌습니다. Gateway URL 넣고 「에이전트 시작」을 누르세요.";
        agentIssuedHint.style.display = "block";
        agentIssuedHint.className = "hint";
      }
      askStatus.textContent = "에이전트 ID가 발급되었습니다.";
      askStatus.className = "status-small success show";
    } catch (err) {
      askStatus.textContent = "연결 오류: " + err.message;
      askStatus.className = "status-small error show";
    } finally {
      btnAgentRegister.disabled = false;
    }
  });
}

// 몰트북 스타일: agent_id로 에이전트 시작
if (btnAgentStart && agentIdEl) {
  btnAgentStart.addEventListener("click", async () => {
    const agentId = agentIdEl.value.trim();
    if (!agentId) {
      askStatus.textContent = "등록된 에이전트의 agent_id를 입력하세요.";
      askStatus.className = "status-small error show";
      return;
    }
    const gwUrl = openclawBaseUrlEl && openclawBaseUrlEl.value.trim();
    if (!gwUrl) {
      askStatus.textContent = "본인 OpenClaw Gateway URL을 입력하세요.";
      askStatus.className = "status-small error show";
      return;
    }
    btnAgentStart.disabled = true;
    askStatus.textContent = "에이전트 시작 중…";
    askStatus.className = "status-small show";
    try {
      const body = { agent_id: agentId, openclaw_base_url: gwUrl };
      const r = await fetch("/api/ai/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        askStatus.textContent = (typeof data.detail === "string" ? data.detail : data.detail?.[0]?.msg) || r.statusText || "시작 실패";
        askStatus.className = "status-small error show";
        return;
      }
      myAiName = data.ai_name || "Agent";
      askStatus.textContent = "✅ 에이전트가 캔버스에 참여했습니다.";
      askStatus.className = "status-small success show";
    } catch (err) {
      askStatus.textContent = "연결 오류: " + err.message;
      askStatus.className = "status-small error show";
    } finally {
      btnAgentStart.disabled = false;
      updateAiStartButtonState();
    }
  });
}

btnAiStart.addEventListener("click", async () => {
  myAiName = aiNameEl.value.trim() || "My AI";
  btnAiStart.disabled = true;
  askStatus.textContent = "AI 시작 중…";
  askStatus.className = "status-small show";
  try {
    const body = {
      ai_name: myAiName,
      ai_provider: aiProviderEl.value,
      model: aiModelEl && aiModelEl.value ? aiModelEl.value : null,
    };
    const key = apiKeyEl.value.trim();
    if (key) body.api_key = key;
    const r = await fetch("/api/ai/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const detail = data.detail;
    askStatus.textContent = (typeof detail === "string" ? detail : Array.isArray(detail) ? detail[0]?.msg : null) || r.statusText || "시작 실패";
      askStatus.className = "status-small error show";
      return;
    }
    myAiName = data.ai_name || myAiName;
    askStatus.textContent = "✅ " + (data.message || "AI가 그리기 시작했습니다. 커서를 따라가 보세요.");
    askStatus.className = "status-small success show";
  } catch (err) {
    askStatus.textContent = "연결 오류: " + err.message;
    askStatus.className = "status-small error show";
  } finally {
    btnAiStart.disabled = false;
  }
});

btnSendMessage.addEventListener("click", async () => {
  const message = aiMessageEl.value.trim();
  if (!message) {
    askStatus.textContent = "메시지를 입력하세요.";
    askStatus.className = "status-small error show";
    return;
  }
  const name = myAiName || aiNameEl.value.trim() || "My AI";
  try {
    const r = await fetch("/api/ai/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ai_name: name, message }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      askStatus.textContent = data.detail || "전송 실패";
      askStatus.className = "status-small error show";
      return;
    }
    askStatus.textContent = "메시지를 보냈습니다. 다음 스트로크에 반영됩니다.";
    askStatus.className = "status-small success show";
    aiMessageEl.value = "";
  } catch (err) {
    askStatus.textContent = "연결 오류: " + err.message;
    askStatus.className = "status-small error show";
  }
});

/** 뷰포트에 맞춰 캔버스가 보이도록 초기 뷰 적용 (뷰포트 크기가 0이면 아무것도 안 함) */
function applyInitialView() {
  const w = canvasViewport.clientWidth;
  const h = canvasViewport.clientHeight;
  if (w <= 0 || h <= 0) return false;
  zoom = getMinZoom();
  centerOnPoint(CANVAS_W / 2, CANVAS_H / 2);
  canvasViewport.style.cursor = "grab";
  return true;
}

// 초기화
fetchCanvas().then(() => {
  connect();
  redraw();

  // 뷰포트가 레이아웃된 뒤에 뷰 적용 (몇 번 재시도해서 0 크기일 때 대비)
  function tryApplyView(attempt) {
    if (applyInitialView()) return;
    if (attempt < 8) setTimeout(() => tryApplyView(attempt + 1), 80 * (attempt + 1));
  }
  setTimeout(() => tryApplyView(0), 50);

  // 뷰포트 크기가 바뀔 때(처음 크기 잡힐 때 포함) 캔버스가 꽉 보이도록 다시 적용
  const ro = new ResizeObserver(() => {
    if (canvasViewport.clientWidth > 0 && canvasViewport.clientHeight > 0) {
      updateCanvasTransform();
    }
  });
  ro.observe(canvasViewport);
  updateAiStartButtonState();
});

// 윈도우 리사이즈 시 뷰 조정
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (canvasViewport.clientWidth > 0 && canvasViewport.clientHeight > 0) {
      updateCanvasTransform();
    }
  }, 250);
});
