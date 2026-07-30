"use strict";

const $ = (id) => document.getElementById(id);

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

function mdToHtml(md) {
  // 아주 가벼운 마크다운 렌더링(제목/리스트/줄바꿈)
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc(md)
    .replace(/^### (.*)$/gm, "<h3>$1</h3>")
    .replace(/^## (.*)$/gm, "<h2>$1</h2>")
    .replace(/^# (.*)$/gm, "<h1>$1</h1>")
    .replace(/^\s*-\s+(.*)$/gm, "• $1")
    .replace(/\n/g, "<br>");
}

// SSE 로 job 진행상황을 로그 요소에 스트리밍하고, 완료 시 콜백 호출
function streamJob(jobId, logEl, onDone) {
  const es = new EventSource(`/api/jobs/${jobId}/stream`);
  es.onmessage = (e) => {
    let evt;
    try { evt = JSON.parse(e.data); } catch { return; }
    if (evt.type === "log" && evt.message) {
      logEl.textContent += evt.message + "\n";
      logEl.scrollTop = logEl.scrollHeight;
    } else if (evt.type === "done" || evt.type === "error") {
      es.close();
      getJSON(`/api/jobs/${jobId}`).then((job) => onDone(job)).catch(() => {});
    }
  };
  es.onerror = () => { es.close(); getJSON(`/api/jobs/${jobId}`).then(onDone).catch(() => {}); };
}

async function refreshStatus() {
  try {
    const s = await getJSON("/api/status");
    const bar = $("status-bar");
    const llmClass = s.llm_available ? "ok" : "warn";
    bar.innerHTML = "";
    const add = (text, cls) => {
      const b = document.createElement("span");
      b.className = "badge " + cls; b.textContent = text; bar.appendChild(b);
    };
    if (s.portable) {
      add("포터블", "ok");
      add(`학습: ${s.llm_model || "sonnet-5"} ${s.llm_available ? "연결됨" : "키 필요"}`, llmClass);
      add(`생성: ${s.gen_model || "opus-4.8"} ${s.gen_available ? "연결됨" : "키 필요"}`, s.gen_available ? "ok" : "warn");
      const ttsOk = !!(s.tts_enabled || (s.keys_set && s.keys_set.typecast));
      add(`TTS: ${ttsOk ? "연결됨" : "키 필요"}`, ttsOk ? "ok" : "warn");
    } else {
      add(`분석 AI: ${s.llm_provider} ${s.llm_available ? "사용가능" : "미설정(휴리스틱)"}`, llmClass);
      add(`생성 AI: ${s.gen_model || s.llm_provider} ${s.gen_available ? "사용가능" : "미설정(휴리스틱)"}`, s.gen_available ? "ok" : "warn");
      const sfx = s.sfx || {};
      const sfxClass = sfx.index_exists ? "ok" : "warn";
      add(`효과음 인덱스: ${sfx.index_exists ? "준비됨(" + sfx.catalog_count + ")" : "미구축"}`, sfxClass);
      add(`효과음 폴더: ${sfx.sfx_dir_exists ? "확인됨" : "없음"}`, sfx.sfx_dir_exists ? "ok" : "err");
    }
    fillChannels(s.channels);
  } catch (e) {
    $("status-bar").innerHTML = `<span class="badge err">상태 조회 실패: ${e.message}</span>`;
  }
}

function applyPortableUI(s) {
  const portable = !!(s && s.portable);
  document.body.classList.toggle("portable", portable);
  const hintFull = $("settings-hint-full");
  const hintPort = $("settings-hint-portable");
  const adv = $("ai-settings-advanced");
  const portBox = $("ai-settings-portable");
  if (hintFull) hintFull.style.display = portable ? "none" : "";
  if (hintPort) hintPort.style.display = portable ? "" : "none";
  if (adv) adv.style.display = portable ? "none" : "";
  if (portBox) portBox.style.display = portable ? "" : "none";
  if (portable) {
    if ($("portable-learn-model")) {
      $("portable-learn-model").textContent = s.portable_analysis_model || s.text_model || "claude-sonnet-5";
    }
    if ($("portable-gen-model")) {
      $("portable-gen-model").textContent = s.portable_generation_model || s.gen_effective_model || "claude-opus-5";
    }
    const key = $("set-key-portable");
    if (key) {
      const has = (s.keys_set || {}).anthropic;
      key.value = "";
      key.placeholder = has
        ? "이미 저장된 키가 있습니다. 바꿀 때만 입력하세요."
        : "Anthropic API 키 입력";
    }
    const ttsKey = $("set-tts-key-portable");
    if (ttsKey) {
      const hasTts = (s.keys_set || {}).typecast;
      ttsKey.value = "";
      ttsKey.placeholder = hasTts
        ? "이미 저장된 TTS 키가 있습니다. 바꿀 때만 입력하세요."
        : "Typecast TTS API 키 입력";
    }
    if ($("heading-learn")) $("heading-learn").textContent = "1. 채널 학습";
    if ($("heading-channels")) $("heading-channels").textContent = "2. 학습된 채널";
    if ($("heading-guide")) $("heading-guide").textContent = "3. 가이드 생성";
    if ($("heading-saved")) $("heading-saved").textContent = "4. 저장된 가이드";
  }
}

let _lastSettings = {};

async function loadSettings() {
  try {
    const s = await getJSON("/api/settings");
    _lastSettings = s;
    applyPortableUI(s);
    if (s.portable) return;
    $("set-provider").value = s.provider || "gemini";
    $("set-model").value = s.text_model || "";
    $("set-vision").value = (s.vision_model && s.vision_model !== s.text_model) ? s.vision_model : "";
    $("set-baseurl").value = s.local_base_url || "";
    $("set-gen-provider").value = s.gen_provider || "";
    $("set-gen-model").value = s.gen_model || "";
    updateGenUI(s);
    updateProviderUI(s);
  } catch (e) { /* 상태바에서 이미 표시 */ }
}

/* shortly:local-script:start */
function updateProviderUI(s) {
  s = s || _lastSettings;
  const prov = $("set-provider").value;
  const isLocal = prov === "local";
  $("row-baseurl").style.display = isLocal ? "" : "none";
  $("local-hint").style.display = isLocal ? "" : "none";
  // 로컬: 드롭다운 선택 / 그 외: 텍스트 입력
  $("row-model-local").style.display = isLocal ? "" : "none";
  $("row-model-text").style.display = isLocal ? "none" : "";
  // 로컬은 키가 필요 없음
  $("set-key").style.display = isLocal ? "none" : "";
  const has = (s.keys_set || {})[prov];
  $("set-key").value = "";
  $("set-key").placeholder = has
    ? "이미 저장된 키가 있습니다. 바꿀 때만 입력하세요."
    : "API 키(토큰) 입력";
  if (isLocal) loadLocalModels();
}

// 로컬 서버(LM Studio/Ollama)에 설치·로드된 모델 목록을 드롭다운에 채웁니다.
async function loadLocalModels() {
  const sel = $("set-model-select");
  const vsel = $("set-vision-select");
  const base = $("set-baseurl").value.trim();
  const url = "/api/local-models" + (base ? "?base_url=" + encodeURIComponent(base) : "");
  sel.innerHTML = '<option value="">불러오는 중...</option>';
  vsel.innerHTML = "";
  try {
    const r = await getJSON(url);
    let models = r.models || [];
    // 저장된 설정이 로컬일 때만 기존 선택값을 복원(다른 provider 모델명 혼입 방지)
    const savedLocal = _lastSettings.provider === "local";
    const cur = savedLocal ? (_lastSettings.text_model || "") : "";
    const curV = savedLocal ? (_lastSettings.vision_model || "") : "";
    // 저장돼 있으나 현재 목록에 없는 모델도 유지되도록 앞에 추가
    [curV, cur].forEach((m) => { if (m && !models.includes(m)) models = [m, ...models]; });
    sel.innerHTML = "";
    models.forEach((m) => {
      const o = document.createElement("option");
      o.value = m; o.textContent = m; sel.appendChild(o);
    });
    vsel.innerHTML = '<option value="">(텍스트 모델과 동일)</option>';
    models.forEach((m) => {
      const o = document.createElement("option");
      o.value = m; o.textContent = m; vsel.appendChild(o);
    });
    if (cur) sel.value = cur;
    if (curV && curV !== cur) vsel.value = curV;
    if (!models.length) {
      sel.innerHTML = '<option value="">모델 없음 · 서버/주소 확인</option>';
    }
    $("settings-msg").textContent = r.error ? ("모델 목록 실패: " + r.error) : "";
  } catch (e) {
    sel.innerHTML = '<option value="">목록 조회 실패</option>';
    $("settings-msg").textContent = "모델 목록 실패: " + e.message;
  }
}
/* shortly:local-script:end */

// 생성 전용 모델 섹션 UI 토글
function updateGenUI(s) {
  s = s || _lastSettings;
  const gp = $("set-gen-provider").value;
  const active = gp !== "";
  $("lbl-gen-model").style.display = active ? "" : "none";
  const has = active ? (s.keys_set || {})[gp] : false;
  $("set-gen-key").style.display = active ? "" : "none";
  $("set-gen-key").placeholder = has
    ? "이미 저장된 키가 있습니다. 바꿀 때만 입력하세요."
    : "생성 AI의 API 키 입력";
  // 현재 실제로 생성에 쓰이는 모델 표시
  const eff = s.gen_effective_model || s.text_model || "";
  if (eff) {
    $("gen-hint").style.display = "";
    $("gen-eff").textContent = eff + (s.gen_available ? "" : " (연결 안 됨)");
  } else {
    $("gen-hint").style.display = "none";
  }
}

if ($("set-gen-provider")) {
  $("set-gen-provider").onchange = () => updateGenUI();
}
/* shortly:local-script:start */
if ($("set-provider")) {
  $("set-provider").onchange = () => updateProviderUI();
}
if ($("btn-reload-models")) {
  $("btn-reload-models").onclick = loadLocalModels;
}
if ($("set-baseurl")) {
  $("set-baseurl").onchange = () => {
    if ($("set-provider") && $("set-provider").value === "local") loadLocalModels();
  };
}
/* shortly:local-script:end */

$("btn-save-settings").onclick = async () => {
  const btn = $("btn-save-settings");
  btn.disabled = true;
  $("settings-msg").textContent = "저장 중...";
  try {
    let body;
    if (_lastSettings.portable) {
      body = {
        provider: "anthropic",
        api_key: ($("set-key-portable") && $("set-key-portable").value.trim()) || null,
        typecast_api_key: ($("set-tts-key-portable")
          && $("set-tts-key-portable").value.trim()) || null,
      };
    } else {
      const isLocal = $("set-provider").value === "local";
      const textModel = isLocal ? $("set-model-select").value : $("set-model").value.trim();
      const visionModel = isLocal ? $("set-vision-select").value : $("set-vision").value.trim();
      body = {
        provider: $("set-provider").value,
        api_key: $("set-key").value.trim() || null,
        text_model: textModel || null,
        vision_model: visionModel || null,
        base_url: $("set-baseurl").value.trim() || null,
        // 빈 문자열이면 '분석과 동일'로 초기화되도록 그대로 전송(null 아님)
        gen_provider: $("set-gen-provider").value,
        gen_model: $("set-gen-model").value.trim(),
        gen_api_key: $("set-gen-key").value.trim() || null,
      };
    }
    const s = await postJSON("/api/settings", body);
    _lastSettings = s;
    applyPortableUI(s);
    $("settings-msg").textContent = s.llm_available
      ? "저장됨 · AI 연결 정상"
      : (s.portable
        ? "저장됨 · Claude API 키를 확인하세요"
        : "저장됨 · (연결 안 됨 → 자동 규칙 사용. 로컬은 서버 실행/pip install openai 확인)");
    if ($("set-gen-key")) $("set-gen-key").value = "";
    if ($("set-key-portable")) $("set-key-portable").value = "";
    if ($("set-tts-key-portable")) $("set-tts-key-portable").value = "";
    if (!s.portable) {
      updateProviderUI(s);
      updateGenUI(s);
    }
    refreshStatus();
  } catch (e) {
    $("settings-msg").textContent = "저장 실패: " + e.message;
  } finally { btn.disabled = false; }
};

let _guidesInit = false;

function fillChannels(channels) {
  const sel = $("channel-select");
  const prev = sel.value;
  sel.innerHTML = "";
  (channels || []).forEach((c) => {
    const o = document.createElement("option");
    o.value = c.input;
    o.textContent = `${c.learned ? "★" : "·"} ${c.input} (쇼츠 ${c.count})`;
    sel.appendChild(o);
  });
  if (prev) sel.value = prev;
  // 최초 1회, 선택된 채널의 저장된 가이드 목록 자동 표시
  if (!_guidesInit && sel.value) {
    _guidesInit = true;
    loadSavedGuides();
  }
}

// --- 이벤트 바인딩 ---
$("btn-build-sfx").onclick = async () => {
  const btn = $("btn-build-sfx");
  btn.disabled = true;
  $("sfx-log").textContent = "";
  try {
    const max = $("sfx-max").value ? parseInt($("sfx-max").value) : null;
    const { job_id } = await postJSON("/api/sfx/build", { max_items: max, probe: true });
    streamJob(job_id, $("sfx-log"), (job) => {
      btn.disabled = false;
      $("sfx-log").textContent += "\n[완료] " + JSON.stringify(job.result || job.error) + "\n";
      refreshStatus();
    });
  } catch (e) { $("sfx-log").textContent = "오류: " + e.message; btn.disabled = false; }
};

$("btn-learn").onclick = async () => {
  const channel = $("learn-channel").value.trim();
  if (!channel) { alert("채널을 입력하세요"); return; }
  const btn = $("btn-learn");
  btn.disabled = true;
  $("learn-log").textContent = "";
  try {
    const body = {
      channel,
      max_count: $("learn-max").value ? parseInt($("learn-max").value) : null,
      min_views: $("learn-minviews").value ? parseInt($("learn-minviews").value) : null,
      do_ocr: $("learn-ocr").checked,
      separate_vocals: $("learn-sep").checked,
      force: $("learn-force").checked,
    };
    const { job_id } = await postJSON("/api/learn", body);
    streamJob(job_id, $("learn-log"), (job) => {
      btn.disabled = false;
      if (job.status === "done" && job.result && job.result.report) {
        $("learn-log").textContent += "\n[학습 완료] 아래 '학습된 채널'에서 리포트를 확인하세요.\n";
      } else {
        $("learn-log").textContent += "\n[종료] " + (job.error || "") + "\n";
      }
      refreshStatus();
    });
  } catch (e) { $("learn-log").textContent = "오류: " + e.message; btn.disabled = false; }
};

$("btn-refresh").onclick = refreshStatus;

$("btn-view-report").onclick = async () => {
  const channel = $("channel-select").value;
  if (!channel) return;
  try {
    const { report } = await getJSON("/api/report?channel=" + encodeURIComponent(channel));
    $("report-view").innerHTML = mdToHtml(report);
  } catch (e) {
    $("report-view").textContent = "리포트를 불러올 수 없습니다: " + e.message;
  }
};

// --- 저장된 가이드 목록/조회 ---
function fmtDate(unixSec) {
  if (!unixSec) return "";
  const d = new Date(unixSec * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function loadSavedGuides() {
  const channel = $("channel-select").value;
  const list = $("saved-guides-list");
  $("saved-guide-result").innerHTML = "";
  $("saved-guide-downloads").innerHTML = "";
  $("saved-guide-revise").innerHTML = "";
  list.innerHTML = "";
  if (!channel) { $("saved-guides-msg").textContent = "먼저 채널을 선택하세요."; return; }
  $("saved-guides-msg").textContent = "불러오는 중...";
  try {
    const { guides } = await getJSON("/api/guides?channel=" + encodeURIComponent(channel));
    if (!guides.length) { $("saved-guides-msg").textContent = "저장된 가이드가 없습니다."; return; }
    $("saved-guides-msg").textContent = `${guides.length}개`;
    guides.forEach((g) => {
      const li = document.createElement("li");
      const dur = g.duration_sec ? ` · ${g.duration_sec}초` : "";
      const typeLabel = g.input_type === "script" ? "대본" : g.input_type === "topic" ? "주제" : "";
      let inputLine = "";
      if (g.input_text) {
        const preview = g.input_text.length > 60 ? g.input_text.slice(0, 60) + "…" : g.input_text;
        inputLine = `<div class="g-input">${typeLabel ? "[" + typeLabel + "] " : ""}${escapeHtml(preview)}</div>`;
      }
      const info = document.createElement("div");
      info.className = "g-info";
      info.innerHTML =
        `<div class="g-title">${escapeHtml(g.title)}</div>` +
        `<div class="g-meta">${fmtDate(g.created)}${dur}${g.hook ? " · 훅: " + escapeHtml(g.hook) : ""}</div>` +
        inputLine;
      info.onclick = () => openSavedGuide(channel, g.name, li);
      const del = document.createElement("button");
      del.className = "g-delete secondary";
      del.textContent = "삭제";
      del.onclick = (e) => { e.stopPropagation(); deleteSavedGuide(channel, g.name, g.title); };
      li.appendChild(info);
      /* shortly:local-script:start */
      addPersonalAssetAutomation(li, channel, g.name, true);
      /* shortly:local-script:end */
      li.appendChild(del);
      list.appendChild(li);
    });
  } catch (e) {
    $("saved-guides-msg").textContent = "목록 조회 실패: " + e.message;
  }
}

function escapeHtml(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function addCapcutExport(container, channel, name) {
  const wrap = document.createElement("div");
  wrap.className = "capcut-export-row";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "download capcut-export";
  btn.textContent = "CapCut 프로젝트로 내보내기";
  const status = document.createElement("span");
  status.className = "capcut-status";
  btn.onclick = async () => {
    btn.disabled = true;
    status.className = "capcut-status";
    const ttsReady = !!(
      _lastSettings
      && (_lastSettings.tts_enabled || (_lastSettings.keys_set || {}).typecast)
    );
    status.textContent = ttsReady
      ? "내보내는 중... (TTS 음성 생성 포함, 잠시 걸릴 수 있어요)"
      : "내보내는 중... (TTS 키 없음 → 자막만 내보냄. 설정에서 Typecast 키를 넣으세요)";
    try {
      const url = `/api/guide-export-capcut?channel=${encodeURIComponent(channel)}&name=${encodeURIComponent(name)}`;
      const r = await fetch(url, { method: "POST" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || r.statusText);
      status.className = "capcut-status ok";
      let msg = `완료! '${data.project_name}' 프로젝트 생성(자막 ${data.segments}개, 컷 이미지 ${data.cuts || 0}개)`;
      if (data.tts_used) {
        msg += `. TTS ${data.tts_generated || 0}개 생성`;
        if (data.tts_reused) msg += `, 재사용 ${data.tts_reused}개`;
        if (data.tts_failed) msg += `, 실패 ${data.tts_failed}개`;
      } else {
        msg += ". TTS 미사용(설정에 Typecast API 키 필요)";
      }
      if (data.tts_error) msg += ` · TTS 참고: ${data.tts_error}`;
      msg += ". CapCut을 껐다 켜면 목록에 나타납니다.";
      status.textContent = msg;
    } catch (e) {
      status.className = "capcut-status err";
      status.textContent = "실패: " + e.message;
    } finally {
      btn.disabled = false;
    }
  };
  wrap.appendChild(btn);
  wrap.appendChild(status);
  container.appendChild(wrap);
}

/* shortly:local-script:start */
function addPersonalAssetAutomation(container, channel, name, compact = false) {
  const link = document.createElement("a");
  link.className = compact ? "g-assets secondary" : "download";
  link.href =
    `/personal/assets?channel=${encodeURIComponent(channel)}` +
    `&name=${encodeURIComponent(name)}`;
  link.textContent = compact ? "에셋 자동화" : "생성 에셋 검토·자동 배치";
  link.onclick = (event) => event.stopPropagation();
  container.appendChild(link);
}
/* shortly:local-script:end */

function guideNameFromDir(dir) {
  return String(dir || "").split(/[\\/]/).filter(Boolean).pop() || "";
}

function qualityNotice(quality) {
  const story = (quality && quality.story_flow) || {};
  const evidence = (quality && quality.research_evidence) || {};
  const issues = story.issues_after_final_pass || [];
  if (issues.length) {
    return (
      `<div class="quality-notice warn"><b>흐름 확인 필요</b>` +
      `<span>${escapeHtml(issues.join(" · "))}</span></div>`
    );
  }
  if (evidence.status === "thin") {
    return (
      `<div class="quality-notice warn"><b>근거 자료가 부족합니다</b>` +
      `<span>대본은 생성했지만, 공개 검색에서 확인된 하드 팩트가 적습니다. ` +
      `중요한 주장과 수치는 내보내기 전에 직접 확인해 주세요.</span></div>`
    );
  }
  if (evidence.status === "strong") {
    return (
      `<div class="quality-notice ok"><b>자동 품질 점검 통과</b>` +
      `<span>이야기 흐름과 주요 출처 구성을 확인했습니다.</span></div>`
    );
  }
  return "";
}

async function openSavedGuide(channel, name, li) {
  document.querySelectorAll("#saved-guides-list li").forEach((el) => el.classList.remove("active"));
  if (li) li.classList.add("active");
  $("saved-guide-result").textContent = "불러오는 중...";
  $("saved-guide-downloads").innerHTML = "";
  $("saved-guide-revise").innerHTML = "";
  try {
    const url = `/api/guide-detail?channel=${encodeURIComponent(channel)}&name=${encodeURIComponent(name)}`;
    const g = await getJSON(url);
    let inputBlock = "";
    if (g.request && g.request.input_text) {
      const typeLabel = g.request.input_type === "script" ? "입력 대본" : "입력 주제";
      inputBlock =
        `<div class="g-input-orig"><div class="g-input-orig-label">${typeLabel}</div>` +
        `<div class="g-input-orig-text">${escapeHtml(g.request.input_text)}</div></div>`;
    }
    $("saved-guide-result").innerHTML =
      inputBlock + qualityNotice(g.quality) + mdToHtml(g.guide_md || "(내용 없음)");
    const dl = $("saved-guide-downloads");
    const mk = (label, path) => {
      const a = document.createElement("a");
      a.className = "download";
      a.href = "/api/download?path=" + encodeURIComponent(path);
      a.textContent = label;
      dl.appendChild(a);
    };
    if (g.output_dir) {
      mk("guide.md 다운로드", g.output_dir + "\\guide.md");
      mk("guide.json 다운로드", g.output_dir + "\\guide.json");
    }
    addCapcutExport(dl, channel, name);
    /* shortly:local-script:start */
    addPersonalAssetAutomation(dl, channel, name);
    /* shortly:local-script:end */
    addReviseForm($("saved-guide-revise"), channel, name);
  } catch (e) {
    $("saved-guide-result").textContent = "가이드를 불러올 수 없습니다: " + e.message;
  }
}

// 저장된 가이드 아래에 '대본 수정 요청' 폼을 렌더링합니다.
function addReviseForm(container, channel, name) {
  container.innerHTML = "";
  const box = document.createElement("div");
  box.className = "revise-panel";

  const label = document.createElement("div");
  label.className = "revise-label";
  label.textContent = "대본 수정 요청";
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent =
    "완성된 대본을 어떻게 바꾸고 싶은지 적어주세요. 원본은 그대로 두고 수정본이 새 가이드로 저장됩니다.";

  const ta = document.createElement("textarea");
  ta.rows = 3;
  ta.placeholder = "예: 도입부 훅을 더 자극적으로, 후반부는 근거를 한 줄 추가해줘";

  const row = document.createElement("div");
  row.className = "row";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = "수정본 생성";
  const status = document.createElement("span");
  status.className = "capcut-status";
  row.appendChild(btn);
  row.appendChild(status);

  const logEl = document.createElement("pre");
  logEl.className = "log";

  btn.onclick = async () => {
    const instruction = ta.value.trim();
    if (!instruction) { alert("수정 요청 내용을 입력하세요"); return; }
    btn.disabled = true;
    status.className = "capcut-status";
    status.textContent = "수정 중...";
    logEl.textContent = "";
    try {
      const { job_id } = await postJSON("/api/guide-revise", {
        channel, name, instruction,
      });
      streamJob(job_id, logEl, (job) => {
        btn.disabled = false;
        const r = job.result || {};
        if (r.ok && r.guide_md) {
          status.className = "capcut-status ok";
          status.textContent = "완료! 수정본이 새 가이드로 저장되었습니다.";
          $("saved-guide-result").innerHTML =
            qualityNotice(r.quality) + mdToHtml(r.guide_md);
          const gname = guideNameFromDir(r.output_dir);
          const dl = $("saved-guide-downloads");
          dl.innerHTML = "";
          const mk = (lbl, path) => {
            const a = document.createElement("a");
            a.className = "download";
            a.href = "/api/download?path=" + encodeURIComponent(path);
            a.textContent = lbl;
            dl.appendChild(a);
          };
          if (r.output_dir) {
            mk("guide.md 다운로드", r.output_dir + "\\guide.md");
            mk("guide.json 다운로드", r.output_dir + "\\guide.json");
          }
          if (gname) {
            addCapcutExport(dl, channel, gname);
            /* shortly:local-script:start */
            addPersonalAssetAutomation(dl, channel, gname);
            /* shortly:local-script:end */
          }
          loadSavedGuides();
        } else {
          status.className = "capcut-status err";
          status.textContent = "실패: " + (r.reason || job.error || "");
        }
      });
    } catch (e) {
      status.className = "capcut-status err";
      status.textContent = "실패: " + e.message;
      btn.disabled = false;
    }
  };

  box.appendChild(label);
  box.appendChild(hint);
  box.appendChild(ta);
  box.appendChild(row);
  box.appendChild(logEl);
  container.appendChild(box);
}

async function deleteSavedGuide(channel, name, title) {
  if (!confirm(`'${title}' 가이드를 정말 삭제하시겠습니까?\n삭제하면 되돌릴 수 없습니다.`)) return;
  try {
    const url = `/api/guide-detail?channel=${encodeURIComponent(channel)}&name=${encodeURIComponent(name)}`;
    const r = await fetch(url, { method: "DELETE" });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    $("saved-guide-result").innerHTML = "";
    $("saved-guide-downloads").innerHTML = "";
    await loadSavedGuides();
  } catch (e) {
    $("saved-guides-msg").textContent = "삭제 실패: " + e.message;
  }
}

$("btn-refresh-guides").onclick = loadSavedGuides;
$("channel-select").onchange = loadSavedGuides;

$("btn-guide").onclick = async () => {
  const channel = $("channel-select").value;
  const text = $("guide-text").value.trim();
  if (!channel) { alert("학습된 채널을 선택하세요"); return; }
  if (!text) { alert("주제 또는 대본을 입력하세요"); return; }
  const mode = document.querySelector('input[name="mode"]:checked').value;
  const btn = $("btn-guide");
  btn.disabled = true;
  $("guide-log").textContent = "";
  $("guide-result").innerHTML = "";
  $("guide-downloads").innerHTML = "";
  try {
    const { job_id } = await postJSON("/api/guide", {
      channel, text, is_script: mode === "script",
    });
    streamJob(job_id, $("guide-log"), (job) => {
      btn.disabled = false;
      const r = job.result || {};
      if (r.ok && r.guide_md) {
        $("guide-result").innerHTML =
          qualityNotice(r.quality) + mdToHtml(r.guide_md);
        const dl = $("guide-downloads");
        const mk = (label, path) => {
          const a = document.createElement("a");
          a.className = "download";
          a.href = "/api/download?path=" + encodeURIComponent(path);
          a.textContent = label;
          dl.appendChild(a);
        };
        if (r.output_dir) {
          mk("guide.md 다운로드", r.output_dir + "\\guide.md");
          mk("guide.json 다운로드", r.output_dir + "\\guide.json");
        }
        const gname = guideNameFromDir(r.output_dir);
        if (gname) {
          addCapcutExport(dl, channel, gname);
          /* shortly:local-script:start */
          addPersonalAssetAutomation(dl, channel, gname);
          /* shortly:local-script:end */
        }
        loadSavedGuides();
      } else {
        $("guide-result").textContent = "실패: " + (r.reason || job.error || "");
      }
    });
  } catch (e) { $("guide-log").textContent = "오류: " + e.message; btn.disabled = false; }
};

refreshStatus();
loadSettings();
