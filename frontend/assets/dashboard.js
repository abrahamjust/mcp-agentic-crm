const stages = ["New", "Contacted", "Qualified", "Won", "Lost"];
const buckets = ["Hot", "Warm", "Cold", "Unscored"];
const state = { leads: [], interactions: [], pending: [], audit: [], search: "", bucket: "", previewTarget: null };
const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      // Keep the HTTP status as the visible error when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function setText(selector, value) {
  const element = $(selector);
  if (element) element.textContent = value;
}

function formatTime(value) {
  if (!value) return "Time unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Time unknown";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
}

function bucketClass(bucket) {
  return `bucket-${String(bucket || "Unscored").toLowerCase()}`;
}

function scoreLabel(lead) {
  return lead.lead_score == null ? "Not scored" : `${Math.round(lead.lead_score * 100)}% score`;
}

function marketingConsent(lead) {
  const consent = (lead.consent || []).find((item) => item.purpose === "marketing");
  if (!consent) return { label: "No marketing consent", className: "consent-unknown" };
  return consent.granted
    ? { label: "Marketing opted in", className: "consent-yes" }
    : { label: "Marketing declined", className: "consent-no" };
}

function leadHoverTarget(lead, content) {
  if (!lead) return content;
  const name = escapeHtml(lead.name || "Unnamed lead");
  return `<span class="lead-hover-target" tabindex="0" role="group" data-lead-hover="${escapeHtml(lead.id)}" aria-label="View full details for ${name}" aria-expanded="false">${content}</span>`;
}

function leadDetailValue(value) {
  if (value == null || value === "") return "Not provided";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function leadDetailRow(label, value) {
  return `<div class="lead-detail-row"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(leadDetailValue(value))}</dd></div>`;
}

function leadDetailMarkup(lead) {
  const consent = (lead.consent || []).length
    ? lead.consent.map((item) => leadDetailRow(
      `${String(item.purpose || "Other").replaceAll("_", " ")} consent`,
      item.granted ? "Opted in" : "Declined",
    )).join("")
    : leadDetailRow("Consent", "Not recorded");
  const factors = (lead.top_factors || []).length
    ? `<div class="lead-detail-factors"><strong>Scoring factors</strong><ul>${lead.top_factors.map((factor) => `<li>${escapeHtml(factor)}</li>`).join("")}</ul></div>`
    : "";
  return `<div class="lead-detail-heading"><strong>${escapeHtml(lead.name || "Unnamed lead")}</strong><span class="bucket-badge ${bucketClass(lead.bucket)}">${escapeHtml(lead.bucket || "Unscored")}</span></div>
    <div class="lead-detail-section"><span class="lead-detail-label">CONTACT &amp; COMPANY</span><dl>
      ${leadDetailRow("Email", lead.email)}${leadDetailRow("Phone", lead.phone)}${leadDetailRow("Company", lead.company)}
      ${leadDetailRow("Company size", lead.company_size)}${leadDetailRow("Job title", lead.job_title)}${leadDetailRow("Seniority", lead.seniority)}
    </dl></div>
    <div class="lead-detail-section"><span class="lead-detail-label">LEAD PROFILE</span><dl>
      ${leadDetailRow("Product interest", lead.product_interest)}${leadDetailRow("Source", lead.source)}
      ${leadDetailRow("Lead origin", lead.lead_origin)}${leadDetailRow("Occupation", lead.occupation)}
      ${leadDetailRow("Notes", lead.notes)}
    </dl></div>
    <div class="lead-detail-section"><span class="lead-detail-label">ENGAGEMENT &amp; PIPELINE</span><dl>
      ${leadDetailRow("Pipeline stage", lead.pipeline_stage)}${leadDetailRow("Score", lead.lead_score == null ? "Not scored" : `${Math.round(lead.lead_score * 100)}%`)}
      ${leadDetailRow("Created", lead.created_at ? formatTime(lead.created_at) : null)}
      ${leadDetailRow("Last activity", lead.last_activity_at ? formatTime(lead.last_activity_at) : null)}
      ${leadDetailRow("Scored at", lead.scored_at ? formatTime(lead.scored_at) : null)}
      ${leadDetailRow("Model version", lead.model_version)}
      ${leadDetailRow("Website visits", lead.total_visits)}
      ${leadDetailRow("Time on website (sec)", lead.time_on_website_sec)}
      ${leadDetailRow("Pages per visit", lead.pages_per_visit)}
    </dl>${factors}</div>
    <div class="lead-detail-section"><span class="lead-detail-label">CONSENT</span><dl>${consent}</dl></div>`;
}

function showLeadPreview(target) {
  const lead = state.leads.find((item) => item.id === target.dataset.leadHover);
  if (!lead) return;
  const preview = $("#lead-detail-preview");
  if (!preview) return;
  if (state.previewTarget && state.previewTarget !== target) state.previewTarget.setAttribute("aria-expanded", "false");
  state.previewTarget = target;
  target.setAttribute("aria-expanded", "true");
  preview.innerHTML = leadDetailMarkup(lead);
  preview.hidden = false;
  const bounds = target.getBoundingClientRect();
  const margin = 12;
  const previewBounds = preview.getBoundingClientRect();
  const left = Math.max(margin, Math.min(bounds.left, window.innerWidth - previewBounds.width - margin));
  const below = bounds.bottom + 8;
  const top = below + previewBounds.height <= window.innerHeight - margin
    ? below
    : Math.max(margin, bounds.top - previewBounds.height - 8);
  preview.style.left = `${left}px`;
  preview.style.top = `${top}px`;
}

function hideLeadPreview() {
  const preview = $("#lead-detail-preview");
  if (!preview) return;
  if (state.previewTarget) state.previewTarget.setAttribute("aria-expanded", "false");
  state.previewTarget = null;
  preview.hidden = true;
}

function leadPreviewForRender(container) {
  if (!state.previewTarget || !container.contains(state.previewTarget)) return null;
  return {
    leadId: state.previewTarget.dataset.leadHover,
    focused: state.previewTarget.matches(":focus"),
  };
}

function refreshLeadPreview(container, previousPreview) {
  if (!previousPreview) return;
  const replacement = [...container.querySelectorAll("[data-lead-hover]")]
    .find((target) => target.dataset.leadHover === previousPreview.leadId);
  if (!replacement) {
    hideLeadPreview();
    return;
  }
  showLeadPreview(replacement);
  if (previousPreview.focused) replacement.focus({ preventScroll: true });
}

function connectLeadPreviews() {
  const preview = document.createElement("aside");
  preview.id = "lead-detail-preview";
  preview.className = "lead-detail-preview";
  preview.hidden = true;
  preview.setAttribute("aria-label", "Full lead details");
  document.body.append(preview);

  document.addEventListener("pointerover", (event) => {
    if (event.pointerType === "touch") return;
    const target = event.target.closest("[data-lead-hover]");
    if (target) showLeadPreview(target);
  });
  document.addEventListener("pointerout", (event) => {
    const target = event.target.closest("[data-lead-hover]");
    if (!target || target.contains(event.relatedTarget) || preview.contains(event.relatedTarget)) return;
    if (event.relatedTarget?.closest?.("[data-lead-hover]")) return;
    setTimeout(() => {
      if (!preview.matches(":hover") && !state.previewTarget?.matches(":focus") && !state.previewTarget?.matches(":hover")) hideLeadPreview();
    }, 80);
  });
  document.addEventListener("focusin", (event) => {
    const target = event.target.closest("[data-lead-hover]");
    if (target) showLeadPreview(target);
  });
  document.addEventListener("focusout", (event) => {
    const target = event.target.closest("[data-lead-hover]");
    if (!target || preview.contains(event.relatedTarget) || event.relatedTarget?.closest?.("[data-lead-hover]")) return;
    setTimeout(() => {
      if (!preview.matches(":hover")) hideLeadPreview();
    }, 80);
  });
  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-lead-hover]");
    if (!target || event.detail === 0) return;
    showLeadPreview(target);
  });
  preview.addEventListener("pointerleave", () => {
    if (!state.previewTarget?.matches(":focus")) hideLeadPreview();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !preview.hidden) {
      const target = state.previewTarget;
      hideLeadPreview();
      target?.focus();
    }
  });
}

function leadCard(lead) {
  const name = escapeHtml(lead.name || "Unnamed lead");
  const company = escapeHtml(lead.company || "Company not provided");
  const email = escapeHtml(lead.email);
  const bucket = escapeHtml(lead.bucket || "Unscored");
  const score = escapeHtml(scoreLabel(lead));
  const source = escapeHtml(lead.source || "Unknown source");
  const consent = marketingConsent(lead);
  const factors = (lead.top_factors || []).slice(0, 2);
  const factorsMarkup = factors.length
    ? `<ul class="lead-factors">${factors.map((factor) => `<li>${escapeHtml(factor)}</li>`).join("")}</ul>`
    : "";
  const stageOptions = stages.map((stage) =>
    `<option value="${stage}" ${lead.pipeline_stage === stage ? "selected" : ""}>${stage}</option>`
  ).join("");
  return `<article class="lead-card">
    <div class="lead-card-top"><div class="lead-ident">${leadHoverTarget(lead, `<span class="lead-name" title="${name}">${name}</span><span class="lead-email" title="${email}">${email}</span>`)}</div>
      <span class="bucket-badge ${bucketClass(lead.bucket)}">${bucket}</span></div>
    <div class="lead-company" title="${company}">${company}</div>
    <div class="lead-meta"><span class="lead-score">${score}</span>${lead.job_title ? `<span>${escapeHtml(lead.job_title)}</span>` : ""}
      <span class="consent-badge ${consent.className}">${consent.label}</span></div>
    ${factorsMarkup}
    <div class="lead-card-bottom"><span class="lead-source">${source}</span>
      <label><span class="sr-only">Move ${name} to another stage</span><select class="stage-select" data-lead-id="${escapeHtml(lead.id)}">${stageOptions}</select></label>
    </div>
  </article>`;
}

function renderMetrics() {
  const unscored = state.leads.filter((lead) => lead.bucket === "Unscored").length;
  const scored = state.leads.length - unscored;
  const won = state.leads.filter((lead) => lead.pipeline_stage === "Won").length;
  const lost = state.leads.filter((lead) => lead.pipeline_stage === "Lost").length;
  const decided = won + lost;
  setText("#metric-total", state.leads.length);
  setText("#metric-total-inline", state.leads.length);
  setText("#metric-hot", state.leads.filter((lead) => lead.bucket === "Hot").length);
  setText("#metric-pending", state.pending.length);
  setText("#metric-scored", scored);
  setText("#metric-unscored", unscored);
  setText("#metric-win-rate", state.leads.length ? `${Math.round((won / state.leads.length) * 100)}%` : "—");
  setText("#metric-win-rate-foot", `${won} Won-stage / ${state.leads.length} leads`);
  setText("#metric-close-rate", decided ? `${Math.round((won / decided) * 100)}%` : "—");
  setText("#metric-close-rate-foot", decided ? `${won} Won / ${decided} decided` : "No Won or Lost leads yet");
  setText("#approval-count", `${state.pending.length} pending`);
  setText("#approval-nav-count", state.pending.length);

  const unscoredCallout = $("#unscored-callout");
  if (unscoredCallout) {
    unscoredCallout.hidden = unscored === 0;
    setText("#unscored-count", unscored);
    setText("#unscored-plural", unscored === 1 ? "" : "s");
    setText("#unscored-verb", unscored === 1 ? "needs" : "need");
  }
  const scoringNotice = $("#scoring-notice");
  if (scoringNotice) scoringNotice.hidden = unscored === 0;
}

function weekStart(date) {
  const monday = new Date(date);
  monday.setHours(0, 0, 0, 0);
  monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
  return monday;
}

function renderHotTrendChart() {
  const container = $("#hot-trend-chart");
  if (!container) return;
  const currentWeek = weekStart(new Date());
  const weeks = Array.from({ length: 8 }, (_, index) => {
    const start = new Date(currentWeek);
    start.setDate(start.getDate() - (7 * (7 - index)));
    const end = new Date(start);
    end.setDate(end.getDate() + 7);
    const count = state.leads.filter((lead) => {
      if (lead.bucket !== "Hot" || !lead.scored_at) return false;
      const scoredAt = new Date(lead.scored_at);
      return !Number.isNaN(scoredAt.getTime()) && scoredAt >= start && scoredAt < end;
    }).length;
    return { start, count };
  });
  const max = Math.max(1, ...weeks.map((week) => week.count));
  const width = 720;
  const height = 220;
  const left = 44;
  const right = 12;
  const top = 14;
  const bottom = 35;
  const plotHeight = height - top - bottom;
  const plotWidth = width - left - right;
  const barWidth = Math.min(42, plotWidth / weeks.length * 0.55);
  const ticks = [...new Set([max, Math.ceil(max / 2), 0])];
  const grid = ticks.map((tick) => {
    const y = top + plotHeight - (tick / max) * plotHeight;
    return `<line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" class="chart-grid-line"/>
      <text x="${left - 10}" y="${y + 4}" class="chart-axis-label" text-anchor="end">${tick}</text>`;
  }).join("");
  const bars = weeks.map(({ start, count }, index) => {
    const x = left + ((index + 0.5) * plotWidth / weeks.length);
    const barHeight = count ? Math.max(2, (count / max) * plotHeight) : 0;
    const y = top + plotHeight - barHeight;
    const label = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(start);
    return `<g><title>Week of ${escapeHtml(label)}: ${count} currently Hot ${count === 1 ? "lead" : "leads"}</title>
      <rect x="${x - barWidth / 2}" y="${y}" width="${barWidth}" height="${barHeight}" rx="5" class="hot-chart-bar"/>
      <text x="${x}" y="${height - 10}" class="chart-axis-label" text-anchor="middle">${escapeHtml(label)}</text>
      ${count ? `<text x="${x}" y="${y - 6}" class="chart-value-label" text-anchor="middle">${count}</text>` : ""}
    </g>`;
  }).join("");
  container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Currently Hot leads by their most recent scoring week">
    ${grid}${bars}</svg>`;
}

function renderConversionChart() {
  const container = $("#conversion-chart");
  if (!container) return;
  if (!state.leads.length) {
    container.innerHTML = `<div class="chart-empty">Conversion data will appear when leads enter the CRM.</div>`;
    return;
  }
  const today = new Date();
  const months = Array.from({ length: 6 }, (_, index) => {
    const date = new Date(today.getFullYear(), today.getMonth() - (5 - index), 1);
    const cohort = state.leads.filter((lead) => {
      const createdAt = new Date(lead.created_at);
      return !Number.isNaN(createdAt.getTime())
        && createdAt.getFullYear() === date.getFullYear()
        && createdAt.getMonth() === date.getMonth();
    });
    const won = cohort.filter((lead) => lead.pipeline_stage === "Won").length;
    return { date, count: cohort.length, won, rate: cohort.length ? (won / cohort.length) * 100 : null };
  });
  const width = 720;
  const height = 220;
  const left = 44;
  const right = 12;
  const top = 14;
  const bottom = 35;
  const plotHeight = height - top - bottom;
  const plotWidth = width - left - right;
  const grid = [100, 50, 0].map((tick) => {
    const y = top + plotHeight - (tick / 100) * plotHeight;
    return `<line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" class="chart-grid-line"/>
      <text x="${left - 10}" y="${y + 4}" class="chart-axis-label" text-anchor="end">${tick}%</text>`;
  }).join("");
  const points = months.map((month, index) => ({
    ...month,
    x: left + (months.length === 1 ? 0.5 : index / (months.length - 1)) * plotWidth,
    y: month.rate == null ? null : top + plotHeight - (month.rate / 100) * plotHeight,
  }));
  const line = points.slice(1).map((point, index) => {
    const previous = points[index];
    if (point.rate == null || previous.rate == null) return "";
    return `<line x1="${previous.x}" y1="${previous.y}" x2="${point.x}" y2="${point.y}" class="conversion-line"/>`;
  }).join("");
  const marks = points.map((point) => {
    const monthLabel = new Intl.DateTimeFormat(undefined, { month: "short" }).format(point.date);
    const title = point.rate == null
      ? `${monthLabel}: no leads`
      : `${monthLabel}: ${point.won} Won of ${point.count} leads (${Math.round(point.rate)}%)`;
    const marker = point.rate == null
      ? ""
      : `<circle cx="${point.x}" cy="${point.y}" r="5" class="conversion-point"><title>${escapeHtml(title)}</title></circle>
        <text x="${point.x}" y="${point.y - 10}" class="chart-value-label" text-anchor="middle">${Math.round(point.rate)}%</text>`;
    return `<g><title>${escapeHtml(title)}</title>${marker}
      <text x="${point.x}" y="${height - 10}" class="chart-axis-label" text-anchor="middle">${escapeHtml(monthLabel)}</text></g>`;
  }).join("");
  const totalWon = state.leads.filter((lead) => lead.pipeline_stage === "Won").length;
  container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Current Won-stage conversion rate by lead-created month">
    ${grid}${line}${marks}</svg><p class="chart-footnote">${totalWon} of ${state.leads.length} leads are currently in Won. Monthly rates use each signup-month cohort and its current stage.</p>`;
}

function renderCategoryChart(selector, values, ariaLabel, emptyMessage) {
  const container = $(selector);
  if (!container) return;
  if (!values.length || values.every((item) => item.count === 0)) {
    container.innerHTML = `<div class="chart-empty">${escapeHtml(emptyMessage)}</div>`;
    return;
  }
  const width = 720;
  const left = 150;
  const right = 42;
  const top = 8;
  const rowHeight = 38;
  const height = top + (values.length * rowHeight);
  const trackWidth = width - left - right;
  const max = Math.max(...values.map((item) => item.count));
  const colors = ["chart-bar-yellow", "chart-bar-lime", "chart-bar-blue", "chart-bar-pink", "chart-bar-green", "chart-bar-muted"];
  const rows = values.map(({ label, count }, index) => {
    const y = top + (index * rowHeight);
    const barWidth = (count / max) * trackWidth;
    const shortLabel = label.length > 20 ? `${label.slice(0, 18)}…` : label;
    return `<g><title>${escapeHtml(label)}: ${count}</title>
      <text x="${left - 12}" y="${y + 19}" class="chart-category-label" text-anchor="end">${escapeHtml(shortLabel)}</text>
      <rect x="${left}" y="${y + 5}" width="${trackWidth}" height="12" rx="6" class="chart-category-track"/>
      <rect x="${left}" y="${y + 5}" width="${barWidth}" height="12" rx="6" class="${colors[index % colors.length]}"/>
      <text x="${left + trackWidth + 12}" y="${y + 16}" class="chart-category-value">${count}</text>
    </g>`;
  }).join("");
  container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(ariaLabel)}">${rows}</svg>`;
}

function renderStageChart() {
  renderCategoryChart(
    "#stage-chart",
    stages.map((label) => ({
      label,
      count: state.leads.filter((lead) => lead.pipeline_stage === label).length,
    })),
    "Current lead count by pipeline stage",
    "Stage data will appear when leads enter the CRM.",
  );
}

function renderScoreChart() {
  const scoredLeads = state.leads.filter((lead) => ["Hot", "Warm", "Cold"].includes(lead.bucket));
  renderCategoryChart(
    "#score-chart",
    ["Hot", "Warm", "Cold"].map((label) => ({
      label,
      count: scoredLeads.filter((lead) => lead.bucket === label).length,
    })),
    "Currently scored leads by Agent A score bucket",
    "No scored leads yet. Unscored leads are excluded from this chart.",
  );
}

function renderSourceChart() {
  const sourceCounts = new Map();
  state.leads.forEach((lead) => {
    const source = lead.source || "Unknown";
    sourceCounts.set(source, (sourceCounts.get(source) || 0) + 1);
  });
  const rankedSources = [...sourceCounts]
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
  const topSources = rankedSources.slice(0, 6);
  const otherCount = rankedSources.slice(6).reduce((total, item) => total + item.count, 0);
  if (otherCount) topSources.push({ label: "Other sources", count: otherCount });
  renderCategoryChart(
    "#source-chart",
    topSources,
    "Lead count by recorded source",
    "Source data will appear when leads enter the CRM.",
  );
}

function renderConsentChart() {
  const consentCounts = { "Opted in": 0, "Declined": 0, "Not recorded": 0 };
  state.leads.forEach((lead) => {
    const consent = (lead.consent || []).find((item) => item.purpose === "marketing");
    if (!consent) consentCounts["Not recorded"] += 1;
    else if (consent.granted) consentCounts["Opted in"] += 1;
    else consentCounts.Declined += 1;
  });
  renderCategoryChart(
    "#consent-chart",
    Object.entries(consentCounts).map(([label, count]) => ({ label, count })),
    "Lead count by marketing consent status",
    "Consent data will appear when leads enter the CRM.",
  );
}

function renderOverview() {
  if (document.body.dataset.page !== "overview") return;
  renderHotTrendChart();
  renderConversionChart();
  renderStageChart();
  renderScoreChart();
  renderSourceChart();
  renderConsentChart();
}

function renderPipeline() {
  const board = $("#pipeline-board");
  if (!board) return;
  const previousPreview = leadPreviewForRender(board);
  const query = state.search.trim().toLowerCase();
  const leads = state.leads.filter((lead) => {
    const matchesName = !query || String(lead.name || "").toLowerCase().includes(query);
    return matchesName && (!state.bucket || lead.bucket === state.bucket);
  });
  if (!leads.length) {
    board.innerHTML = `<div class="pipeline-empty">${state.leads.length
      ? "No leads match your search and score filter."
      : "Your new leads will appear here."}</div>`;
    refreshLeadPreview(board, previousPreview);
    return;
  }
  board.innerHTML = stages.map((stage) => {
    const stageLeads = leads.filter((lead) => lead.pipeline_stage === stage);
    const cards = stageLeads.length
      ? stageLeads.map(leadCard).join("")
      : `<div class="empty-stage">${state.leads.length ? "No leads in this stage" : "Your new leads will appear here"}</div>`;
    return `<section class="stage-column" aria-label="${stage} stage">
      <div class="stage-heading"><span><i class="stage-dot stage-${stage.toLowerCase()}"></i>${stage}</span><span class="stage-count">${stageLeads.length}</span></div>
      <div class="stage-cards">${cards}</div>
    </section>`;
  }).join("");
  board.querySelectorAll(".stage-select").forEach((select) => {
    select.addEventListener("change", () => moveLead(select.dataset.leadId, select.value, select));
  });
  refreshLeadPreview(board, previousPreview);
}

function renderApprovals() {
  const list = $("#approval-list");
  if (!list) return;
  const previousPreview = leadPreviewForRender(list);
  const leadById = new Map(state.leads.map((lead) => [lead.id, lead]));
  if (!state.pending.length) {
    list.innerHTML = `<div class="empty-state">${state.interactions.length
      ? "No follow-up drafts are waiting for review."
      : "No pending drafts yet. Hot leads with marketing consent may receive a draft when Agent B is running."}</div>`;
    refreshLeadPreview(list, previousPreview);
    return;
  }
  list.innerHTML = state.pending.map((item) => {
    const lead = leadById.get(item.lead_id);
    const payload = item.payload || {};
    const email = payload.email || {};
    const reviewer = payload.reviewer_context || {};
    const factors = (reviewer.top_factors || []).slice(0, 3).map(escapeHtml).join(" · ");
    const context = [lead?.company, reviewer.bucket, reviewer.lead_score == null ? null : `${Math.round(reviewer.lead_score * 100)}% score`]
      .filter(Boolean).map(escapeHtml).join(" · ");
    return `<article class="approval-card" data-interaction-id="${escapeHtml(item.id)}">
      <div class="approval-card-head"><div>${leadHoverTarget(lead, `<span class="approval-person">${escapeHtml(lead?.name || "Lead record")}</span>`)}
        <div class="approval-context">${context || "Consent check passed"} · ${escapeHtml(lead?.email || "")}</div></div>
        <span class="bucket-badge bucket-hot">EMAIL DRAFT</span></div>
      <div class="email-subject">${escapeHtml(email.subject || "Follow-up draft")}</div>
      <p class="email-body">${escapeHtml(email.body || "The email draft has no body.")}</p>
      ${factors ? `<div class="reviewer-factors">Score factors: ${factors}</div>` : ""}
      <div class="approval-actions"><button class="reject-button" type="button" data-status="rejected">Reject</button>
        <button class="approve-button" type="button" data-status="approved" data-open-compose="true" ${lead?.email ? "" : "disabled"}>Open email &amp; approve</button></div>
    </article>`;
  }).join("");
  list.querySelectorAll("[data-status]").forEach((button) => {
    button.addEventListener("click", () => updateApproval(button.closest("[data-interaction-id]"), button));
  });
  refreshLeadPreview(list, previousPreview);
}

function interactionTitle(item, lead) {
  const name = lead?.name || "A lead";
  if (item.action_type === "drafted_followup_email") return `Follow-up draft created for ${name}`;
  if (item.action_type === "no_action_consent_blocked") return `Follow-up blocked for ${name}`;
  return `${String(item.action_type || "Agent action").replaceAll("_", " ")} · ${name}`;
}

function renderActivity() {
  const list = $("#activity-list");
  if (!list) return;
  const previousPreview = leadPreviewForRender(list);
  const leadById = new Map(state.leads.map((lead) => [lead.id, lead]));
  const items = [
    ...state.interactions.map((item) => ({ kind: "interaction", item, timestamp: item.created_at })),
    ...state.audit.map((item) => ({ kind: "consent", item, timestamp: item.timestamp })),
  ].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp)).slice(0, 50);
  if (!items.length) {
    list.innerHTML = `<div class="empty-state">Agent actions and consent decisions will appear here when recorded.</div>`;
    refreshLeadPreview(list, previousPreview);
    return;
  }
  list.innerHTML = items.map(({ kind, item, timestamp }) => {
    const lead = leadById.get(item.lead_id);
    if (kind === "consent") {
      const allowed = item.result === "allowed";
      return `<div class="activity-row"><span class="activity-icon">${allowed ? "✓" : "⊘"}</span><div class="activity-copy">
        <div class="activity-title">${leadHoverTarget(lead, `${escapeHtml(item.purpose_required)} consent ${allowed ? "allowed" : "blocked"} · ${escapeHtml(lead?.name || "Lead record")}`)}</div>
        <div class="activity-detail">Checked by ${escapeHtml(item.actor || "agent")} · ${escapeHtml(item.action_attempted || "consent check")}</div>
        <span class="status-badge ${allowed ? "status-approved" : "status-blocked"}">${allowed ? "allowed" : "blocked"}</span>
      </div><time class="activity-time">${escapeHtml(formatTime(timestamp))}</time></div>`;
    }
    const reason = item.payload?.reason;
    const detail = reason || item.payload?.email?.subject || item.payload?.suggested_next_step || "CRM pipeline activity";
    const icon = item.action_type === "drafted_followup_email" ? "✉"
      : item.action_type === "no_action_consent_blocked" ? "⊘" : "↗";
    return `<div class="activity-row"><span class="activity-icon">${icon}</span><div class="activity-copy">
      <div class="activity-title">${leadHoverTarget(lead, escapeHtml(interactionTitle(item, lead)))}</div>
      <div class="activity-detail">${escapeHtml(detail)}</div>
      <span class="status-badge status-${escapeHtml(item.status)}">${escapeHtml(String(item.status || "unknown").replaceAll("_", " "))}</span>
    </div><time class="activity-time">${escapeHtml(formatTime(timestamp))}</time></div>`;
  }).join("");
  refreshLeadPreview(list, previousPreview);
}

async function moveLead(leadId, pipelineStage, select) {
  select.disabled = true;
  try {
    await request(`/leads/${encodeURIComponent(leadId)}/stage`, {
      method: "PATCH",
      body: JSON.stringify({ pipeline_stage: pipelineStage }),
    });
    await loadDashboard();
    showToast(`Lead moved to ${pipelineStage}.`);
  } catch (error) {
    showToast(`Could not update the pipeline stage: ${error.message}`);
    select.disabled = false;
    await loadDashboard();
  }
}

async function updateApproval(card, button) {
  if (!card || button.disabled) return;
  const status = button.dataset.status;
  const opensCompose = button.dataset.openCompose === "true";
  const interaction = state.pending.find((item) => item.id === card.dataset.interactionId);
  const lead = interaction && state.leads.find((item) => item.id === interaction.lead_id);
  const email = interaction?.payload?.email || {};
  const composeUrl = opensCompose && lead?.email
    ? `mailto:${encodeURIComponent(lead.email)}?subject=${encodeURIComponent(email.subject || "")}&body=${encodeURIComponent(email.body || "")}`
    : null;
  const composeWindow = composeUrl ? window.open("about:blank", "_blank") : null;
  const buttons = card.querySelectorAll("button");
  buttons.forEach((item) => { item.disabled = true; });
  try {
    await request(`/interactions/${encodeURIComponent(card.dataset.interactionId)}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    if (composeUrl) {
      if (composeWindow) {
        composeWindow.location.href = composeUrl;
      } else {
        showToast("Approval saved. Opening your default email app in this tab.");
      }
    }
    await loadDashboard();
    if (composeUrl && !composeWindow) {
      location.href = composeUrl;
    } else {
      showToast(status === "approved"
        ? opensCompose
          ? "Draft approved and opened in your default email app. Review and send it there."
          : "Draft approved. No email was sent. Agent B will update the lead stage when it is running."
        : "Draft rejected. No email was sent.");
    }
  } catch (error) {
    if (composeWindow) composeWindow.close();
    buttons.forEach((item) => { item.disabled = false; });
    showToast(`Could not update this draft: ${error.message}`);
  }
}

function showToast(message) {
  const toast = $("#toast");
  if (!toast) return;
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast.timeout);
  showToast.timeout = setTimeout(() => { toast.hidden = true; }, 5500);
}

async function loadDashboard() {
  const errorBox = $("#page-error");
  try {
    const [leads, interactions, pending, audit] = await Promise.all([
      request("/leads?limit=1000"),
      request("/interactions?limit=100"),
      request("/interactions?status=pending_approval&limit=100"),
      request("/audit-log?limit=100"),
    ]);
    state.leads = leads;
    state.interactions = interactions;
    state.pending = pending;
    state.audit = audit;
    if (errorBox) errorBox.hidden = true;
    renderMetrics();
    renderOverview();
    renderPipeline();
    renderApprovals();
    renderActivity();
  } catch (error) {
    console.error("Could not load CRM dashboard data:", error);
    setText("#error-message", error instanceof Error ? error.message : String(error));
    if (errorBox) errorBox.hidden = false;
    setConnection(false, "API unavailable");
  }
}

function setConnection(connected, label) {
  setText("#connection-label", label);
  const dot = $("#connection-dot");
  if (dot) {
    dot.classList.toggle("connected", connected);
    dot.classList.toggle("disconnected", !connected);
  }
}

function connectLiveUpdates() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${location.host}/ws`);
  socket.addEventListener("open", () => setConnection(true, "Live"));
  socket.addEventListener("message", () => {
    clearTimeout(connectLiveUpdates.refreshTimer);
    connectLiveUpdates.refreshTimer = setTimeout(loadDashboard, 180);
  });
  socket.addEventListener("error", () => setConnection(false, "Reconnecting"));
  socket.addEventListener("close", () => {
    setConnection(false, "Reconnecting");
    setTimeout(connectLiveUpdates, 3000);
  }, { once: true });
}

const page = document.body.dataset.page;
if (page !== "lead-form") {
  $("#lead-search")?.addEventListener("input", (event) => {
    state.search = event.target.value;
    renderPipeline();
  });
  $("#bucket-filter")?.addEventListener("change", (event) => {
    state.bucket = event.target.value;
    renderPipeline();
  });
  $("#retry-button")?.addEventListener("click", loadDashboard);

  const initialBucket = new URLSearchParams(location.search).get("bucket");
  if (initialBucket && buckets.includes(initialBucket)) {
    state.bucket = initialBucket;
    const filter = $("#bucket-filter");
    if (filter) filter.value = initialBucket;
  }
  connectLeadPreviews();
  if (new URLSearchParams(location.search).get("created") === "1") {
    showToast("Lead saved. Scoring and follow-up will run when the agent workers are active.");
    history.replaceState(null, "", "/");
  }

  loadDashboard();
  connectLiveUpdates();
  setInterval(loadDashboard, 20000);
}
