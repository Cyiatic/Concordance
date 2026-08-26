(() => {
  "use strict";

  const payload = window.__CONCORDANCE_CAPTURE_SESSION__ || {};
  const status = payload.status && typeof payload.status === "object" ? payload.status : {};
  const coverage = status.coverage && typeof status.coverage === "object" ? status.coverage : {};
  const media = status.media && typeof status.media === "object" ? status.media : {};
  const evidence = status.evidence && typeof status.evidence === "object" ? status.evidence : {};
  const nextStep = status.next_step && typeof status.next_step === "object" ? status.next_step : {};
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  }[character]));
  const number = (value) => Number(value || 0).toLocaleString();
  const date = (value) => {
    if (!value) return "date unavailable";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" }).format(parsed);
  };
  const timestamp = (value) => {
    if (!value) return "timestamp unavailable";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(parsed);
  };
  const statusLabel = String(status.status || "unverified");
  const statusText = statusLabel === "verified" ? "Coverage verified" : statusLabel === "partial" ? "Partial capture" : "Coverage needs review";
  const complete = Boolean(status.complete);
  const ranges = Array.isArray(status.captures) ? status.captures : [];
  const publicRanges = Array.isArray(coverage.ranges) ? coverage.ranges : [];
  const checkpoints = Array.isArray(coverage.checkpoints) ? coverage.checkpoints : [];
  const missingDates = Array.isArray(coverage.missing_expected_dates) ? coverage.missing_expected_dates : [];
  const uniqueMessages = Number(coverage.unique_message_count || 0);
  const startConfirmed = Boolean(coverage.start_confirmed || status.reached_start);
  const endConfirmed = Boolean(coverage.end_confirmed || status.reached_end);
  const boundaries = Number(startConfirmed) + Number(endConfirmed);
  const totalMedia = Number(media.local_media || 0) + Number(media.allowed_remote_media || 0);
  const nextAction = String(status.next_action || "Open the intended DM and capture a rendered range.");
  const nextStepAction = String(nextStep.action || nextAction);
  const nextStepReason = String(nextStep.reason || "The session plan will update after the next saved range.");

  const setText = (id, value) => { const node = $(id); if (node) node.textContent = value; };
  const addStatusClass = (node, value) => {
    if (!node) return;
    node.classList.remove("is-verified", "is-partial", "is-warning");
    if (value === "verified") node.classList.add("is-verified");
    else if (value === "partial") node.classList.add("is-partial");
    else node.classList.add("is-warning");
  };

  const renderStatus = () => {
    const badge = $("status-badge");
    if (badge) { badge.textContent = statusText; addStatusClass(badge, statusLabel); }
    const hero = $("hero-state");
    if (hero) { hero.textContent = statusText; if (complete) hero.classList.add("is-verified"); }
    setText("hero-note", `${number(uniqueMessages)} unique message${uniqueMessages === 1 ? "" : "s"} across ${number(ranges.length)} capture range${ranges.length === 1 ? "" : "s"}.`);
    setText("hero-action", nextAction);
    setText("sidebar-status", statusText);
    setText("sidebar-ranges", number(ranges.length));
    setText("sidebar-messages", number(uniqueMessages));
    setText("sidebar-target", status.channel_id || "channel not set");
    setText("stat-messages", number(uniqueMessages));
    setText("stat-ranges", number(ranges.length));
    setText("stat-range-note", coverage.ranges_linked ? "overlap-linked" : "needs overlap");
    setText("stat-boundaries", `${boundaries} / 2`);
    setText("stat-media", number(totalMedia));
    setText("stat-media-note", `${number(media.remote_media || 0)} remote · ${number(evidence.files || 0)} evidence file${Number(evidence.files || 0) === 1 ? "" : "s"}`);
    const stepPlan = $("step-plan");
    if (stepPlan) {
      stepPlan.classList.toggle("is-complete", nextStep.kind === "complete");
      stepPlan.classList.toggle("is-warning", nextStep.kind === "repair_overlap" || nextStep.kind === "capture_checkpoint");
    }
    setText("step-plan-copy", nextStepAction);
    setText("step-plan-reason", nextStepReason);
    const stepMeta = $("step-plan-meta");
    if (stepMeta) {
      const options = nextStep.adapter_options && typeof nextStep.adapter_options === "object" ? nextStep.adapter_options : {};
      const direction = String(options.direction || nextStep.direction || "none");
      const settle = Number(options.settle_ms || 900);
      const overlap = Number(nextStep.overlap_required || 1);
      const reference = nextStep.reference_capture ? `reference · ${escapeHtml(nextStep.reference_capture)}` : "no prior range";
      stepMeta.innerHTML = `<span><strong>direction</strong> ${escapeHtml(direction)}</span><span><strong>settle</strong> ${number(settle)} ms</span><span><strong>overlap</strong> ${number(overlap)} message${overlap === 1 ? "" : "s"}</span><span>${reference}</span>`;
    }
    const start = $("boundary-start");
    const end = $("boundary-end");
    [[start, startConfirmed], [end, endConfirmed]].forEach(([node, reached]) => {
      if (!node) return;
      node.classList.toggle("is-reached", reached);
      const stateNode = node.querySelector(".boundary-state");
      if (stateNode) stateNode.textContent = reached ? "reached" : "unconfirmed";
    });
  };

  const renderRanges = () => {
    const list = $("range-list");
    if (!list) return;
    if (!ranges.length) {
      list.innerHTML = `<div class="range-empty">No ranges have been added. Capture the visible DM, save the JSON beside the session manifest, then run <span class="mono">capture-session add</span>.</div>`;
      return;
    }
    list.innerHTML = ranges.map((range, index) => {
      const publicRange = publicRanges[index] || {};
      const overlap = Number(publicRange.overlap_with_previous || 0);
      const boundary = [range.at_start ? "oldest boundary" : "", range.at_end ? "newest boundary" : ""].filter(Boolean).join(" · ") || "interior range";
      const overlapText = index === 0 ? "first range" : overlap ? `${number(overlap)} shared message${overlap === 1 ? "" : "s"}` : "no shared messages";
      const overlapClass = index === 0 || overlap ? "good" : "warn";
      const evidenceCount = Array.isArray(range.evidence) ? range.evidence.length : 0;
      const evidenceText = evidenceCount ? `${number(evidenceCount)} evidence file${evidenceCount === 1 ? "" : "s"}` : "no rendered evidence";
      return `<article class="range"><div class="range-head"><strong>Range ${index + 1}</strong><span class="range-file">${escapeHtml(range.path || "capture.json")}</span></div><div class="range-date">${escapeHtml(timestamp(range.oldest_timestamp))} → ${escapeHtml(timestamp(range.newest_timestamp))}</div><div class="range-meta"><span>${number(range.message_count)} message${Number(range.message_count) === 1 ? "" : "s"}</span><span class="${overlapClass}">${escapeHtml(overlapText)}</span><span>${escapeHtml(boundary)}</span><span>${escapeHtml(evidenceText)}</span></div></article>`;
    }).join("");
  };

  const renderCheckpoints = () => {
    const list = $("checkpoint-list");
    const empty = $("checkpoint-empty");
    if (!list || !empty) return;
    if (!checkpoints.length) { list.innerHTML = ""; empty.hidden = false; return; }
    empty.hidden = true;
    list.innerHTML = checkpoints.map((checkpoint) => {
      const observed = Boolean(checkpoint.observed);
      const count = Number(checkpoint.range_count || 0);
      return `<div class="checkpoint${observed ? " is-observed" : ""}"><span class="checkpoint-mark" aria-hidden="true">${observed ? "✓" : "!"}</span><div class="checkpoint-copy"><div class="checkpoint-date">${escapeHtml(checkpoint.date || "unknown date")}</div><div class="checkpoint-state">${observed ? `observed in ${number(count)} range${count === 1 ? "" : "s"}` : "not observed in captured messages"}</div></div></div>`;
    }).join("");
  };

  const renderAudit = () => {
    const grid = $("audit-grid");
    if (!grid) return;
    const items = [
      ["attachments", media.attachments], ["link previews", media.embeds], ["preview media", media.embed_media],
      ["calls", media.calls], ["replies", media.replies], ["reactions", media.reactions],
      ["stickers / emoji", Number(media.stickers || 0) + Number(media.custom_emojis || 0)], ["profile assets", media.profile_media], ["rendered evidence", evidence.files]
    ];
    grid.innerHTML = items.map(([label, value]) => `<div class="audit-item"><div class="audit-label">${escapeHtml(label)}</div><div class="audit-value">${number(value)}</div></div>`).join("");
    const hosts = media.remote_media_hosts && typeof media.remote_media_hosts === "object" ? media.remote_media_hosts : {};
    const hostEntries = Object.entries(hosts);
    const hostNode = $("audit-hosts");
    if (hostNode) hostNode.innerHTML = hostEntries.length ? `<strong>Observed remote hosts:</strong> ${hostEntries.map(([host, count]) => `${escapeHtml(host)} (${number(count)})`).join(" · ")}` : "No remote media hosts observed.";
    const warning = $("audit-warning");
    const unapproved = Number(media.unapproved_remote_media || 0);
    if (warning) { warning.hidden = unapproved === 0; warning.textContent = `${number(unapproved)} remote media reference${unapproved === 1 ? " is" : "s are"} outside the approved Discord CDN/YouTube thumbnail allowlist. It will remain a reference until explicitly handled; it was not downloaded by the archive tools.`; }
  };

  const generated = payload.generated_at ? `Dashboard snapshot generated ${timestamp(payload.generated_at)} · source session ${payload.session_file || "unavailable"}.` : "Dashboard snapshot generated locally.";
  setText("generated-note", `${generated} Refresh this dashboard after adding a range; it is a snapshot, not a live connection.`);
  renderStatus();
  renderRanges();
  renderCheckpoints();
  renderAudit();

  $("copy-action")?.addEventListener("click", async () => {
    const button = $("copy-action");
    try {
      await navigator.clipboard.writeText(nextAction);
      button.textContent = "Copied next action";
    } catch {
      button.textContent = "Copy unavailable offline";
    }
    window.setTimeout(() => { button.textContent = "Copy next action"; }, 1800);
  });

  $("copy-step-options")?.addEventListener("click", async () => {
    const button = $("copy-step-options");
    const value = String(nextStep.copy_text || nextStepAction);
    try {
      await navigator.clipboard.writeText(value);
      button.textContent = "Copied options";
    } catch {
      button.textContent = "Copy unavailable offline";
    }
    window.setTimeout(() => { button.textContent = "Copy options"; }, 1800);
  });
})();
