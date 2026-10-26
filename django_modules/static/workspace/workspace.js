(/* Initialize workspace state and register interface controls. */ () => {
  "use strict";

  const STORAGE_KEY = "multiagent_state_v1";
  const PREFERENCES_KEY = "multiagent_preferences_v1";
  const MAX_MODELS = 5;
  const AGENT_STATUS_INTERVAL = 2000;
  const $ = /* Find a document element by its ID. */ (id) => document.getElementById(id);
  const uid = /* Generate a UUID for idempotent API operations. */ () => globalThis.crypto?.randomUUID?.() || "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, /* Replace this UUID token with a random hexadecimal digit. */ (character) => {
    const random = crypto.getRandomValues(new Uint8Array(1))[0] & 15;
    return (character === "x" ? random : (random & 3) | 8).toString(16);
  });
  const node = /* Create an element and assign safe text content. */ (tag, className = "", text = "") => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    return element;
  };
  const icon = /* Create an icon from the local SVG sprite. */ (name) => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(svg.namespaceURI, "use");
    use.setAttribute("href", `${document.body.dataset.icons}#${name}`);
    svg.append(use);
    return svg;
  };
  document.querySelectorAll("[data-icon]").forEach(/* Apply this update to each matching element. */ (element) => element.replaceChildren(icon(element.dataset.icon)));

  /** Read a browser preference or legacy import without trusting malformed JSON. */

  function readBrowserValue(key) {
    try { const value = JSON.parse(localStorage.getItem(key) || "{}"); return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
    catch { return {}; }
  }
  const preferences = readBrowserValue(PREFERENCES_KEY);
  const state = { apis: [], models: [], chats: [], projects: [], master: null,
    projectId: preferences.projectId || null, database: "postgresql", nextOffset: null,
    theme: preferences.theme === "light" ? "light" : "dark",
    sidebarCollapsed: Boolean(preferences.sidebarCollapsed), agentsHidden: Boolean(preferences.agentsHidden),
    importId: preferences.importId || uid() };
  let currentChatId = preferences.activeChatId || null;
  let workspaceReady = false;
  let workspaceSequence = 0;
  let creatingChat = null;
  let preparingSend = false;
  let pollTimer;
  let agentStatusTimer;
  let agentStatusSequence = 0;
  const pending = new Map();
  const drafts = new Map();
  const dialogOrigins = new WeakMap();
  const mobile = matchMedia("(max-width: 760px)");
  const compact = matchMedia("(max-width: 1180px)");
  const workspace = $("workspace");
  let toastTimer;
  let storageWarningShown = false;

  /** Show a temporary notification with an optional recovery action. */

  function toast(message, action) {
    clearTimeout(toastTimer);
    $("toast").replaceChildren(document.createTextNode(message));
    if (action) {
      const button = node("button", "", action.label);
      button.onclick = /* Run the notification recovery action. */ () => { $("toast").hidden = true; Promise.resolve().then(action.run).catch(/* Display the asynchronous action error. */ (error) => toast(error.message)); };
      $("toast").append(button);
    }
    $("toast").hidden = false;
    toastTimer = setTimeout(/* Run the deferred UI update. */ () => { $("toast").hidden = true; }, action ? 8000 : 4500);
  }
  /** Save appearance and selection preferences; workspace records remain on the server. */
  function saveState() {
    const saved = { activeChatId: currentChatId, projectId: state.projectId, theme: state.theme,
      sidebarCollapsed: state.sidebarCollapsed, agentsHidden: state.agentsHidden, importId: state.importId };
    try { localStorage.setItem(PREFERENCES_KEY, JSON.stringify(saved)); return true; }
    catch {
      if (!storageWarningShown) {
        storageWarningShown = true;
        toast("Appearance preferences could not be saved in this browser. Workspace data is stored on the server.");
      }
      return false;
    }
  }
  const currentChat = /* Find the currently selected conversation. */ () => state.chats.find(/* Find the record matching this identifier. */ (chat) => chat.id === currentChatId);

  /** Call a same-origin JSON API with CSRF protection and readable errors. */

  async function apiRequest(path, { method = "GET", body, signal } = {}) {
    const headers = { "Accept": "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (method !== "GET") headers["X-CSRFToken"] = document.querySelector('[name="csrfmiddlewaretoken"]').value;
    let response;
    try { response = await fetch(path, { method, headers, signal, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body) }); }
    catch (error) { if (signal?.aborted) throw error; throw new Error("Could not reach the workspace server. Check your connection."); }
    let result;
    try { result = await response.json(); } catch { throw new Error(`The server returned an unreadable response (${response.status}).`); }
    if (!response.ok) {
      const fields = result.fields ? Object.values(result.fields).flat().join(" ") : "";
      const error = new Error(fields || result.error || result.data?.error || `Request failed (${response.status}).`);
      error.code = result.code; error.data = result.data; throw error;
    }
    return result.data;
  }

  /** Display a persistent load error with a retry or sign-in action. */

  function showWorkspaceError(error) {
    $("workspaceError").hidden = false; $("workspaceErrorText").textContent = error.message;
    $("retryWorkspaceBtn").textContent = error.code === "ACCESS_DENIED" ? "Sign in" : "Retry";
    $("retryWorkspaceBtn").onclick = /* Retry workspace loading or open administrator sign-in. */ () => error.code === "ACCESS_DENIED" ? location.assign("/admin/login/?next=/") : refreshWorkspace();
  }

  /** Load the database workspace while discarding obsolete responses. */

  async function refreshWorkspace() {
    workspaceReady = false;
    updateSend();
    const sequence = ++workspaceSequence;
    try {
      const result = await apiRequest(`/api/workspace/${state.projectId ? `?projectId=${encodeURIComponent(state.projectId)}` : ""}`);
      if (sequence !== workspaceSequence) return;
      const oldChats = new Map(state.chats.map(/* Convert each item to the required shape. */ (chat) => [chat.id, chat]));
      Object.assign(state, { apis: result.apis, models: result.models, master: result.master, projects: result.projects,
        projectId: result.projectId, database: result.database, nextOffset: result.chats.nextOffset });
      state.chats = result.chats.items.map(/* Convert each item to the required shape. */ (chat) => ({ messages: [], loaded: false, ...oldChats.get(chat.id), ...chat }));
      if (!state.chats.some(/* Check whether a matching item exists. */ (chat) => chat.id === currentChatId)) currentChatId = state.chats[0]?.id || null;
      workspaceReady = true; $("workspaceError").hidden = true;
      renderProjects(); renderChats(); renderAgents(); renderMessages(); saveState();
      $("importNotice").hidden = !hasLegacyData();
      if (currentChatId) await loadConversation(currentChatId);
    } catch (error) { showWorkspaceError(error); }
    finally { updateSend(); }
  }

  /** Load saved messages, older history, and pending response status. */

  async function loadConversation(id, before = null) {
    const chat = state.chats.find(/* Find the record matching this identifier. */ (item) => item.id === id); if (!chat) return;
    const sequence = (chat.loadSequence || 0) + 1; chat.loadSequence = sequence;
    const data = await apiRequest(`/api/conversations/${encodeURIComponent(id)}/${before ? `?before=${encodeURIComponent(before)}` : ""}`);
    if (chat.loadSequence !== sequence || !state.chats.includes(chat)) return;
    const oldHeight = $("chat").scrollHeight, oldTop = $("chat").scrollTop;
    const messages = before ? [...data.messages, ...chat.messages] : data.messages;
    Object.assign(chat, data, { messages, loaded: true });
    renderChats();
    if (currentChatId === id) {
      renderMessages();
      if (before) $("chat").scrollTop = $("chat").scrollHeight - oldHeight + oldTop;
      clearTimeout(pollTimer);
      if (chat.pendingRequest && !pending.has(id)) pollTimer = setTimeout(/* Run the deferred UI update. */ () => loadConversation(id).catch(showWorkspaceError), 1800);
    }
  }

  /** Refresh project selection and the visible database labels. */

  function renderProjects() {
    $("projectPicker").replaceChildren();
    if (!state.projects.length) $("projectPicker").append(new Option("Create a project", ""));
    for (const project of state.projects) $("projectPicker").append(new Option(project.name, project.id));
    $("projectPicker").value = state.projectId || "";
    const name = state.projects.find(/* Find the record matching this identifier. */ (project) => project.id === state.projectId)?.name || "Workspace";
    document.querySelectorAll("[data-project-name]").forEach(/* Apply this update to each matching element. */ (element) => { element.textContent = name; element.title = name; });
    $("loadMoreChats").hidden = state.nextOffset === null;
    $("storageLabel").textContent = state.database === "postgresql" ? "Saved in PostgreSQL" : "SQLite development mode";
  }

  $("projectPicker").onchange = /* Load the selected project and its saved conversations. */ () => { saveDraft(); clearTimeout(pollTimer); state.projectId = $("projectPicker").value; currentChatId = null; $("input").value = ""; refreshWorkspace(); };
  $("newProjectBtn").onclick = /* Open an empty project creation form. */ () => { $("projectForm").reset(); openDialog("projectDialog"); $("projectName").focus(); };
  $("projectForm").onsubmit = async /* Create a project in the database and select it. */ (event) => {
    event.preventDefault(); const button = $("projectForm").querySelector('[type="submit"]'); button.disabled = true;
    try {
      const project = await apiRequest("/api/projects/", { method: "POST", body: { name: $("projectName").value.trim(), requirements: $("projectRequirements").value.trim() } });
      state.projectId = project.id; currentChatId = null; $("projectDialog").close(); await refreshWorkspace(); toast("Project saved.");
    } catch (error) { toast(error.message); } finally { button.disabled = false; }
  };
  $("loadMoreChats").onclick = async /* Append the next page of saved conversations. */ () => {
    try {
      const data = await apiRequest(`/api/conversations/?projectId=${state.projectId}&offset=${state.nextOffset}`);
      state.chats.push(...data.items.filter(/* Keep records that match this condition. */ (item) => !state.chats.some(/* Check whether a matching item exists. */ (chat) => chat.id === item.id)).map(/* Convert each item to the required shape. */ (chat) => ({ ...chat, messages: [], loaded: false })));
      state.nextOffset = data.nextOffset; renderChats(); renderProjects();
    } catch (error) { toast(error.message); }
  };

  /** Check whether old browser records are available for import. */

  function hasLegacyData() {
    const legacy = readBrowserValue(STORAGE_KEY);
    return Boolean(legacy.master || [legacy.apis, legacy.models, legacy.chats].some(/* Check whether a matching item exists. */ (items) => Array.isArray(items) && items.length));
  }
  $("importBrowserBtn").onclick = async /* Import legacy browser data and retain it until the server confirms success. */ () => {
    $("importBrowserBtn").disabled = true;
    try {
      const legacy = readBrowserValue(STORAGE_KEY);
      const result = await apiRequest("/api/workspace/import/", { method: "POST", body: { id: state.importId, workspace: legacy, projectId: state.projectId } });
      if (JSON.stringify(readBrowserValue(STORAGE_KEY)) === JSON.stringify(legacy)) localStorage.removeItem(STORAGE_KEY);
      state.importId = uid(); saveState(); state.projectId = result.projectId || state.projectId;
      $("importNotice").hidden = true; await refreshWorkspace(); toast("Browser workspace imported. Data is now saved on the server.");
    } catch (error) { toast(error.message); } finally { $("importBrowserBtn").disabled = false; }
  };
  $("dismissImportBtn").onclick = /* Hide the import reminder without deleting browser data. */ () => { $("importNotice").hidden = true; };

  /** Apply the selected color theme and update the theme control. */

  function setTheme() {
    document.documentElement.dataset.theme = state.theme;
    $("themeBtn").replaceChildren(icon(state.theme === "dark" ? "sun" : "moon"));
    $("themeBtn").setAttribute("aria-label", `Switch to ${state.theme === "dark" ? "light" : "dark"} theme`);
  }
  $("themeBtn").onclick = /* Switch the color theme and save the appearance preference. */ () => { state.theme = state.theme === "dark" ? "light" : "dark"; setTheme(); saveState(); };

  /** Synchronize panel visibility, focus isolation, and accessibility attributes. */

  function syncPanels() {
    const sidebarOpen = mobile.matches && workspace.classList.contains("sidebar-open");
    const agentsOpen = compact.matches && workspace.classList.contains("agents-open");
    $("panelBackdrop").hidden = !(sidebarOpen || agentsOpen);
    document.querySelector(".workspace-body").inert = sidebarOpen;
    $("sidebar").inert = agentsOpen;
    document.querySelector(".conversation").inert = agentsOpen;
    document.querySelector(".topbar").inert = agentsOpen;
    document.querySelector(".statusbar").inert = agentsOpen;
    for (const [id, isModal] of [["sidebar", sidebarOpen], ["agentPanel", agentsOpen]]) {
      const panel = $(id);
      if (isModal) { panel.setAttribute("role", "dialog"); panel.setAttribute("aria-modal", "true"); }
      else { panel.removeAttribute("role"); panel.removeAttribute("aria-modal"); }
    }
    $("menuBtn").setAttribute("aria-expanded", String(mobile.matches ? sidebarOpen : !state.sidebarCollapsed));
    $("agentsBtn").setAttribute("aria-expanded", String(compact.matches ? agentsOpen : !state.agentsHidden));
    scheduleAgentStatus(true);
  }
  /** Close temporary navigation and agent overlays. */
  function closePanels() {
    workspace.classList.remove("sidebar-open", "agents-open");
    syncPanels();
  }
  /** Show or hide the agent panel for the current screen size. */
  function toggleAgents(force) {
    if (compact.matches) {
      const open = force ?? !workspace.classList.contains("agents-open");
      closePanels();
      workspace.classList.toggle("agents-open", open);
      syncPanels();
      if (open) $("closeAgents").focus();
    } else {
      state.agentsHidden = force === undefined ? !state.agentsHidden : !force;
      workspace.classList.toggle("agents-hidden", state.agentsHidden);
      syncPanels(); saveState();
    }
  }
  $("menuBtn").onclick = /* Reveal the sidebar in the current desktop or mobile layout. */ () => {
    if (mobile.matches) { closePanels(); workspace.classList.add("sidebar-open"); syncPanels(); $("collapseSidebar").focus(); }
    else { state.sidebarCollapsed = false; workspace.classList.remove("sidebar-collapsed"); syncPanels(); saveState(); }
  };
  $("collapseSidebar").onclick = /* Hide the sidebar and return focus to its toggle. */ () => {
    if (mobile.matches) closePanels();
    else { state.sidebarCollapsed = true; workspace.classList.add("sidebar-collapsed"); syncPanels(); saveState(); }
    $("menuBtn").focus();
  };
  $("agentsBtn").onclick = /* Toggle the agent team panel. */ () => toggleAgents();
  $("agentsNav").onclick = /* Open the agent team panel. */ () => toggleAgents(true);
  $("closeAgents").onclick = /* Close the agent panel and restore focus to its toggle. */ () => { toggleAgents(false); $("agentsBtn").focus(); };
  $("panelBackdrop").onclick = /* Dismiss temporary panels and focus the composer. */ () => { closePanels(); $("input").focus(); };
  mobile.addEventListener("change", closePanels);
  compact.addEventListener("change", /* Restart or stop status polling when the agent panel layout changes. */ () => scheduleAgentStatus(true));
  document.addEventListener("visibilitychange", /* Pause status polling while the browser tab is hidden. */ () => scheduleAgentStatus(true));
  compact.addEventListener("change", closePanels);

  /** Open a modal dialog and remember where to restore keyboard focus. */

  function openDialog(id) {
    const dialog = $(id);
    if (dialog.open) return;
    dialogOrigins.set(dialog, document.activeElement);
    closePanels();
    dialog.showModal();
  }
  document.querySelectorAll("[data-close-dialog]").forEach(/* Apply this update to each matching element. */ (button) => { button.onclick = /* Close the containing dialog. */ () => button.closest("dialog").close(); });
  document.querySelectorAll("dialog").forEach(/* Apply this update to each matching element. */ (dialog) => {
    dialog.addEventListener("click", /* Close the dialog when its backdrop is clicked. */ (event) => {
      const box = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) dialog.close();
    });
    dialog.addEventListener("close", /* Restore focus after the dialog closes. */ () => {
      const origin = dialogOrigins.get(dialog);
      if (origin?.isConnected && origin.getClientRects().length) origin.focus();
      else $("input").focus();
    });
  });

  const plans = { projectId: null, items: [], selectedId: null, selected: null, nextOffset: null,
    sequence: 0, pollTimer: null, submission: null, sending: false };

  /** Update planning controls to reflect generation and saved master configuration. */
  function syncPlanControls() {
    const generating = plans.sending || plans.items.some(/* Detect generation even while an older plan is selected. */ (plan) => plan.status === "GENERATING");
    $("generatePlanBtn").disabled = generating || !state.master;
    $("generatePlanBtn").textContent = generating ? "Generating plan..." : "Generate task plan";
    $("planGenerationStatus").textContent = generating ? "You can close this dialog; the saved attempt remains in history." : !state.master ? "Configure a master model in Settings first." : "";
    $("planResult").setAttribute("aria-busy", String(generating));
    $("morePlansBtn").hidden = plans.nextOffset === null;
  }

  /** Display a planning error without replacing other workspace state. */
  function showPlanError(error) {
    $("planError").textContent = error.message;
    $("planError").hidden = false;
  }

  /** Render saved task instructions and dependencies using safe text elements. */
  function renderPlan(plan) {
    plans.selected = plan; plans.selectedId = plan?.id || null;
    $("planResult").replaceChildren(); $("planError").hidden = true;
    clearTimeout(plans.pollTimer);
    if (!plan) {
      $("planResult").append(node("p", "record-empty", "Generate a plan to see its tasks and dependencies."));
    } else {
      const summary = node("div", "plan-summary");
      summary.append(node("h3", "", plan.title || "Plan request"), node("p", "", plan.summary || plan.request));
      summary.append(node("small", "", `${plan.modelName} · ${plan.status === "READY" ? `${plan.taskCount} saved tasks · Execution not started` : plan.status === "GENERATING" ? "Generating" : "Generation failed"}`));
      $("planResult").append(summary);
      if (plan.error) showPlanError(new Error(plan.error));
      for (const task of plan.tasks || []) {
        const details = node("details", "plan-task"), heading = node("summary", "", `${task.key} — ${task.title}`);
        heading.append(node("small", "", `${task.type} · ${task.status}`)); details.append(heading);
        details.append(node("p", "", task.instructions));
        details.append(node("h4", "", "Dependencies"), node("p", "", task.dependsOn.length ? task.dependsOn.join(", ") : "None — can start when execution is available."));
        details.append(node("h4", "", "Required capabilities"), node("p", "", task.requiredCapabilities.join(", ")));
        if (task.capabilityWeights) details.append(node("h4", "", "Capability weights"), node("p", "", Object.entries(task.capabilityWeights).map(/* Format each stored capability weight as a percentage. */ ([name, weight]) => `${name}: ${Math.round(weight * 100)}%`).join(", ")));
        details.append(node("h4", "", "Expected output"), node("p", "", task.expectedOutput));
        const criteria = node("ul");
        for (const criterion of task.acceptanceCriteria) criteria.append(node("li", "", criterion));
        details.append(node("h4", "", "Acceptance criteria"), criteria);
        $("planResult").append(details);
      }
      if (plans.items.some(/* Keep history current until all visible generation attempts have finished. */ (item) => item.status === "GENERATING") && $("plansDialog").open) {
        plans.pollTimer = setTimeout(/* Reload persisted generation state after a page reload or another client submission. */ () => loadPlanHistory().catch(showPlanError), 1800);
      }
    }
    syncPlanControls();
  }

  /** Fetch project plan history and its selection while discarding obsolete responses. */
  async function loadPlanHistory(append = false, selectedId = plans.selectedId) {
    const sequence = ++plans.sequence, projectId = plans.projectId;
    clearTimeout(plans.pollTimer);
    const offset = append && plans.nextOffset !== null ? plans.nextOffset : 0;
    const data = await apiRequest(`/api/projects/${projectId}/plans/?offset=${offset}`);
    if (sequence !== plans.sequence || !$("plansDialog").open) return;
    plans.items = append ? [...plans.items, ...data.items] : data.items;
    plans.nextOffset = data.nextOffset;
    $("planPicker").replaceChildren();
    for (const plan of plans.items) $("planPicker").append(new Option(`${plan.title || plan.request.slice(0, 60)} · ${plan.status === "READY" ? "Saved" : plan.status === "FAILED" ? "Failed" : "Generating"}`, plan.id));
    if (!plans.items.length) $("planPicker").append(new Option("No saved plans", ""));
    const id = plans.items.some(/* Preserve the selected plan if it is present in this history page. */ (plan) => plan.id === selectedId) ? selectedId : plans.items[0]?.id;
    $("planPicker").value = id || "";
    const plan = id ? await apiRequest(`/api/projects/${projectId}/plans/${id}/`) : null;
    if (sequence === plans.sequence && $("plansDialog").open) renderPlan(plan);
  }

  $("taskPlansBtn").onclick = async /* Open saved planning for the currently selected project. */ () => {
    if (!workspaceReady) { toast("Wait for the workspace to connect."); return; }
    if (!state.projectId) { $("newProjectBtn").click(); return; }
    if (plans.projectId !== state.projectId) {
      plans.projectId = state.projectId; plans.items = []; plans.selectedId = null; plans.selected = null;
      $("planRequest").value = $("input").value.slice(0, 8000);
      $("planPicker").replaceChildren(new Option("Loading saved plans...", ""));
      renderPlan(null);
    }
    const project = state.projects.find(/* Find the name of the project whose plan history is opening. */ (item) => item.id === plans.projectId);
    $("planProjectName").textContent = project?.name || "Current project";
    openDialog("plansDialog"); syncPlanControls();
    try { await loadPlanHistory(); } catch (error) { showPlanError(error); }
  };
  $("planPicker").onchange = async /* Load the selected saved plan and its dependency details. */ () => {
    const id = $("planPicker").value, sequence = ++plans.sequence, projectId = plans.projectId;
    clearTimeout(plans.pollTimer);
    if (!id) return;
    try {
      const plan = await apiRequest(`/api/projects/${projectId}/plans/${id}/`);
      if (sequence === plans.sequence && $("plansDialog").open) renderPlan(plan);
    } catch (error) { if (sequence === plans.sequence) showPlanError(error); }
  };
  $("refreshPlansBtn").onclick = /* Refresh persisted plan history and recover interrupted requests. */ () => loadPlanHistory().catch(showPlanError);
  $("morePlansBtn").onclick = /* Append an older page of saved plans. */ () => loadPlanHistory(true).catch(showPlanError);
  $("plansDialog").addEventListener("close", /* Stop browser polling while server generation continues independently. */ () => { ++plans.sequence; clearTimeout(plans.pollTimer); });
  $("planForm").onsubmit = async /* Generate a validated plan, preserving the request ID across uncertain network retries. */ (event) => {
    event.preventDefault();
    if (plans.sending || plans.items.some(/* Prevent another submission while this project has a generating plan. */ (plan) => plan.status === "GENERATING")) return;
    const projectId = plans.projectId, request = $("planRequest").value.trim();
    if (!request) return;
    if (!plans.submission || plans.submission.projectId !== projectId || plans.submission.request !== request) plans.submission = { id: uid(), projectId, request };
    const submission = plans.submission;
    plans.sending = true; $("planError").hidden = true; syncPlanControls();
    try {
      const plan = await apiRequest(`/api/projects/${projectId}/plans/`, { method: "POST", body: { id: submission.id, request } });
      plans.submission = null;
      if ($("plansDialog").open && plans.projectId === projectId) await loadPlanHistory(false, plan.id);
      toast(plan.status === "READY" ? "Task plan saved. Execution has not started." : "Plan generation is still in progress.");
    } catch (error) {
      if (error.data?.id) plans.submission = null;
      if ($("plansDialog").open && plans.projectId === projectId) {
        if (error.data?.id) {
          try { await loadPlanHistory(false, error.data.id); } catch (loadError) { showPlanError(loadError); }
        }
        showPlanError(error);
      } else toast(error.message);
    } finally { plans.sending = false; syncPlanControls(); }
  };

  /** Keep the current unsent message in memory for this conversation. */

  function saveDraft() { drafts.set(currentChatId || "new", $("input").value); }
  /** Select a conversation, restore its draft, and load its saved messages. */
  async function selectChat(id) {
    saveDraft(); currentChatId = id;
    $("input").value = drafts.get(id || "new") || "";
    closePanels(); saveState(); renderChats(); renderMessages(); resizeComposer();
    $("input").focus();
    clearTimeout(pollTimer);
    if (id) { try { await loadConversation(id); } catch (error) { showWorkspaceError(error); } }
  }
  /** Create a server conversation or reuse the current empty one. */
  async function createChat() {
    if (!workspaceReady) return null;
    if (!state.projectId) { $("newProjectBtn").click(); return null; }
    if (creatingChat) return creatingChat;
    saveDraft();
    const existing = currentChat();
    if (existing?.loaded && !existing.messages.length && !pending.has(existing.id)) { closePanels(); $("input").focus(); return existing; }
    creatingChat = (async /* Run this callback for the current interface operation. */ () => {
      const projectId = state.projectId;
      const saved = await apiRequest("/api/conversations/", { method: "POST", body: { id: uid(), projectId, title: "New chat" } });
      if (state.projectId !== projectId) return null;
      const chat = { ...saved, messages: [], loaded: true }; state.chats.unshift(chat); await selectChat(chat.id); return chat;
    })();
    try { return await creatingChat; } catch (error) { toast(error.message); return null; }
    finally { creatingChat = null; }
  }
  /** Soft-delete a saved conversation and offer a server-backed undo. */
  async function deleteChat(chat) {
    try {
      await apiRequest(`/api/conversations/${chat.id}/`, { method: "DELETE" });
      pending.get(chat.id)?.controller.abort();
      state.chats = state.chats.filter(/* Keep records that match this condition. */ (item) => item.id !== chat.id);
      if (currentChatId === chat.id) await selectChat(state.chats[0]?.id || null);
      saveState(); renderChats(); renderMessages(); resizeComposer();
      toast("Chat deleted.", { label: "Undo", run: async /* Run this callback for the current interface operation. */ () => {
        await apiRequest(`/api/conversations/${chat.id}/`, { method: "PATCH", body: { deleted: false } });
        if (!state.chats.some(/* Check whether a matching item exists. */ (item) => item.id === chat.id)) state.chats.unshift(chat);
        await selectChat(chat.id);
      } });
    } catch (error) { toast(error.message); }
  }
  /** Render conversation navigation and the selected chat title. */
  function renderChats() {
    const list = $("chatList"); list.replaceChildren();
    $("chatCount").textContent = String(state.chats.length);
    $("chatTitle").textContent = currentChat()?.title || "New chat";
    document.title = `${currentChat()?.title || "New chat"} · Multi-Agent`;
    if (!state.chats.length) list.append(node("p", "history-empty", workspaceReady ? "Your conversations will appear here." : "Loading workspace..."));
    for (const chat of state.chats) {
      const row = node("div", `chat-item${chat.id === currentChatId ? " active" : ""}${pending.has(chat.id) ? " pending" : ""}`);
      const select = node("button", "chat-select", chat.title);
      select.title = chat.title;
      if (chat.id === currentChatId) select.setAttribute("aria-current", "page");
      select.onclick = /* Open this saved conversation. */ () => selectChat(chat.id);
      const remove = node("button", "icon-button"); remove.append(icon("trash"));
      remove.setAttribute("aria-label", `Delete chat: ${chat.title}`); remove.title = "Delete chat";
      remove.onclick = /* Delete this conversation with an undo option. */ () => deleteChat(chat);
      row.append(select, remove); list.append(row);
    }
  }

  /** Render basic inline formatting without executing model-provided HTML. */

  function inlineText(container, text) {
    const parts = text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g);
    for (const part of parts) {
      if (part.startsWith("`") && part.endsWith("`")) container.append(node("code", "", part.slice(1, -1)));
      else if (part.startsWith("**") && part.endsWith("**")) container.append(node("strong", "", part.slice(2, -2)));
      else container.append(document.createTextNode(part));
    }
  }
  /** Copy text and report whether clipboard access succeeded. */
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied to clipboard."); }
    catch { toast("Clipboard access is unavailable. Select the text to copy it."); }
  }
  /** Render safe headings, lists, paragraphs, and fenced code blocks. */
  function renderMarkdown(container, text) {
    const lines = text.replace(/\r\n/g, "\n").split("\n");
    let paragraph = [], list = null;
    const flush = /* Append buffered paragraph text and reset list state. */ () => {
      if (paragraph.length) { const p = node("p"); inlineText(p, paragraph.join("\n")); container.append(p); paragraph = []; }
      list = null;
    };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (/^\s*```/.test(line)) {
        flush();
        const language = line.replace(/^\s*```/, "").trim() || "code";
        const codeLines = [];
        while (++i < lines.length && !/^\s*```/.test(lines[i])) codeLines.push(lines[i]);
        const block = node("div", "code-block"), heading = node("div", "code-heading"), copy = node("button", "", "Copy");
        copy.type = "button"; copy.prepend(icon("copy")); copy.onclick = /* Copy the displayed code block. */ () => copyText(codeLines.join("\n"));
        heading.append(node("span", "", language), copy);
        const pre = node("pre"); pre.append(node("code", "", codeLines.join("\n")));
        block.append(heading, pre); container.append(block);
      } else if (/^#{1,6}\s/.test(line)) {
        flush(); const level = Math.max(3, Math.min(line.match(/^#+/)[0].length, 5));
        const heading = node(`h${level}`); inlineText(heading, line.replace(/^#+\s+/, "")); container.append(heading);
      } else if (/^\s*(?:[-*]|\d+\.)\s+/.test(line)) {
        if (paragraph.length) flush();
        const tag = /^\s*\d+\./.test(line) ? "ol" : "ul";
        if (!list || list.tagName.toLowerCase() !== tag) { list = node(tag); container.append(list); }
        const item = node("li"); inlineText(item, line.replace(/^\s*(?:[-*]|\d+\.)\s+/, "")); list.append(item);
      } else if (!line.trim()) flush();
      else { list = null; paragraph.push(line); }
    }
    flush();
  }
  /** Build an assistant message with attribution and copy controls. */
  function agentMessage(message, isPending = false) {
    const article = node("article", `message agent${message.error ? " error" : ""}`);
    const header = node("div", "message-header"), avatar = node("span", "message-avatar");
    avatar.append(icon("command"));
    header.append(avatar, node("strong", "", message.agentName || "Master"), node("span", "model-label", message.category || ""));
    if (!isPending) {
      const copy = node("button", "icon-button"); copy.append(icon("copy")); copy.setAttribute("aria-label", "Copy response");
      copy.onclick = /* Copy this assistant response. */ () => copyText(message.text); header.append(copy);
    }
    const body = node("div", "message-body");
    if (isPending) body.append(node("div", "pending-copy", "Thinking through your request..."));
    else renderMarkdown(body, message.text);
    article.append(header, body); return article;
  }
  /** Render the active conversation and its loading or pending state. */
  function renderMessages() {
    const chat = currentChat(), messages = $("messages"); messages.replaceChildren();
    $("empty").hidden = Boolean(chat?.messages.length || pending.has(chat?.id) || chat?.pendingRequest || (chat && !chat.loaded));
    $("olderMessagesBtn").hidden = !chat?.olderCursor;
    if (chat && !chat.loaded) messages.append(node("p", "pending-copy", "Loading conversation..."));
    for (const message of chat?.messages || []) {
      if (message.role === "user") messages.append(node("article", "message user", message.text));
      else messages.append(agentMessage(message));
    }
    const request = pending.get(chat?.id);
    if (request) messages.append(agentMessage({ agentName: request.master.name, category: request.master.modelName }, true));
    else if (chat?.pendingRequest) messages.append(agentMessage({ agentName: chat.pendingRequest.agentName, category: chat.pendingRequest.modelName }, true));
    $("chat").scrollTop = $("chat").scrollHeight;
    updateSend();
  }
  /** Enable sending or stopping only when the workspace state permits it. */
  function updateSend() {
    const isPending = pending.has(currentChatId);
    $("send").disabled = !isPending && (!workspaceReady || !state.master || !state.projectId || currentChat()?.pendingRequest || !$("input").value.trim());
    $("send").classList.toggle("stopping", isPending);
    $("send").setAttribute("aria-label", isPending ? "Stop receiving response" : "Send message");
    $("send").title = isPending ? "Stop receiving response" : "Send message";
    $("send").replaceChildren(icon(isPending ? "square" : "arrow-up"));
  }
  $("olderMessagesBtn").onclick = async /* Prepend the previous page of saved messages. */ () => {
    const chat = currentChat(); if (!chat?.olderCursor) return;
    try { await loadConversation(chat.id, chat.olderCursor); } catch (error) { toast(error.message); }
  };
  /** Grow the composer to fit its text within the height limit. */
  function resizeComposer() {
    $("input").style.height = "auto";
    $("input").style.height = `${Math.min($("input").scrollHeight, 190)}px`;
    updateSend();
  }
  $("newChatBtn").onclick = createChat;
  $("input").addEventListener("input", /* Keep the draft and resize the composer. */ () => { saveDraft(); resizeComposer(); });
  document.querySelectorAll("[data-prompt]").forEach(/* Apply this update to each matching element. */ (button) => {
    button.onclick = /* Insert this prompt starter into the composer. */ () => { $("input").value = button.dataset.prompt; saveDraft(); resizeComposer(); $("input").focus(); };
  });
  $("input").addEventListener("keydown", /* Send on Enter while preserving newlines and composition. */ (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!pending.has(currentChatId) && $("input").value.trim()) $("form").requestSubmit();
    }
  });
  $("form").addEventListener("submit", async /* Send a persisted message or stop receiving its response. */ (event) => {
    event.preventDefault();
    if (pending.has(currentChatId)) { pending.get(currentChatId).controller.abort(); return; }
    const prompt = $("input").value.trim(); if (!prompt) return;
    if (!workspaceReady || !state.master) { toast("Configure the master model before sending a message."); return; }
    if (preparingSend) return;
    preparingSend = true;
    const chat = currentChat() || await createChat();
    preparingSend = false;
    if (!chat) return;
    const master = { ...state.master };
    const request = { controller: new AbortController(), master, timedOut: false };
    const timer = setTimeout(/* Run the deferred UI update. */ () => { request.timedOut = true; request.controller.abort(); }, 190000);
    chat.messages.push({ id: uid(), role: "user", text: prompt });
    if (chat.title === "New chat") chat.title = prompt.slice(0, 45);
    pending.set(chat.id, request); drafts.set(chat.id, ""); $("input").value = "";
    saveState(); renderChats(); renderMessages(); resizeComposer();
    try {
      await apiRequest("/api/chat/", { method: "POST", signal: request.controller.signal,
        body: { prompt, conversationId: chat.id, requestId: uid() } });
    } catch (error) {
      if (state.chats.includes(chat)) {
        const text = request.controller.signal.aborted
          ? request.timedOut ? "The response timed out. Reload the conversation to check its saved status." : "Stopped receiving. The server will save the response when generation finishes."
          : error.message || "Could not reach the server. Check the connection and try again.";
        toast(text);
      }
    } finally {
      clearTimeout(timer); pending.delete(chat.id); saveState(); renderChats();
      try { if (state.chats.includes(chat)) await loadConversation(chat.id); } catch (error) { showWorkspaceError(error); }
      if (currentChatId === chat.id) { renderMessages(); $("input").focus(); }
    }
  });

  const roleNames = { GENERAL: "General", FRONTEND: "Frontend", BACKEND: "Backend", DATABASE: "Database", TESTING: "Testing", REVIEW: "Code review", INTEGRATION: "Integration" };
  /** Build a model summary without claiming live connectivity. */
  function agentContents(model, isMaster = false) {
    const configured = Boolean(model);
    model = model || { name: "Choose a master model", modelName: "Not configured" };
    const content = document.createDocumentFragment(), identity = node("div", "agent-identity"), avatar = node("span", "agent-avatar"), details = node("div");
    avatar.append(icon(isMaster ? "command" : "code"));
    const nameRow = node("div", "agent-name-row");
    nameRow.append(node("span", "agent-name", model.name || "Unnamed specialist"));
    if (!isMaster) nameRow.append(agentStatus(model));
    details.append(nameRow, node("span", "agent-model", model.modelName || "Choose a model"));
    identity.append(avatar, details);
    const meta = node("div", "agent-meta"), api = state.apis.find(/* Find the record matching this identifier. */ (item) => item.id === model.apiId);
    meta.append(node("span", "role-label", isMaster ? "Master" : roleNames[model.role] || "General"), node("span", "", !configured ? "Setup required" : api?.name || (isMaster ? "Local endpoint" : "No connection")));
    content.append(identity, meta); return content;
  }
  /** Describe the time since an agent's last heartbeat as reported by the server. */
  function lastSeenText(seconds) {
    if (seconds === null || seconds === undefined) return "Last seen: Never";
    const [value, unit] = seconds < 60 ? [seconds, "second"] : seconds < 3600 ? [Math.floor(seconds / 60), "minute"] : seconds < 86400 ? [Math.floor(seconds / 3600), "hour"] : [Math.floor(seconds / 86400), "day"];
    return `Last seen: ${value} ${unit}${value === 1 ? "" : "s"} ago`;
  }
  /** Show the server's heartbeat verdict on a status indicator; the label never relies on color alone. */
  function applyAgentStatus(element, model) {
    element.classList.toggle("online", Boolean(model.online));
    element.title = lastSeenText(model.lastSeenSeconds);
    element.querySelector(".agent-status-label").textContent = model.online ? "Online" : "Offline";
  }
  /** Build a specialist's heartbeat indicator with a decorative dot and a text label. */
  function agentStatus(model) {
    const element = node("span", "agent-status"); element.dataset.agentStatus = model.id || "";
    const dot = node("span", "agent-status-dot"); dot.setAttribute("aria-hidden", "true");
    element.append(dot, node("span", "agent-status-label"));
    applyAgentStatus(element, model); return element;
  }
  /** Summarize live status polling in the agent panel footer. */
  function renderLiveStatus(mode = "live") {
    const online = state.models.filter(/* Keep specialists the server reports online. */ (model) => model.online).length;
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    $("liveStatusDot").className = `status-dot${mode === "live" ? " live" : mode === "failed" ? " failed" : ""}`;
    $("liveStatusText").textContent = mode === "failed" ? `Live status unavailable · retrying every ${AGENT_STATUS_INTERVAL / 1000}s`
      : mode === "paused" ? "Live status paused while hidden"
      : `Live · ${online} of ${state.models.length} specialist${state.models.length === 1 ? "" : "s"} online · updated ${time}`;
  }
  /** Report whether the agent panel is currently visible to the user. */
  function agentsVisible() {
    return document.visibilityState === "visible" && (compact.matches ? workspace.classList.contains("agents-open") : !state.agentsHidden);
  }
  /** Poll specialist status while the agent panel is visible, and stop polling otherwise. */
  function scheduleAgentStatus(immediate = false) {
    clearTimeout(agentStatusTimer);
    if (!workspaceReady || !agentsVisible()) { agentStatusSequence++; if (workspaceReady) renderLiveStatus("paused"); return; }
    agentStatusTimer = setTimeout(refreshAgentStatus, immediate ? 0 : AGENT_STATUS_INTERVAL);
  }
  /** Fetch current specialist status and update the indicators without rebuilding the panel. */
  async function refreshAgentStatus() {
    const sequence = ++agentStatusSequence;
    try {
      const agents = await apiRequest("/api/agents/");
      if (sequence !== agentStatusSequence) return;
      const latest = new Map(agents.map(/* Index each specialist by its identifier. */ (agent) => [agent.id, agent]));
      for (const model of state.models) {
        const fresh = latest.get(model.id);
        if (fresh) Object.assign(model, { status: fresh.status, online: fresh.online, lastSeenSeconds: fresh.lastSeenSeconds, lastHeartbeat: fresh.lastHeartbeat });
      }
      $("specialistList").querySelectorAll("[data-agent-status]").forEach(/* Update this indicator from the latest status. */ (element) => {
        const model = state.models.find(/* Find the record matching this identifier. */ (item) => item.id === element.dataset.agentStatus);
        if (model) applyAgentStatus(element, model);
      });
      renderLiveStatus();
    } catch { if (sequence === agentStatusSequence) renderLiveStatus("failed"); }
    finally { if (sequence === agentStatusSequence) scheduleAgentStatus(); }
  }
  /** Refresh the master, specialists, and saved connection counts. */
  function renderAgents() {
    $("masterAgent").replaceChildren(agentContents(state.master, true));
    $("masterAgent").setAttribute("aria-label", `Configure master model: ${state.master?.name || "Not configured"}`);
    $("composerModel").textContent = state.master?.modelName || "Configure a model";
    $("composerModel").title = state.master?.modelName || "Open master model settings";
    $("teamSummary").textContent = `${state.master ? "1 master" : "No master configured"} · ${state.models.length} specialist${state.models.length === 1 ? "" : "s"}`;
    $("specialistCount").textContent = `${state.models.length} / ${MAX_MODELS}`;
    $("navAgentCount").textContent = String(state.models.length + (state.master ? 1 : 0));
    $("connectionStatus").textContent = `${state.database === "postgresql" ? "PostgreSQL" : "SQLite"} · ${state.apis.filter(/* Keep records that match this condition. */ (api) => !api.isDraft).length} API connections`;
    $("specialistList").replaceChildren();
    if (!state.models.length) {
      const empty = node("div", "specialist-empty");
      empty.append(node("strong", "", "A place for every specialty."), document.createTextNode("Add frontend, backend, database, or testing models to your team."));
      $("specialistList").append(empty);
    }
    for (const model of state.models) {
      const button = node("button", "specialist-agent"); button.append(agentContents(model));
      button.onclick = /* Open this specialist configuration. */ () => { model.collapsed = false; openSettings("models"); };
      $("specialistList").append(button);
    }
    $("addSpecialistBtn").disabled = state.models.length >= MAX_MODELS;
    if (workspaceReady) renderLiveStatus();
    updateSend(); scheduleAgentStatus();
  }
  /** Populate a connection selector with saved endpoints only. */
  function connectionOptions(select, selected, local = false) {
    select.replaceChildren(new Option(local ? "Default local endpoint" : "Choose an API connection", ""));
    for (const api of state.apis.filter(/* Keep records that match this condition. */ (item) => !item.isDraft)) select.append(new Option(api.name || "Unnamed connection", api.id));
    select.value = selected || "";
  }
  /** Load the persisted master configuration into its form. */
  function renderMasterForm() {
    $("masterName").value = state.master?.name || "Master";
    $("masterIdentifier").value = state.master?.modelName || "";
    connectionOptions($("masterConnection"), state.master?.apiId, true);
    $("removeMasterBtn").hidden = !state.master;
  }
  /** Select a settings section and update keyboard tab behavior. */
  function setSettingsTab(name, focus = false) {
    document.querySelectorAll("[data-settings-tab]").forEach(/* Apply this update to each matching element. */ (tab) => {
      const active = tab.dataset.settingsTab === name;
      tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1;
      $(`settings-${tab.dataset.settingsTab}`).hidden = !active;
      if (active && focus) tab.focus();
    });
  }
  /** Refresh configuration forms and open the requested settings section. */
  function openSettings(tab = "master") {
    renderMasterForm(); renderRecords("apis"); renderRecords("models");
    setSettingsTab(tab); openDialog("settingsDialog");
  }
  $("settingsBtn").onclick = /* Open master model settings. */ () => openSettings();
  $("masterPicker").onclick = /* Open configuration for the selected master. */ () => openSettings();
  $("masterAgent").onclick = /* Open configuration for the master agent card. */ () => openSettings();
  $("addSpecialistBtn").onclick = /* Create a specialist draft and open its settings. */ () => { addRecord("models"); openSettings("models"); };
  document.querySelectorAll("[data-settings-tab]").forEach(/* Apply this update to each matching element. */ (tab) => {
    tab.onclick = /* Select this settings section. */ () => setSettingsTab(tab.dataset.settingsTab);
    tab.addEventListener("keydown", /* Move keyboard focus between settings tabs. */ (event) => {
      const tabs = [...document.querySelectorAll("[data-settings-tab]")], index = tabs.indexOf(tab);
      const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index - 1 + tabs.length) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
      if (next >= 0) { event.preventDefault(); setSettingsTab(tabs[next].dataset.settingsTab, true); }
    });
  });
  $("masterForm").onsubmit = async /* Save the master model through the workspace API. */ (event) => {
    event.preventDefault();
    if (!$("masterName").value.trim() || !$("masterIdentifier").value.trim()) { toast("Enter a name and model identifier."); return; }
    const button = $("masterForm").querySelector('[type="submit"]'); button.disabled = true;
    try {
      state.master = await apiRequest("/api/master/", { method: "PUT", body: { name: $("masterName").value.trim(), modelName: $("masterIdentifier").value.trim(), apiId: $("masterConnection").value } });
      toast("Master model saved to the database."); renderAgents(); renderMasterForm();
    } catch (error) { toast(error.message); } finally { button.disabled = false; }
  };
  $("removeMasterBtn").onclick = async /* Remove the saved master configuration. */ () => {
    try { await apiRequest("/api/master/", { method: "DELETE" }); state.master = null; renderAgents(); renderMasterForm(); toast("Master configuration removed."); }
    catch (error) { toast(error.message); }
  };
  /** Create a labelled configuration input with its validation attributes. */
  function addField(form, label, name, value, options = {}) {
    const id = `${form.id}-${name}`, caption = node("label", "", label);
    caption.htmlFor = id;
    const field = node(options.select ? "select" : "input");
    field.id = id; field.name = name;
    if (!options.select) { field.type = options.type || "text"; field.maxLength = options.type === "password" ? 4096 : name === "url" ? 500 : 100; field.placeholder = options.placeholder || ""; }
    field.required = Boolean(options.required);
    if (name === "key") field.autocomplete = "off";
    field.value = value || ""; form.append(caption, field); return field;
  }
  /** Render editable endpoint or specialist records and their save actions. */
  function renderRecords(kind) {
    const isApi = kind === "apis", container = $(isApi ? "apiList" : "modelList");
    container.replaceChildren();
    if (!state[kind].length) container.append(node("p", "record-empty", isApi ? "No API connections yet. The master uses the default local endpoint." : "No specialists yet. Add a model to get started."));
    for (const record of state[kind]) {
      const card = node("div", "record");
      const collapsed = record.collapsed ?? Boolean(record.name || record.url || record.modelName);
      if (collapsed) {
        const summary = node("div", "record-summary"), details = node("div");
        details.append(node("strong", "", record.name || (isApi ? "Unnamed connection" : "Unnamed specialist")), node("small", "", (isApi ? record.url : record.modelName) || "Needs setup"));
        const edit = node("button", "icon-button"), remove = node("button", "icon-button");
        edit.append(icon("pencil")); remove.append(icon("trash"));
        edit.setAttribute("aria-label", `Edit ${record.name || (isApi ? "connection" : "specialist")}`);
        remove.setAttribute("aria-label", `Delete ${record.name || (isApi ? "connection" : "specialist")}`);
        edit.onclick = /* Expand this configuration for editing. */ () => { record.collapsed = false; renderRecords(kind); container.querySelector("input")?.focus(); };
        remove.onclick = /* Delete this saved configuration or discard its draft. */ () => deleteRecord(kind, record);
        summary.append(icon(isApi ? "network" : "robot"), details, edit, remove); card.append(summary);
      } else {
        const form = node("form", "settings-form"); form.id = `record-${record.id}`;
        addField(form, "Display name", "name", record.name, { required: true, placeholder: isApi ? "Office workstation" : "Backend specialist" });
        if (isApi) {
          addField(form, "Endpoint URL", "url", record.url, { type: "url", required: true, placeholder: "http://192.168.1.20:11434" });
          addField(form, "API key (optional)", "key", record.key, { type: "password", placeholder: record.hasKey ? "Saved key: leave blank to keep" : "Auth token" });
          if (record.hasKey) {
            const label = node("label", "checkbox-field"), clear = node("input"); clear.type = "checkbox"; clear.name = "clearKey"; clear.checked = Boolean(record.clearKey);
            label.append(clear, document.createTextNode("Remove saved API key")); form.append(label);
          }
        } else {
          addField(form, "Model identifier", "modelName", record.modelName, { required: true, placeholder: "qwen2.5-coder:7b" });
          const connection = addField(form, "API connection", "apiId", record.apiId, { select: true, required: true });
          connectionOptions(connection, record.apiId);
          const role = addField(form, "Specialty", "role", record.role, { select: true });
          for (const [value, label] of Object.entries(roleNames)) role.append(new Option(label, value));
          role.value = record.role || "GENERAL";
          if (!state.apis.length) form.append(node("p", "field-hint", "Add an API connection in the API connections tab first."));
        }
        // Preserve partially edited records when another card is opened or added.
        const preserveDraft = /* Keep unsaved form edits in memory when cards are redrawn. */ () => { Object.assign(record, Object.fromEntries(new FormData(form))); if (isApi) record.clearKey = Boolean(form.elements.clearKey?.checked); };
        form.addEventListener("input", preserveDraft);
        form.addEventListener("change", preserveDraft);
        const actions = node("div", "record-actions"), remove = node("button", "button button-danger", "Delete"), save = node("button", "button button-primary", "Save changes");
        remove.type = "button"; remove.onclick = /* Delete this saved configuration or discard its draft. */ () => deleteRecord(kind, record); save.type = "submit";
        actions.append(remove, save); form.append(actions);
        form.onsubmit = async /* Validate and save this endpoint or specialist through the API. */ (event) => {
          event.preventDefault(); const data = Object.fromEntries(new FormData(form));
          if (!data.name.trim() || (!isApi && !data.modelName.trim())) { toast("Complete the required fields."); return; }
          if (isApi) {
            try { const url = new URL(data.url); if (!["http:", "https:"].includes(url.protocol)) throw new Error("Invalid scheme"); }
            catch { toast("Enter a valid HTTP or HTTPS endpoint URL."); return; }
          }
          save.disabled = true;
          try {
            const body = isApi ? { name: data.name.trim(), url: data.url.trim(), key: data.key || "", clearKey: Boolean(form.elements.clearKey?.checked) }
              : { name: data.name.trim(), modelName: data.modelName.trim(), apiId: data.apiId, role: data.role };
            const base = isApi ? "/api/connections/" : "/api/agents/";
            const saved = await apiRequest(record.isDraft ? base : `${base}${record.id}/`, { method: record.isDraft ? "POST" : "PATCH", body });
            Object.assign(record, saved, { isDraft: false, collapsed: true, key: "", clearKey: false });
            toast(isApi ? "API connection saved to the database." : "Specialist saved to the database.");
            renderRecords(kind); renderAgents();
            if (isApi) { renderMasterForm(); renderRecords("models"); }
          } catch (error) { toast(error.message); } finally { save.disabled = false; }
        };
        card.append(form);
      }
      container.append(card);
    }
    $("addModelBtn").disabled = state.models.length >= MAX_MODELS;
    $("modelLimitNote").textContent = `${state.models.length} of ${MAX_MODELS}`;
  }
  /** Delete a saved configuration or discard an unsaved draft. */
  async function deleteRecord(kind, record) {
    if (!record.isDraft) {
      try { await apiRequest(`/api/${kind === "apis" ? "connections" : "agents"}/${record.id}/`, { method: "DELETE" }); }
      catch (error) { toast(error.message); return; }
    }
    state[kind] = state[kind].filter(/* Keep records that match this condition. */ (item) => item.id !== record.id);
    if (kind === "apis") {
      state.models.forEach(/* Apply this update to each matching element. */ (model) => { if (model.apiId === record.id) model.apiId = ""; });
      if (state.master?.apiId === record.id) state.master.apiId = "";
      renderMasterForm(); renderRecords("models");
    }
    saveState(); renderRecords(kind); renderAgents();
  }
  /** Add an in-memory configuration draft within the specialist limit. */
  function addRecord(kind) {
    if (kind === "models" && state.models.length >= MAX_MODELS) return;
    state[kind].push(kind === "apis"
      ? { id: uid(), name: "", url: "", key: "", collapsed: false, isDraft: true }
      : { id: uid(), name: "", apiId: "", modelName: "", role: "GENERAL", collapsed: false, isDraft: true });
    saveState(); renderRecords(kind); renderAgents();
  }
  $("addApiBtn").onclick = /* Create and focus a new endpoint configuration draft. */ () => { addRecord("apis"); $("apiList").lastElementChild.querySelector("input")?.focus(); };
  $("addModelBtn").onclick = /* Create and focus a new specialist configuration draft. */ () => { addRecord("models"); $("modelList").lastElementChild.querySelector("input")?.focus(); };

  let searchSequence = 0, searchTimer;
  /** Search database conversations and ignore stale search responses. */
  async function renderSearch() {
    const sequence = ++searchSequence, query = $("searchInput").value.trim(), results = $("searchResults");
    results.replaceChildren(node("p", "search-empty", "Searching conversations..."));
    try {
      const data = await apiRequest(`/api/conversations/?limit=50&search=${encodeURIComponent(query)}${state.projectId ? `&projectId=${state.projectId}` : ""}`);
      if (sequence !== searchSequence) return;
      results.replaceChildren();
      for (const chat of data.items) {
        const button = node("button", "search-result");
        button.append(node("strong", "", chat.title), node("small", "", "Saved conversation"));
        button.onclick = /* Open the selected search result. */ () => { if (!state.chats.some(/* Check whether a matching item exists. */ (item) => item.id === chat.id)) state.chats.unshift({ ...chat, messages: [], loaded: false }); $("searchDialog").close(); selectChat(chat.id); };
        results.append(button);
      }
      if (!data.items.length) results.append(node("p", "search-empty", query ? "No matching conversations." : "Your conversations will appear here."));
    } catch (error) { if (sequence === searchSequence) results.replaceChildren(node("p", "search-empty", error.message)); }
  }
  /** Open conversation search with an empty query. */
  function openSearch() { $("searchInput").value = ""; renderSearch(); openDialog("searchDialog"); $("searchInput").focus(); }
  $("searchBtn").onclick = openSearch;
  $("searchInput").oninput = /* Debounce conversation searches and invalidate older results. */ () => { clearTimeout(searchTimer); searchSequence++; searchTimer = setTimeout(renderSearch, 200); };
  $("shortcutsBtn").onclick = /* Open the keyboard shortcuts dialog. */ () => openDialog("shortcutsDialog");
  document.addEventListener("keydown", /* Handle workspace shortcuts and contain panel focus. */ (event) => {
    const dialogOpen = document.querySelector("dialog[open]");
    if ((event.ctrlKey || event.metaKey) && !event.altKey && ["j", "k"].includes(event.key.toLowerCase())) {
      event.preventDefault(); if (dialogOpen) return;
      if (event.key.toLowerCase() === "j") createChat(); else openSearch();
    }
    if (event.key === "Escape" && !dialogOpen) { closePanels(); $("input").focus(); }
    if (event.key === "Tab" && !dialogOpen) {
      const panel = workspace.classList.contains("sidebar-open") && mobile.matches ? $("sidebar") : workspace.classList.contains("agents-open") && compact.matches ? $("agentPanel") : null;
      if (!panel) return;
      const focusable = [...panel.querySelectorAll("button:not(:disabled), a[href], input, select")].filter(/* Keep records that match this condition. */ (item) => item.getClientRects().length);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });

  workspace.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
  workspace.classList.toggle("agents-hidden", state.agentsHidden);
  setTheme(); syncPanels(); renderChats(); renderMessages(); renderAgents(); resizeComposer();
  refreshWorkspace();
})();
