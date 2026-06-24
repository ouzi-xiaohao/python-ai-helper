const conversation = document.querySelector("#conversation");
const messageTemplate = document.querySelector("#messageTemplate");
const textInput = document.querySelector("#textInput");
const sendButton = document.querySelector("#sendButton");
const voiceButton = document.querySelector("#voiceButton");
const stopButton = document.querySelector("#stopButton");
const wave = document.querySelector("#wave");
const modelSelect = document.querySelector("#modelSelect");
const voiceSelect = document.querySelector("#voiceSelect");
const asrSelect = document.querySelector("#asrSelect");
const ttsSelect = document.querySelector("#ttsSelect");
const fileInput = document.querySelector("#fileInput");
const attachButton = document.querySelector("#attachButton");
const attachmentStrip = document.querySelector("#attachmentStrip");
const sessionSelect = document.querySelector("#sessionSelect");
const newSessionButton = document.querySelector("#newSessionButton");
const sessionLabel = document.querySelector("#sessionLabel");
const usageLabel = document.querySelector("#usageLabel");
const costLabel = document.querySelector("#costLabel");
const toolsLabel = document.querySelector("#toolsLabel");
const scenarioTitle = document.querySelector("#scenarioTitle");
const scenarioSubtitle = document.querySelector("#scenarioSubtitle");
const scenarioActions = document.querySelector("#scenarioActions");
const knowledgeCount = document.querySelector("#knowledgeCount");
const knowledgeHint = document.querySelector("#knowledgeHint");
const knowledgeFileInput = document.querySelector("#knowledgeFileInput");
const knowledgeUploadButton = document.querySelector("#knowledgeUploadButton");
const loginPanel = document.querySelector("#loginPanel");
const loginForm = document.querySelector("#loginForm");
const usernameInput = document.querySelector("#usernameInput");
const passwordInput = document.querySelector("#passwordInput");
const loginError = document.querySelector("#loginError");
const userLabel = document.querySelector("#userLabel");
const logoutButton = document.querySelector("#logoutButton");

let welcomeMessage = "你好！我是校园服务智能助手，可以帮你做办事咨询、宿舍报修、图片故障描述和实时信息查询。";

// Frontend state mirrors the current browser session. The backend remains the
// source of truth for persisted sessions and message history.
const state = {
  messages: [
    {
      role: "assistant",
      content: welcomeMessage,
    },
  ],
  sessionId: null,
  recognition: null,
  speaking: false,
  toolsUsed: [],
  modelOptions: [],
  pendingAttachments: [],
  knowledgeDocuments: [],
  authToken: localStorage.getItem("ouzi_auth_token"),
  currentUser: null,
};

async function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.authToken) headers.set("Authorization", `Bearer ${state.authToken}`);
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    logout(false);
    throw new Error("登录已过期，请重新登录。");
  }
  return response;
}

function showLogin(message = "") {
  loginPanel.classList.remove("hidden");
  loginError.textContent = message;
}

function hideLogin() {
  loginPanel.classList.add("hidden");
  loginError.textContent = "";
}

function logout(reload = true) {
  state.authToken = null;
  state.currentUser = null;
  localStorage.removeItem("ouzi_auth_token");
  userLabel.textContent = "未登录";
  updateKnowledgePermission();
  showLogin();
  if (reload) {
    state.messages = [{ role: "assistant", content: welcomeMessage }];
    renderMessages();
  }
}

async function login(username, password) {
  const response = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new Error("用户名或密码错误");
  }
  const data = await response.json();
  state.authToken = data.access_token;
  state.currentUser = { username: data.username, role: data.role };
  localStorage.setItem("ouzi_auth_token", state.authToken);
  userLabel.textContent = `${data.username} · ${data.role}`;
  updateKnowledgePermission();
  hideLogin();
}

async function loadCurrentUser() {
  if (!state.authToken) return false;
  try {
    const response = await apiFetch("/api/me");
    if (!response.ok) return false;
    state.currentUser = await response.json();
    userLabel.textContent = `${state.currentUser.username} · ${state.currentUser.role}`;
    updateKnowledgePermission();
    hideLogin();
    return true;
  } catch {
    return false;
  }
}

function updateKnowledgePermission() {
  const canUploadKnowledge = state.currentUser?.role === "admin";
  knowledgeUploadButton.hidden = !canUploadKnowledge;
  knowledgeFileInput.disabled = !canUploadKnowledge;
}

function fillWave(active = false) {
  // The waveform is decorative but state-aware: it animates while recording or
  // playing synthesized speech.
  wave.replaceChildren();
  const values = [8, 12, 18, 24, 30, 34, 32, 26, 18, 12, 9, 10, 14, 22, 29, 34, 30, 22, 14, 10];
  values.forEach((value, index) => {
    const bar = document.createElement("span");
    bar.className = "bar";
    const boost = active ? Math.sin((Date.now() / 120 + index) * 0.8) * 12 + 14 : 0;
    bar.style.height = `${Math.max(5, value + boost)}px`;
    wave.append(bar);
  });
}

function renderMessages() {
  // Re-rendering the small chat list keeps streaming updates simple and avoids
  // stale button handlers after messages change.
  conversation.replaceChildren();
  state.messages.forEach((message) => {
    const node = messageTemplate.content.firstElementChild.cloneNode(true);
    node.classList.add(message.role);
    node.querySelector(".speaker").textContent = message.role === "assistant" ? "AI" : "你";
    node.querySelector(".content").textContent = `${message.role === "assistant" ? "AI" : "你"}：${message.content}`;

    const actions = node.querySelector(".message-actions");
    if (message.role === "assistant") {
      actions.append(actionButton("语音播放 ▶", () => speakText(message.content)));
      actions.append(actionButton("复制", () => navigator.clipboard?.writeText(message.content)));
    } else {
      actions.append(actionButton("语音输入", startVoiceInput));
    }
    conversation.append(node);
  });
  conversation.scrollTop = conversation.scrollHeight;
}

function renderAttachments() {
  attachmentStrip.replaceChildren();
  attachmentStrip.classList.toggle("has-items", state.pendingAttachments.length > 0);
  state.pendingAttachments.forEach((attachment) => {
    const chip = document.createElement("span");
    chip.className = "attachment-chip";

    const preview = document.createElement("img");
    preview.src = attachment.url;
    preview.alt = attachment.filename;

    const label = document.createElement("span");
    label.textContent = attachment.filename;

    chip.append(preview, label);
    attachmentStrip.append(chip);
  });
}

function actionButton(label, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

async function loadOptions() {
  const response = await apiFetch("/api/options");
  const data = await response.json();
  state.modelOptions = data.models;
  setOptions(modelSelect, data.models, "name");
  setOptions(asrSelect, data.asr_engines, "name");
  setOptions(ttsSelect, data.tts_engines, "name");
  renderScenario(data.scenario);
  setOptions(voiceSelect, [
    { id: "huoshan-clear-female", name: "火山引擎-清新女声" },
    { id: "browser-default", name: "浏览器默认语音" },
  ], "name");
}

function renderScenario(scenario) {
  if (!scenario) return;
  scenarioTitle.textContent = scenario.name;
  scenarioSubtitle.textContent = scenario.subtitle;
  welcomeMessage = `你好！我是${scenario.name}，可以处理${scenario.badges.join("、")}等问题。`;
  state.messages = state.messages.length === 1 && state.messages[0].role === "assistant"
    ? [{ role: "assistant", content: welcomeMessage }]
    : state.messages;
  scenarioActions.replaceChildren();

  scenario.quick_actions.forEach((action) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = action.label;
    button.addEventListener("click", () => {
      textInput.value = action.prompt;
      textInput.focus();
    });
    scenarioActions.append(button);
  });
  renderMessages();
}

async function loadSessions() {
  // Session metadata comes from SQLite through the backend. If the database is
  // empty, create the first conversation automatically.
  const response = await apiFetch("/api/sessions");
  const sessions = await response.json();
  sessionSelect.replaceChildren();

  sessions.forEach((session) => {
    const option = document.createElement("option");
    option.value = session.id;
    option.textContent = `${session.id} · ${session.title}`;
    sessionSelect.append(option);
  });

  if (state.sessionId) {
    sessionSelect.value = String(state.sessionId);
  } else if (sessions.length > 0) {
    state.sessionId = sessions[0].id;
    sessionSelect.value = String(state.sessionId);
    await loadHistory(state.sessionId);
  } else {
    await createSession();
  }
  updateStats();
}

async function loadKnowledgeDocuments() {
  const response = await apiFetch("/api/knowledge");
  if (!response.ok) return;
  state.knowledgeDocuments = await response.json();
  renderKnowledgeStatus();
}

function renderKnowledgeStatus(uploading = false) {
  const count = state.knowledgeDocuments.length;
  knowledgeCount.textContent = `${count} 份资料`;
  knowledgeHint.textContent = uploading
    ? "正在入库..."
    : count > 0
      ? `最近：${state.knowledgeDocuments[0].filename}`
      : state.currentUser?.role === "admin"
        ? "支持 txt / md / pdf / docx"
        : "普通用户可使用资料问答";
}

async function uploadKnowledgeFiles(files) {
  if (state.currentUser?.role !== "admin") return;
  const selectedFiles = Array.from(files);
  if (selectedFiles.length === 0) return;
  renderKnowledgeStatus(true);

  for (const file of selectedFiles) {
    const form = new FormData();
    form.append("file", file);
    const response = await apiFetch("/api/knowledge/upload", {
      method: "POST",
      body: form,
    });
    if (!response.ok) {
      const error = await response.text();
      state.messages.push({ role: "assistant", content: `知识库上传失败：${error}` });
      renderMessages();
      continue;
    }
  }

  await loadKnowledgeDocuments();
}

async function createSession() {
  const response = await apiFetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: "新的对话" }),
  });
  const session = await response.json();
  state.sessionId = session.id;
  state.messages = [{ role: "assistant", content: welcomeMessage }];
  await loadSessions();
  renderMessages();
}

async function loadHistory(sessionId) {
  const response = await apiFetch(`/api/sessions/${sessionId}`);
  const data = await response.json();
  state.sessionId = data.session.id;
  state.messages = data.messages.length > 0
    ? data.messages
    : [{ role: "assistant", content: welcomeMessage }];
  renderMessages();
  updateStats();
}

function setOptions(select, items, labelKey) {
  select.replaceChildren();
  items.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item[labelKey];
    select.append(option);
  });
}

async function sendMessage() {
  // Chat defaults to the SSE endpoint so users see model output as it arrives.
  const text = textInput.value.trim();
  if (!text && state.pendingAttachments.length === 0) return;

  const attachments = [...state.pendingAttachments];
  const attachmentText = attachments.length > 0
    ? `\n[图片：${attachments.map((item) => item.filename).join("，")}]`
    : "";
  state.messages.push({ role: "user", content: `${text || "请分析这张图片。"}${attachmentText}` });
  textInput.value = "";
  state.pendingAttachments = [];
  renderAttachments();
  renderMessages();

  const history = state.messages.map(({ role, content }) => ({ role, content }));
  const pending = { role: "assistant", content: "" };
  state.toolsUsed = [];
  updateStats();
  state.messages.push(pending);
  renderMessages();

  try {
    const response = await apiFetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: modelSelect.value,
        session_id: state.sessionId,
        messages: history,
        attachments,
      }),
    });
    if (!response.ok) throw new Error(await response.text());
    await readChatStream(response, pending);
  } catch (error) {
    pending.content = `调用失败：${error.message}`;
  }
  renderMessages();
  await loadSessions();
}

async function uploadImages(files) {
  const selectedFiles = Array.from(files);
  if (selectedFiles.length === 0) return;

  const form = new FormData();
  selectedFiles.forEach((file) => form.append("files", file));
  const response = await apiFetch("/api/uploads", {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    const error = await response.text();
    state.messages.push({ role: "assistant", content: `图片上传失败：${error}` });
    renderMessages();
    return;
  }

  const data = await response.json();
  state.pendingAttachments.push(...data.attachments);
  ensureVisionModelSelected();
  renderAttachments();
}

function ensureVisionModelSelected() {
  const current = state.modelOptions.find((model) => model.id === modelSelect.value);
  if (current?.supports_vision) return;
  const visionModel = state.modelOptions.find((model) => model.supports_vision);
  if (visionModel) modelSelect.value = visionModel.id;
}

async function readChatStream(response, pending) {
  // The backend emits SSE frames. We parse them manually because fetch streams
  // support POST bodies while EventSource only supports GET.
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const eventText of events) {
      handleStreamEvent(eventText, pending);
    }
  }
}

function handleStreamEvent(eventText, pending) {
  // meta/tool/token/done/error events keep UI state, tool visibility, and usage
  // accounting synchronized with the backend.
  const lines = eventText.split("\n");
  const event = lines.find((line) => line.startsWith("event: "))?.slice(7);
  const dataLine = lines.find((line) => line.startsWith("data: "));
  if (!event || !dataLine) return;

  const data = JSON.parse(dataLine.slice(6));
  if (event === "meta") {
    state.sessionId = data.session_id;
    updateStats();
  }
  if (event === "token") {
    pending.content += data.text;
    renderMessages();
  }
  if (event === "tool") {
    state.toolsUsed.push(data);
    updateStats();
  }
  if (event === "done") {
    state.sessionId = data.session_id;
    state.toolsUsed = data.tools_used || state.toolsUsed;
    updateStats(data.usage, data.cost);
  }
  if (event === "error") {
    pending.content = `调用失败：${data.message}`;
  }
}

function updateStats(usage = null, cost = null) {
  sessionLabel.textContent = `会话：${state.sessionId ?? "未创建"}`;
  if (usage) {
    usageLabel.textContent = `Tokens：${usage.total_tokens}（输入 ${usage.prompt_tokens} / 输出 ${usage.completion_tokens}）`;
  }
  if (cost) {
    costLabel.textContent = `费用：$${cost.total_cost.toFixed(8)}`;
  }
  toolsLabel.textContent = state.toolsUsed.length > 0
    ? `工具：${state.toolsUsed.map((tool) => tool.title).join("、")}`
    : "工具：未调用";
}

function startVoiceInput() {
  // Browser ASR is useful for demos because it does not need backend keys.
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    textInput.value = "今天北京天气怎么样？";
    return;
  }

  const recognition = new SpeechRecognition();
  recognition.lang = "zh-CN";
  recognition.interimResults = true;
  recognition.continuous = false;
  state.recognition = recognition;

  recognition.onstart = () => {
    voiceButton.classList.add("recording");
    voiceButton.textContent = "正在听...";
  };
  recognition.onresult = (event) => {
    const transcript = Array.from(event.results)
      .map((result) => result[0].transcript)
      .join("");
    textInput.value = transcript;
  };
  recognition.onend = () => {
    voiceButton.classList.remove("recording");
    voiceButton.textContent = "按住说话";
  };
  recognition.start();
}

async function speakText(text) {
  // Cloud TTS engines return audio_url; browser TTS engines return null and use
  // SpeechSynthesis locally.
  const response = await apiFetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ engine: ttsSelect.value, text }),
  });
  const data = await response.json();

  if (data.audio_url) {
    const audio = new Audio(data.audio_url);
    state.speaking = true;
    audio.addEventListener("ended", () => {
      state.speaking = false;
    });
    audio.addEventListener("error", () => {
      state.speaking = false;
    });
    await audio.play();
    return;
  }

  if (!window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "zh-CN";
  utterance.onstart = () => {
    state.speaking = true;
  };
  utterance.onend = () => {
    state.speaking = false;
  };
  window.speechSynthesis.speak(utterance);
}

function stopAll() {
  state.recognition?.stop();
  window.speechSynthesis?.cancel();
  state.speaking = false;
}

sendButton.addEventListener("click", sendMessage);
textInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") sendMessage();
});
voiceButton.addEventListener("click", startVoiceInput);
stopButton.addEventListener("click", stopAll);
attachButton.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", async () => {
  await uploadImages(fileInput.files);
  fileInput.value = "";
});
knowledgeUploadButton.addEventListener("click", () => knowledgeFileInput.click());
knowledgeFileInput.addEventListener("change", async () => {
  await uploadKnowledgeFiles(knowledgeFileInput.files);
  knowledgeFileInput.value = "";
});
loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await login(usernameInput.value.trim(), passwordInput.value);
    await initAppData();
  } catch (error) {
    showLogin(error.message);
  }
});
logoutButton.addEventListener("click", () => logout());
newSessionButton.addEventListener("click", createSession);
sessionSelect.addEventListener("change", () => loadHistory(Number(sessionSelect.value)));

setInterval(() => fillWave(state.speaking || voiceButton.classList.contains("recording")), 160);
fillWave(false);
renderMessages();
renderAttachments();

async function initAppData() {
  await loadOptions();
  await loadKnowledgeDocuments();
  await loadSessions();
}

async function initApp() {
  if (await loadCurrentUser()) {
    await initAppData();
  } else {
    showLogin();
  }
}

await initApp();
