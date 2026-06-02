"use strict";

const state = {
  groups: [],
  slug: null,
  tab: "channels",
  videoTagFilter: "",
  settingsCat: "ai_gateway",
  aiGatewayModels: [],
};

// ── API 헬퍼 ──────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = (data && data.detail) || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function toast(msg, isErr) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  setTimeout(() => (t.className = "toast"), 2600);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function formatDuration(seconds) {
  if (seconds == null) return "-";
  const n = Number(seconds);
  if (!Number.isFinite(n) || n < 0) return "-";
  const total = Math.floor(n);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

const content = () => document.getElementById("content");

// ── 그룹 ──────────────────────────────────────────────────
async function loadGroups() {
  state.groups = await api("GET", "/api/groups");
  const sel = document.getElementById("group-select");
  sel.innerHTML = state.groups
    .map((g) => `<option value="${esc(g.slug)}">${esc(g.name)} (${esc(g.slug)})</option>`)
    .join("");
  if (state.groups.length) {
    if (!state.slug || !state.groups.find((g) => g.slug === state.slug)) {
      state.slug = state.groups[0].slug;
    }
    sel.value = state.slug;
    render();
  } else {
    state.slug = null;
    content().innerHTML = `<div class="empty">그룹이 없습니다. "+ 새 그룹"으로 생성하세요.</div>`;
  }
}

function newGroupModal() {
  openModal(`
    <h2>새 그룹</h2>
    <div class="field"><label>그룹 영문 ID (소문자/숫자/밑줄)</label><input id="ng-slug" placeholder="invest" /></div>
    <div class="field"><label>그룹 명칭</label><input id="ng-name" placeholder="투자 모니터" /></div>
    <div class="field"><label>DB 스키마 이름 (선택, 기본 youtube_{그룹 영문 ID})</label><input id="ng-schema" placeholder="youtube_invest" /></div>
    <div class="row"><button class="btn" id="ng-save">생성</button></div>
  `);
  document.getElementById("ng-save").onclick = async () => {
    try {
      const slug = document.getElementById("ng-slug").value.trim();
      const name = document.getElementById("ng-name").value.trim();
      const schema = document.getElementById("ng-schema").value.trim();
      const payload = { slug, name };
      if (schema) payload.schema_name = schema;
      await api("POST", "/api/groups", payload);
      closeModal();
      state.slug = slug;
      await loadGroups();
      toast("그룹 생성됨");
    } catch (e) {
      toast(e.message, true);
    }
  };
}

// ── 탭 라우팅 ──────────────────────────────────────────────
function render() {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === state.tab)
  );
  if (!state.slug) return;
  if (state.tab === "channels") renderChannels();
  else if (state.tab === "videos") renderVideos();
  else if (state.tab === "instant") renderInstantAnalyze();
  else if (state.tab === "tags") renderTags();
  else if (state.tab === "digests") renderDigests();
  else if (state.tab === "settings") renderSettings();
  else if (state.tab === "logs") renderLogs();
}

// ── 채널 ──────────────────────────────────────────────────
async function renderChannels() {
  content().innerHTML = `
    <div class="toolbar">
      <input id="ch-input" placeholder="채널 URL / @handle / UC아이디" style="min-width:320px" />
      <label class="muted"><input type="checkbox" id="ch-backfill" /> 과거 영상 수집</label>
      <button class="btn" id="ch-add">채널 추가</button>
      <div class="spacer"></div>
      <button class="btn secondary" id="ch-poll">지금 폴링</button>
    </div>
    <div id="ch-list"><div class="empty">불러오는 중…</div></div>`;
  document.getElementById("ch-add").onclick = addChannel;
  document.getElementById("ch-poll").onclick = async () => {
    try { await api("POST", `/api/groups/${state.slug}/actions/poll`); toast("폴링 시작됨 (잠시 후 영상 탭 확인)"); }
    catch (e) { toast(e.message, true); }
  };
  try {
    const list = await api("GET", `/api/groups/${state.slug}/channels`);
    const el = document.getElementById("ch-list");
    if (!list.length) { el.innerHTML = `<div class="empty">등록된 채널이 없습니다.</div>`; return; }
    el.innerHTML = list.map((c) => `
      <div class="card row">
        <img class="thumb" src="${esc(c.thumbnail_url || "")}" onerror="this.style.visibility='hidden'" />
        <div class="grow">
          <div class="title">${esc(c.channel_name)}</div>
          <div class="muted">${esc(c.channel_handle || c.channel_id)} · ${c.is_active ? "활성" : "비활성"} · 알림 ${c.notify_enabled ? "ON" : "OFF"}</div>
          <div class="row" style="gap:8px; margin-top:6px;">
            <label class="muted" style="display:flex; align-items:center; gap:6px; margin:0;">
              주기(시간)
              <input
                type="number"
                min="1"
                step="1"
                value="${Math.max(1, Math.floor(Number(c.poll_interval_min || 60) / 60))}"
                style="width:90px"
                onchange="updateChannelInterval(${c.channel_pk}, this.value)"
              />
            </label>
          </div>
          <div class="muted">마지막 확인: ${c.last_checked_at ? new Date(c.last_checked_at).toLocaleString() : "없음"}</div>
        </div>
        <button class="btn small secondary" onclick="toggleChannel(${c.channel_pk}, ${!c.is_active})">${c.is_active ? "비활성화" : "활성화"}</button>
        <button class="btn small secondary" onclick="toggleChannelNotify(${c.channel_pk}, ${!c.notify_enabled})">${c.notify_enabled ? "알림 끄기" : "알림 켜기"}</button>
        <button class="btn small danger" onclick="deleteChannel(${c.channel_pk})">삭제</button>
      </div>`).join("");
  } catch (e) { toast(e.message, true); }
}

async function addChannel() {
  const input = document.getElementById("ch-input").value.trim();
  if (!input) return;
  const backfill = document.getElementById("ch-backfill").checked;
  try {
    await api("POST", `/api/groups/${state.slug}/channels`, { channel_input: input, backfill });
    toast("채널 추가됨");
    renderChannels();
  } catch (e) { toast(e.message, true); }
}
async function toggleChannel(pk, active) {
  try { await api("PATCH", `/api/groups/${state.slug}/channels/${pk}`, { is_active: active }); renderChannels(); }
  catch (e) { toast(e.message, true); }
}
async function toggleChannelNotify(pk, enabled) {
  try { await api("PATCH", `/api/groups/${state.slug}/channels/${pk}`, { notify_enabled: enabled }); renderChannels(); }
  catch (e) { toast(e.message, true); }
}
async function updateChannelInterval(pk, hoursValue) {
  const hours = parseInt(hoursValue || "0", 10);
  if (!Number.isFinite(hours) || hours < 1) {
    toast("주기(시간)는 1 이상이어야 합니다.", true);
    renderChannels();
    return;
  }
  try {
    await api("PATCH", `/api/groups/${state.slug}/channels/${pk}`, { poll_interval_min: hours * 60 });
    toast("채널 주기 저장됨");
  } catch (e) {
    toast(e.message, true);
    renderChannels();
  }
}
async function deleteChannel(pk) {
  if (!confirm("채널을 삭제할까요?")) return;
  try { await api("DELETE", `/api/groups/${state.slug}/channels/${pk}`); renderChannels(); }
  catch (e) { toast(e.message, true); }
}

// ── 영상 ──────────────────────────────────────────────────
async function renderVideos() {
  content().innerHTML = `
    <div class="toolbar">
      <select id="vd-status">
        <option value="">전체 상태</option>
        <option value="pending">pending</option>
        <option value="processing">processing</option>
        <option value="done">done</option>
        <option value="failed">failed</option>
      </select>
      <input id="vd-tag" placeholder="태그 필터 (예: 반도체)" value="${esc(state.videoTagFilter || "")}" style="min-width:220px" />
      <button class="btn secondary" id="vd-tag-apply">태그 적용</button>
      <button class="btn secondary" id="vd-tag-clear">태그 초기화</button>
      <button class="btn secondary" id="vd-refresh">새로고침</button>
      <div class="spacer"></div>
      <button class="btn secondary" id="vd-analyze">지금 분석 (1건)</button>
    </div>
    <div id="vd-list"><div class="empty">불러오는 중…</div></div>`;
  document.getElementById("vd-status").onchange = loadVideos;
  document.getElementById("vd-tag-apply").onclick = () => {
    state.videoTagFilter = document.getElementById("vd-tag").value.trim();
    loadVideos();
  };
  document.getElementById("vd-tag-clear").onclick = () => {
    state.videoTagFilter = "";
    document.getElementById("vd-tag").value = "";
    loadVideos();
  };
  document.getElementById("vd-refresh").onclick = loadVideos;
  document.getElementById("vd-analyze").onclick = async () => {
    try { await api("POST", `/api/groups/${state.slug}/actions/analyze`); toast("분석 시작됨 (잠시 후 새로고침)"); }
    catch (e) { toast(e.message, true); }
  };
  loadVideos();
}

async function loadVideos() {
  const status = document.getElementById("vd-status").value;
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (state.videoTagFilter) params.set("tag", state.videoTagFilter);
  const q = params.toString() ? `?${params.toString()}` : "";
  try {
    const list = await api("GET", `/api/groups/${state.slug}/videos${q}`);
    const el = document.getElementById("vd-list");
    if (!list.length) { el.innerHTML = `<div class="empty">영상이 없습니다.</div>`; return; }
    el.innerHTML = list.map((v) => `
      <div class="card row">
        <img class="thumb" src="${esc(v.thumbnail_url || "")}" onerror="this.style.visibility='hidden'" />
        <div class="grow">
          <div class="title">${esc(v.headline || v.title)}</div>
          <div class="muted">${esc(v.one_line || "")}</div>
          <div class="muted">${new Date(v.published_at).toLocaleString()} ${v.notified_at ? "· 발송됨" : ""}</div>
        </div>
        <span class="badge ${esc(v.analysis_status)}">${esc(v.analysis_status)}</span>
        <button class="btn small secondary" onclick="openVideo(${v.video_pk})">상세</button>
      </div>`).join("");
  } catch (e) { toast(e.message, true); }
}

// ── 태그 ──────────────────────────────────────────────────
async function renderTags() {
  content().innerHTML = `
    <div class="toolbar">
      <button class="btn secondary" id="tg-refresh">새로고침</button>
    </div>
    <div id="tg-cloud" class="card"><div class="empty">불러오는 중…</div></div>
    <div id="tg-list"><div class="empty">불러오는 중…</div></div>`;
  document.getElementById("tg-refresh").onclick = renderTags;
  try {
    const tags = await api("GET", `/api/groups/${state.slug}/tags?min_count=1&limit=200`);
    const cloud = document.getElementById("tg-cloud");
    const list = document.getElementById("tg-list");
    if (!tags.length) {
      cloud.innerHTML = `<div class="empty">태그가 없습니다.</div>`;
      list.innerHTML = "";
      return;
    }
    const maxCount = Math.max(...tags.map((t) => Number(t.video_count || 1)));
    cloud.innerHTML = `<div class="tags">${tags.map((t) => {
      const count = Number(t.video_count || 1);
      const size = 12 + Math.round((count / maxCount) * 16);
      return `<button class="tag" style="font-size:${size}px" onclick="openTagVideos('${esc(t.name)}')">${esc(t.name)} (${count})</button>`;
    }).join("")}</div>`;
    list.innerHTML = tags.map((t) => `
      <div class="card row">
        <div class="grow">
          <div class="title">${esc(t.name)}</div>
          <div class="muted">유형: ${esc(t.tag_type)} · 영상 수: ${t.video_count}</div>
        </div>
        <button class="btn small secondary" onclick="openTagVideos('${esc(t.name)}')">영상 보기</button>
      </div>
    `).join("");
  } catch (e) {
    toast(e.message, true);
  }
}

function openTagVideos(tagName) {
  state.videoTagFilter = String(tagName || "").trim();
  state.tab = "videos";
  render();
}

async function openVideo(pk) {
  try {
    const v = await api("GET", `/api/groups/${state.slug}/videos/${pk}`);
    const a = v.analysis;
    const bulletPoints = Array.isArray(a?.bullet_points) ? a.bullet_points : [];
    const insights = Array.isArray(a?.insights) ? a.insights : [];
    const keyPoints = Array.isArray(a?.key_points) ? a.key_points : [];
    const entities = Array.isArray(a?.entities) ? a.entities : [];
    const tags = Array.isArray(v.tags) ? v.tags : [];
    openModal(`
      <span class="close-x" onclick="closeModal()">×</span>
      <h2>${esc(v.title)}</h2>
      <div class="muted">${new Date(v.published_at).toLocaleString()} · 재생시간 ${formatDuration(v.duration_seconds)} · <a href="${esc(v.video_url)}" target="_blank" style="color:var(--accent)">영상 열기</a></div>
      <div style="margin:10px 0"><span class="badge ${esc(v.analysis_status)}">${esc(v.analysis_status)}</span></div>
      ${v.analysis_error ? `<div class="muted" style="color:var(--danger)">${esc(v.analysis_error)}</div>` : ""}
      ${a ? `
        <h3>${esc(a.headline || "")}</h3>
        <p><b>${esc(a.one_line || "")}</b></p>
        <div class="md">${esc(a.short_summary_md || "")}</div>
        ${bulletPoints.length ? `<h4 style="margin-top:12px">핵심 내용</h4><ul>${bulletPoints.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
        ${insights.length ? `<h4 style="margin-top:12px">인사이트</h4><ul>${insights.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
        ${keyPoints.length ? `<h4 style="margin-top:12px">타임스탬프 포인트</h4><ul>${keyPoints.map((kp) => `<li><b>${esc(kp?.timestamp || "-")}</b> · ${esc(kp?.point || "")}</li>`).join("")}</ul>` : ""}
        ${entities.length ? `<h4 style="margin-top:12px">등장 엔티티</h4><ul>${entities.map((en) => `<li>${esc(en?.type || "-")} · ${esc(en?.name || "")}</li>`).join("")}</ul>` : ""}
        ${tags.length ? `<h4 style="margin-top:12px">태그</h4><div class="tags">${tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}
        ${a.full_analysis_md ? `<details style="margin-top:10px"><summary class="muted">전체 분석</summary><div class="md" style="margin-top:8px">${esc(a.full_analysis_md)}</div></details>` : ""}
        <div class="muted" style="margin-top:10px">감성: ${esc(a.sentiment || "-")} · 신뢰도: ${a.confidence_score ?? "-"} · 모델: ${esc(a.model_name || "-")}</div>
      ` : `<div class="muted">분석 결과가 아직 없습니다.</div>`}
      <div class="row" style="margin-top:16px">
        <button class="btn secondary" onclick="reanalyze(${pk})">재분석 요청</button>
        <button class="btn danger" onclick="deleteVideo(${pk})">삭제</button>
      </div>
    `);
  } catch (e) { toast(e.message, true); }
}
async function reanalyze(pk) {
  try { await api("POST", `/api/groups/${state.slug}/videos/${pk}/reanalyze`); closeModal(); toast("재분석 대기열 등록됨"); loadVideos(); }
  catch (e) { toast(e.message, true); }
}
async function deleteVideo(pk) {
  if (!confirm("영상을 삭제할까요? (재수집되지 않도록 차단 목록에 추가됩니다)")) return;
  try { await api("DELETE", `/api/groups/${state.slug}/videos/${pk}`); closeModal(); toast("삭제됨"); loadVideos(); }
  catch (e) { toast(e.message, true); }
}

// ── 영상 분석(단일 URL) ───────────────────────────────────
async function renderInstantAnalyze() {
  content().innerHTML = `
    <div class="card">
      <div class="title" style="margin-bottom:8px">단일 영상 분석 등록</div>
      <div class="muted" style="margin-bottom:12px">YouTube 영상 URL 또는 영상 ID를 입력하면 즉시 분석 대기열에 등록합니다.</div>
      <div class="row" style="align-items:flex-end; gap:8px;">
        <div class="grow">
          <label class="muted" for="inst-url">영상 URL / ID</label>
          <input id="inst-url" placeholder="https://www.youtube.com/watch?v=... 또는 영상ID" />
        </div>
        <button class="btn" id="inst-submit">분석 등록</button>
      </div>
      <div id="inst-result" class="muted" style="margin-top:10px"></div>
    </div>`;
  document.getElementById("inst-submit").onclick = submitInstantAnalyze;
  const input = document.getElementById("inst-url");
  input.onkeydown = (e) => {
    if (e.key === "Enter") submitInstantAnalyze();
  };
}

async function submitInstantAnalyze() {
  const input = document.getElementById("inst-url");
  const result = document.getElementById("inst-result");
  const videoUrl = input.value.trim();
  if (!videoUrl) {
    toast("영상 URL/ID를 입력하세요.", true);
    return;
  }
  try {
    const resp = await api("POST", `/api/groups/${state.slug}/videos/instant`, { video_url: videoUrl });
    result.textContent = `등록 완료: video_id=${resp.video_id} (${resp.existing ? "기존 영상 재분석" : "신규 영상"})`;
    toast("즉시 분석 대기열에 등록됨");
    openVideo(resp.video_pk);
  } catch (e) {
    toast(e.message, true);
  }
}

// ── 설정 ──────────────────────────────────────────────────
const SETTING_DEFS = {
  ai_gateway: [
    {
      key: "base_url",
      label: "게이트웨이 Base URL",
      help: "예: 100.114.126.67:4000 또는 http://... 형식. 저장 후 모델 목록 조회 가능",
    },
    { key: "api_key", label: "API 키", secret: true, help: "litellm 게이트웨이 인증 키" },
    {
      key: "primary_model",
      label: "기본 모델 (경로 A)",
      type: "model_select",
      help: "영상 분석 1차 호출 모델. 아래 목록에서 스크롤로 선택 가능",
    },
    {
      key: "fallback_model",
      label: "폴백 모델 (경로 B)",
      type: "model_select",
      help: "기본 모델 실패 시 재시도에 사용할 모델",
    },
    {
      key: "temperature",
      label: "temperature",
      type: "float",
      help: "창의성 정도(0~1 권장). 낮을수록 일관적, 높을수록 다양함",
    },
    {
      key: "max_tokens",
      label: "max_tokens",
      type: "int",
      help: "LLM 최대 출력 길이 제한. 값이 너무 작으면 요약이 잘릴 수 있음",
    },
  ],
  prompts: [{ key: "analysis_prompt", label: "분석 프롬프트", type: "textarea" }],
  database: [
    { key: "host", label: "호스트" },
    { key: "port", label: "포트", type: "int" },
    { key: "dbname", label: "DB 이름" },
    { key: "username", label: "사용자" },
    { key: "password", label: "비밀번호", secret: true },
    {
      key: "sslmode",
      label: "sslmode",
      type: "select",
      options: ["disable", "prefer", "require"],
      help: "disable: 암호화 안 함, prefer: 가능하면 SSL(권장), require: SSL 필수",
    },
  ],
  polling: [
    { key: "youtube_api_key", label: "YouTube API 키", secret: true },
    {
      key: "window_hours",
      label: "최신 영상 수집 범위",
      type: "int_days",
      help: "최근 N일 이내 업로드된 영상만 수집. 예: 7일 = 일주일 전부터 수집",
    },
    {
      key: "default_channel_interval_min",
      label: "새 영상 확인 주기",
      type: "int_hours",
      help: "각 채널의 새 영상 확인 기본 주기. 예: 12시간",
    },
    { key: "max_concurrent_channels", label: "동시 점검 채널 수", type: "int" },
    { key: "max_concurrent_analyses", label: "AI 동시 요약 수", type: "int" },
  ],
  notification: [
    { key: "enabled", label: "알림 활성화", type: "bool" },
    { key: "bot_token", label: "텔레그램 봇 토큰", secret: true },
    { key: "chat_ids", label: "Chat ID 목록", type: "chatlist" },
    {
      key: "parse_mode",
      label: "parse_mode",
      type: "select",
      options: ["HTML", "MarkdownV2", "None"],
      help: "텔레그램 메시지 서식 방식. 일반적으로 HTML 권장",
    },
  ],
  digest: [
    { key: "enabled", label: "주간 리뷰 자동 생성", type: "bool" },
    { key: "period_weeks", label: "집계 기간(주)", type: "int" },
    {
      key: "schedule_day",
      label: "실행 요일",
      type: "select",
      options: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    },
    { key: "schedule_time", label: "실행 시각(HH:MM)" },
    { key: "timezone", label: "시간대" },
    { key: "telegram_enabled", label: "다이제스트 텔레그램 발송", type: "bool" },
    { key: "category", label: "카테고리 필터(선택)" },
  ],
};

const SETTING_TAB_LABELS = {
  ai_gateway: "AI Gateway",
  prompts: "Prompts",
  database: "Database",
  polling: "Monitoring",
  notification: "Notification",
  digest: "Digest",
};

async function renderSettings() {
  const cats = Object.keys(SETTING_DEFS);
  content().innerHTML = `
    <div class="subtabs">${cats.map((c) =>
      `<div class="subtab ${c === state.settingsCat ? "active" : ""}" onclick="selectCat('${c}')">${esc(SETTING_TAB_LABELS[c] || c)}</div>`).join("")}</div>
    <div id="set-toolbar" class="toolbar"></div>
    <div id="set-form"><div class="empty">불러오는 중…</div></div>`;
  try {
    const items = await api("GET", `/api/groups/${state.slug}/settings/${state.settingsCat}`);
    if (state.settingsCat === "ai_gateway") {
      document.getElementById("set-toolbar").innerHTML = `
        <button class="btn secondary" id="ai-model-load">모델 목록 불러오기</button>
        <span class="muted">게이트웨이 접속이 되면 모델을 스크롤 선택할 수 있습니다.</span>
      `;
      document.getElementById("ai-model-load").onclick = () => loadAiGatewayModels(true);
      state.aiGatewayModels = [];
      await loadAiGatewayModels(false);
    } else {
      document.getElementById("set-toolbar").innerHTML = "";
    }
    const map = {};
    items.forEach((i) => (map[i.key] = i));
    const defs = SETTING_DEFS[state.settingsCat];
    document.getElementById("set-form").innerHTML =
      defs.map((d) => fieldHtml(d, map[d.key])).join("") +
      `<div class="row" style="margin-top:10px"><button class="btn" id="set-save">저장</button></div>`;
    document.getElementById("set-save").onclick = saveSettings;
  } catch (e) { toast(e.message, true); }
}
function selectCat(c) { state.settingsCat = c; renderSettings(); }

async function loadAiGatewayModels(showToast) {
  try {
    const models = await api("GET", `/api/groups/${state.slug}/settings/ai_gateway/models`);
    state.aiGatewayModels = Array.isArray(models) ? models : [];
    if (showToast) toast(`모델 ${state.aiGatewayModels.length}개 로드됨`);
  } catch (e) {
    state.aiGatewayModels = [];
    if (showToast) toast(e.message, true);
  }
}

function fieldHtml(def, item) {
  const val = item ? item.value : "";
  const help = def.help ? `<div class="muted">${esc(def.help)}</div>` : "";
  if (def.type === "textarea")
    return `<div class="field"><label>${esc(def.label)}</label><textarea data-key="${def.key}">${esc(val || "")}</textarea>${help}</div>`;
  if (def.type === "bool") {
    const checked = String(val).toLowerCase() === "true" || val === true ? "checked" : "";
    if (state.settingsCat === "notification" && def.key === "enabled") {
      return `<div class="field">
        <label>${esc(def.label)}</label>
        <label style="display:flex; align-items:center; gap:8px; margin-top:4px;">
          <input type="checkbox" data-key="${def.key}" data-type="bool" ${checked} />
          <span>사용</span>
        </label>
        ${help}
      </div>`;
    }
    return `<div class="field">
      <label>
        <input type="checkbox" data-key="${def.key}" data-type="bool" ${checked} />
        ${esc(def.label)}
      </label>
      ${help}
    </div>`;
  }
  if (def.type === "model_select") {
    const models = state.aiGatewayModels || [];
    const safeKey = def.key.replace(/[^a-zA-Z0-9_-]/g, "_");
    const toggleId = `model-toggle-${safeKey}`;
    const pickerId = `model-picker-${safeKey}`;
    const currentId = `model-current-${safeKey}`;
    const opts = models
      .map((m) => {
        const selected = m === val ? "selected" : "";
        return `<option value="${esc(m)}" ${selected}>${esc(m)}</option>`;
      })
      .join("");
    const current = val || "(선택 안 됨)";
    return `<div class="field"><label>${esc(def.label)}</label>
      <input type="hidden" data-key="${def.key}" value="${esc(val || "")}" />
      <div class="row" style="gap:8px; align-items:center;">
        <span id="${currentId}" class="muted">${esc(current)}</span>
        <button type="button" class="btn small secondary" id="${toggleId}" onclick="toggleModelPicker('${pickerId}', '${toggleId}')">모델 선택 열기</button>
      </div>
      <div id="${pickerId}" style="display:none; margin-top:8px;">
        <select size="8" onchange="applyModelSelection('${def.key}', this.value, '${currentId}')">${opts || `<option value="">(모델 목록을 먼저 불러오세요)</option>`}</select>
      </div>
      ${help}</div>`;
  }
  if (def.type === "select") {
    const options = (def.options || []).map((o) => {
      const selected = String(val || "") === o ? "selected" : "";
      return `<option value="${esc(o)}" ${selected}>${esc(o)}</option>`;
    });
    if (val && !def.options.includes(String(val))) {
      options.unshift(`<option value="${esc(val)}" selected>${esc(val)} (현재값)</option>`);
    }
    return `<div class="field"><label>${esc(def.label)}</label><select data-key="${def.key}">${options.join("")}</select>${help}</div>`;
  }
  if (def.type === "int_days") {
    const hours = Number(val || 0);
    const days = Number.isFinite(hours) ? Math.max(0, Math.floor(hours / 24)) : 0;
    return `<div class="field"><label>${esc(def.label)} (일)</label>
      <input type="number" min="0" step="1" data-key="${def.key}" data-type="int_days" value="${esc(days)}" />${help}</div>`;
  }
  if (def.type === "int_hours") {
    const mins = Number(val || 0);
    const hours = Number.isFinite(mins) ? Math.max(0, Math.floor(mins / 60)) : 0;
    return `<div class="field"><label>${esc(def.label)} (시간)</label>
      <input type="number" min="0" step="1" data-key="${def.key}" data-type="int_hours" value="${esc(hours)}" />${help}</div>`;
  }
  if (def.type === "chatlist") {
    let arr = [];
    try { arr = JSON.parse(val || "[]"); } catch { arr = String(val || "").split(",").map((s) => s.trim()).filter(Boolean); }
    if (!Array.isArray(arr)) arr = [];
    return `<div class="field"><label>${esc(def.label)}</label>
      <div class="chatlist" id="chatlist">
        ${arr.map((c) => chatRow(c)).join("")}
      </div>
      <button class="btn small secondary" style="margin-top:6px" onclick="addChatRow()">+ Chat ID 추가</button>${help}</div>`;
  }
  if (def.secret) {
    const placeholder = val ? "설정됨 (변경 시에만 입력)" : "미설정";
    return `<div class="field"><label>${esc(def.label)}</label>
      <input type="password" data-key="${def.key}" data-secret="1" placeholder="${placeholder}" />${help}</div>`;
  }
  return `<div class="field"><label>${esc(def.label)}</label>
    <input type="text" data-key="${def.key}" data-type="${def.type || "string"}" value="${esc(val ?? "")}" />${help}</div>`;
}

function chatRow(value) {
  return `<div class="row"><input class="grow chat-id" value="${esc(value || "")}" placeholder="-100... 또는 사용자 ID" />
    <button class="btn small danger" onclick="this.parentElement.remove()">×</button></div>`;
}
function addChatRow() {
  const list = document.getElementById("chatlist");
  list.insertAdjacentHTML("beforeend", chatRow(""));
}

function toggleModelPicker(pickerId, buttonId) {
  const picker = document.getElementById(pickerId);
  const btn = document.getElementById(buttonId);
  if (!picker || !btn) return;
  const open = picker.style.display !== "none";
  picker.style.display = open ? "none" : "block";
  btn.textContent = open ? "모델 선택 열기" : "모델 선택 닫기";
}

function applyModelSelection(key, value, currentId) {
  const hidden = document.querySelector(`[data-key="${key}"]`);
  if (!hidden) return;
  hidden.value = value;
  const label = document.getElementById(currentId);
  if (label) label.textContent = value || "(선택 안 됨)";
}

async function saveSettings() {
  const defs = SETTING_DEFS[state.settingsCat];
  const items = [];
  const toValueType = (d) => {
    if (d.type === "bool") return "bool";
    if (d.type === "int") return "int";
    if (d.type === "float") return "float";
    if (d.type === "chatlist") return "json";
    // select/model_select/textarea/string 등 UI 타입은 저장 타입 string으로 보낸다.
    return "string";
  };
  for (const d of defs) {
    if (d.type === "chatlist") {
      const ids = Array.from(document.querySelectorAll(".chat-id"))
        .map((i) => i.value.trim()).filter(Boolean);
      items.push({ key: d.key, value: JSON.stringify(ids), value_type: "json" });
      continue;
    }
    const el = document.querySelector(`[data-key="${d.key}"]`);
    if (!el) continue;
    if (d.type === "bool") {
      items.push({ key: d.key, value: el.checked ? "true" : "false", value_type: "bool" });
    } else if (d.type === "int_days") {
      const days = parseInt(el.value || "0", 10);
      const safeDays = Number.isFinite(days) && days > 0 ? days : 0;
      items.push({ key: d.key, value: String(safeDays * 24), value_type: "int" });
    } else if (d.type === "int_hours") {
      const hours = parseInt(el.value || "0", 10);
      const safeHours = Number.isFinite(hours) && hours > 0 ? hours : 0;
      items.push({ key: d.key, value: String(safeHours * 60), value_type: "int" });
    } else if (d.secret) {
      // 빈 값이면 기존 시크릿 보존(백엔드 처리)
      items.push({ key: d.key, value: el.value, value_type: "string", is_secret: true });
    } else {
      items.push({ key: d.key, value: el.value, value_type: toValueType(d) });
    }
  }
  try {
    await api("PUT", `/api/groups/${state.slug}/settings/${state.settingsCat}`, { items });
    toast("저장됨");
    renderSettings();
  } catch (e) { toast(e.message, true); }
}

// ── 로그 ──────────────────────────────────────────────────
async function renderLogs() {
  content().innerHTML = `<div class="toolbar"><button class="btn secondary" id="lg-refresh">새로고침</button></div><div id="lg-list"><div class="empty">불러오는 중…</div></div>`;
  document.getElementById("lg-refresh").onclick = renderLogs;
  try {
    const logs = await api("GET", `/api/groups/${state.slug}/logs`);
    const el = document.getElementById("lg-list");
    if (!logs.length) { el.innerHTML = `<div class="empty">로그가 없습니다.</div>`; return; }
    el.innerHTML = logs.map((l) => `
      <div class="card row">
        <span class="badge ${l.status === "success" ? "done" : l.status === "fail" ? "failed" : "pending"}">${esc(l.status)}</span>
        <div class="grow">
          <div class="title">${esc(l.job_type)}</div>
          <div class="muted">${esc(l.message || "")}</div>
        </div>
        <div class="muted">${new Date(l.started_at).toLocaleString()}${l.duration_ms != null ? ` · ${l.duration_ms}ms` : ""}</div>
      </div>`).join("");
  } catch (e) { toast(e.message, true); }
}

// ── 주간 리뷰 ──────────────────────────────────────────────
async function renderDigests() {
  content().innerHTML = `
    <div class="toolbar">
      <button class="btn" id="dg-generate">지금 생성</button>
      <button class="btn secondary" id="dg-refresh">새로고침</button>
    </div>
    <div id="dg-list"><div class="empty">불러오는 중…</div></div>`;
  document.getElementById("dg-refresh").onclick = renderDigests;
  document.getElementById("dg-generate").onclick = async () => {
    try {
      const d = await api("POST", `/api/groups/${state.slug}/digests/generate`, { save: true });
      toast("주간 리뷰 생성 완료");
      openDigest(d.digest_pk);
      renderDigests();
    } catch (e) {
      toast(e.message, true);
    }
  };
  try {
    const digests = await api("GET", `/api/groups/${state.slug}/digests`);
    const el = document.getElementById("dg-list");
    if (!digests.length) {
      el.innerHTML = `<div class="empty">주간 리뷰가 없습니다.</div>`;
      return;
    }
    el.innerHTML = digests.map((d) => `
      <div class="card row">
        <div class="grow">
          <div class="title">${esc(d.headline || "주간 리뷰")}</div>
          <div class="muted">${new Date(d.period_start).toLocaleDateString()} ~ ${new Date(d.period_end).toLocaleDateString()} · 영상 ${d.video_count}건 · ${esc(d.status)}</div>
        </div>
        <button class="btn small secondary" onclick="openDigest(${d.digest_pk})">상세</button>
        <button class="btn small danger" onclick="deleteDigest(${d.digest_pk})">삭제</button>
      </div>
    `).join("");
  } catch (e) {
    toast(e.message, true);
  }
}

async function openDigest(digestPk) {
  try {
    const d = await api("GET", `/api/groups/${state.slug}/digests/${digestPk}`);
    const tags = Array.isArray(d.top_tags) ? d.top_tags : [];
    const channels = Array.isArray(d.top_channels) ? d.top_channels : [];
    openModal(`
      <span class="close-x" onclick="closeModal()">×</span>
      <h2>${esc(d.headline || "주간 리뷰")}</h2>
      <div class="muted">${new Date(d.period_start).toLocaleDateString()} ~ ${new Date(d.period_end).toLocaleDateString()} · 상태 ${esc(d.status)}</div>
      <div class="muted" style="margin-top:6px">분석 영상 수: ${d.video_count}</div>
      ${d.summary_md ? `<div class="md" style="margin-top:12px">${esc(d.summary_md)}</div>` : ""}
      ${tags.length ? `<h4 style="margin-top:12px">상위 태그</h4><div class="tags">${tags.map((t) => `<span class="tag">${esc(t.name)} (${t.count})</span>`).join("")}</div>` : ""}
      ${channels.length ? `<h4 style="margin-top:12px">상위 채널</h4><ul>${channels.map((c) => `<li>${esc(c.name)} (${c.count})</li>`).join("")}</ul>` : ""}
      <div class="muted" style="margin-top:8px">감성 분포: ${esc(JSON.stringify(d.sentiment_breakdown || {}))}</div>
    `);
  } catch (e) {
    toast(e.message, true);
  }
}

async function deleteDigest(digestPk) {
  if (!confirm("주간 리뷰를 삭제할까요?")) return;
  try {
    await api("DELETE", `/api/groups/${state.slug}/digests/${digestPk}`);
    toast("삭제됨");
    renderDigests();
  } catch (e) {
    toast(e.message, true);
  }
}

// ── 모달 ──────────────────────────────────────────────────
function openModal(html) {
  document.getElementById("modal-root").innerHTML =
    `<div class="modal-backdrop" onclick="if(event.target===this)closeModal()"><div class="modal">${html}</div></div>`;
}
function closeModal() { document.getElementById("modal-root").innerHTML = ""; }

// ── 초기화 ────────────────────────────────────────────────
document.getElementById("group-select").onchange = (e) => { state.slug = e.target.value; render(); };
document.getElementById("btn-new-group").onclick = newGroupModal;
document.querySelectorAll(".tab").forEach((t) => (t.onclick = () => { state.tab = t.dataset.tab; render(); }));
loadGroups().catch((e) => toast(e.message, true));
