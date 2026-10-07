const el = (id) => document.getElementById(id);

const messagesEl = el("messages");
const composerEl = el("composer");
const inputEl = el("input");
const sendEl = el("send");
const suggestionsEl = el("suggestions");
const selectEl = el("dashboard-select");
const iframeEl = el("dashboard-iframe");
const placeholderEl = el("frame-placeholder");
const refreshEl = el("refresh");
const openMetabaseEl = el("open-metabase");

const state = {
  history: [],
  metabaseUrl: "",
  currentDashboardId: null,
  ready: false,
  busy: false,
};

// ── rendering ──────────────────────────────────────────────────────────

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

/**
 * Inline markdown the model actually emits — bold, italic, code.
 * Only ever applied to already-escaped text, so no markup can slip through.
 */
function inlineMarkdown(escaped) {
  return escaped
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

const BULLET = /^\s*[-*]\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;

/** Render the model's text as paragraphs and lists, preserving blank-line breaks. */
function paragraphs(text) {
  return text
    .split(/\n\s*\n/)
    .map((block) => {
      const lines = block.trim().split("\n");
      const bulleted = lines.every((l) => BULLET.test(l));
      const numbered = lines.every((l) => NUMBERED.test(l));

      if ((bulleted || numbered) && lines.length > 0) {
        const tag = numbered ? "ol" : "ul";
        const items = lines
          .map((l) => l.replace(bulleted ? BULLET : NUMBERED, ""))
          .map((l) => `<li>${inlineMarkdown(escapeHtml(l))}</li>`)
          .join("");
        return `<${tag}>${items}</${tag}>`;
      }

      return `<p>${inlineMarkdown(escapeHtml(block.trim())).replace(/\n/g, "<br>")}</p>`;
    })
    .join("");
}

function addMessage(role, html, { error = false } = {}) {
  const wrapper = document.createElement("div");
  wrapper.className = `message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = `bubble${error ? " error" : ""}`;
  bubble.innerHTML = html;
  wrapper.appendChild(bubble);
  messagesEl.appendChild(wrapper);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return wrapper;
}

function addTypingIndicator() {
  return addMessage(
    "assistant",
    '<span class="typing"><span></span><span></span><span></span></span>'
  );
}

function renderActions(actions) {
  if (!actions || actions.length === 0) return "";
  const labels = {
    saved_question: "Saved question",
    created_dashboard: "New dashboard",
    pinned_card: "Pinned to dashboard",
  };
  const chips = actions
    .map((a) => {
      const label = labels[a.kind] || a.kind;
      const text = escapeHtml(`${label}: ${a.name}`);
      return a.url
        ? `<a class="action-chip" href="${escapeHtml(a.url)}" target="_blank" rel="noopener">✓ ${text} ↗</a>`
        : `<span class="action-chip">✓ ${text}</span>`;
    })
    .join("");
  return `<div class="actions">${chips}</div>`;
}

function renderQueries(queries) {
  if (!queries || queries.length === 0) return "";
  const blocks = queries.map((q) => `<pre>${escapeHtml(q)}</pre>`).join("");
  const label = queries.length === 1 ? "View SQL" : `View SQL (${queries.length} queries)`;
  return `<details class="sql-toggle"><summary>${label}</summary>${blocks}</details>`;
}

// ── chat ───────────────────────────────────────────────────────────────

async function sendMessage(text) {
  if (state.busy || !text.trim()) return;
  state.busy = true;
  sendEl.disabled = true;
  inputEl.value = "";
  suggestionsEl.hidden = true;

  addMessage("user", paragraphs(text));
  const typing = addTypingIndicator();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: state.history }),
    });

    typing.remove();

    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      addMessage(
        "assistant",
        paragraphs(detail.detail || `Request failed (${response.status}).`),
        { error: true }
      );
      return;
    }

    const data = await response.json();
    addMessage(
      "assistant",
      paragraphs(data.answer) + renderActions(data.actions) + renderQueries(data.queries_run)
    );

    state.history.push({ role: "user", content: text });
    state.history.push({ role: "assistant", content: data.answer });
    // Keep the context window bounded — last 10 turns is plenty for follow-ups.
    if (state.history.length > 20) state.history = state.history.slice(-20);

    if (data.dashboard_changed) {
      await loadDashboards();
      reloadFrame();
    }
  } catch (err) {
    typing.remove();
    addMessage("assistant", paragraphs(`Something went wrong: ${err.message}`), {
      error: true,
    });
  } finally {
    state.busy = false;
    sendEl.disabled = false;
    inputEl.focus();
  }
}

composerEl.addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage(inputEl.value);
});

suggestionsEl.addEventListener("click", (e) => {
  const button = e.target.closest(".suggestion");
  if (button) sendMessage(button.textContent.trim());
});

// ── dashboard embedding ────────────────────────────────────────────────

function showPlaceholder(message, detail, link) {
  placeholderEl.innerHTML = `
    ${link ? "" : '<div class="spinner"></div>'}
    <p>${escapeHtml(message)}</p>
    ${detail ? `<small>${escapeHtml(detail)}</small>` : ""}
    ${link ? `<a href="${escapeHtml(link)}" target="_blank" rel="noopener">Open Metabase ↗</a>` : ""}
  `;
  placeholderEl.hidden = false;
  iframeEl.hidden = true;
}

async function showDashboard(dashboardId) {
  if (!dashboardId) return;
  state.currentDashboardId = dashboardId;
  openMetabaseEl.href = `${state.metabaseUrl}/dashboard/${dashboardId}`;

  try {
    const response = await fetch(`/api/embed/dashboard/${dashboardId}`);
    if (!response.ok) throw new Error(`status ${response.status}`);
    const data = await response.json();

    if (data.embedded && data.embed_url) {
      iframeEl.src = data.embed_url;
      iframeEl.hidden = false;
      placeholderEl.hidden = true;
    } else {
      showPlaceholder(
        "Embedding isn't enabled",
        "Set METABASE_EMBEDDING_SECRET in ai-analytics/.env to show dashboards inline.",
        data.fallback_url
      );
    }
  } catch (err) {
    showPlaceholder("Couldn't load the dashboard", err.message, `${state.metabaseUrl}`);
  }
}

function reloadFrame() {
  if (!iframeEl.hidden && iframeEl.src) {
    // Re-mint the token rather than just reloading, so an expired one heals.
    showDashboard(state.currentDashboardId);
  }
}

refreshEl.addEventListener("click", reloadFrame);

selectEl.addEventListener("change", () => {
  showDashboard(Number(selectEl.value));
});

async function loadDashboards() {
  try {
    const response = await fetch("/api/dashboards");
    if (!response.ok) return false;
    const data = await response.json();

    const previous = state.currentDashboardId;
    selectEl.innerHTML = "";
    for (const d of data.dashboards) {
      const option = document.createElement("option");
      option.value = d.id;
      option.textContent = d.name;
      selectEl.appendChild(option);
    }

    const target =
      previous && data.dashboards.some((d) => d.id === previous)
        ? previous
        : data.default_id || (data.dashboards[0] && data.dashboards[0].id);

    if (target) {
      selectEl.value = String(target);
      state.currentDashboardId = target;
    }
    return Boolean(target);
  } catch {
    return false;
  }
}

// ── status polling ─────────────────────────────────────────────────────

function setBadge(id, ok, text, pending = false) {
  const badge = el(id);
  badge.textContent = `● ${text}`;
  badge.className = `badge ${pending ? "badge-pending" : ok ? "badge-ok" : "badge-bad"}`;
}

async function pollStatus() {
  try {
    const response = await fetch("/api/status");
    const data = await response.json();

    state.metabaseUrl = data.metabase_url || "";
    openMetabaseEl.href = state.metabaseUrl;

    setBadge(
      "badge-llm",
      data.llm_configured,
      data.llm_configured
        ? `Using ${data.llm_provider} (${data.llm_model})`
        : "LLM API key not configured"
    );

    if (data.ready) {
      setBadge("badge-metabase", true, "Metabase connected");
      if (!state.ready) {
        state.ready = true;
        const found = await loadDashboards();
        if (found) showDashboard(state.currentDashboardId);
      }
      return true;
    }

    setBadge("badge-metabase", false, "Metabase starting…", true);
    if (data.error) {
      showPlaceholder("Waiting for Metabase…", data.error.slice(0, 200), "");
    }
    return false;
  } catch {
    setBadge("badge-metabase", false, "Backend unreachable");
    return false;
  }
}

async function waitForReady() {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (await pollStatus()) return;
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
}

waitForReady();
inputEl.focus();
