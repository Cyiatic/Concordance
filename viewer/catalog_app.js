(() => {
  "use strict";

  const catalog = window.__CONCORDANCE_CATALOG__ || {};
  const archives = Array.isArray(catalog.archives) ? catalog.archives : [];
  const messageIndex = Array.isArray(window.__CONCORDANCE_MESSAGE_INDEX__) ? window.__CONCORDANCE_MESSAGE_INDEX__ : [];
  const search = document.getElementById("catalog-search");
  const list = document.getElementById("archive-list");
  const messageResults = document.getElementById("message-results");
  const messageResultList = document.getElementById("message-result-list");
  const messageResultCount = document.getElementById("message-results-count");
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;"
  }[character]));
  const safeHref = (value) => {
    const candidate = String(value ?? "");
    return /^archives\/[a-z0-9][a-z0-9-]*\/index\.html$/.test(candidate) ? candidate : "#";
  };
  const safeMessageHref = (entry) => {
    const base = safeHref(entry?.viewer_path);
    const messageId = typeof entry?.message_id === "string" ? entry.message_id : "";
    return base === "#" || !messageId ? "#" : `${base}#message=${encodeURIComponent(messageId)}`;
  };
  const initials = (value) => String(value || "C")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase() || "C";
  const dateLabel = (value) => {
    if (!value) return "date unavailable";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" }).format(parsed);
  };
  const formatRange = (entry) => {
    const start = dateLabel(entry.oldest_timestamp);
    const end = dateLabel(entry.newest_timestamp);
    return start === end ? start : `${start} → ${end}`;
  };
  const coverageClass = (entry) => entry.coverage_complete ? "is-verified" : entry.coverage_status === "partial" ? "is-partial" : "is-unverified";
  const coverageLabel = (entry) => entry.coverage_complete ? "Coverage verified" : entry.coverage_status === "partial" ? "Partial capture" : "Coverage unknown";
  const searchableText = (entry) => [entry.title, entry.kind, entry.source_file, ...(Array.isArray(entry.participant_names) ? entry.participant_names : [])].join(" ").toLowerCase();
  const searchableMessageText = (entry) => [entry.archive_title, entry.author_name, entry.timestamp, entry.content].join(" ").toLowerCase();
  const numberLabel = (value) => Number(value || 0).toLocaleString();

  const renderStats = (visible) => {
    const verified = archives.filter((entry) => entry.coverage_complete).length;
    const messages = archives.reduce((total, entry) => total + Number(entry.message_count || 0), 0);
    document.getElementById("stat-archives").textContent = numberLabel(archives.length);
    document.getElementById("stat-verified").textContent = numberLabel(verified);
    document.getElementById("stat-messages").textContent = numberLabel(messages);
    document.getElementById("catalog-count").textContent = visible.length === archives.length
      ? `${numberLabel(archives.length)} archive${archives.length === 1 ? "" : "s"}`
      : `${numberLabel(visible.length)} of ${numberLabel(archives.length)} archives`;
    document.getElementById("overview-copy").textContent = archives.length
      ? `${numberLabel(archives.length)} local archive${archives.length === 1 ? " is" : "s are"} indexed here. ${messageIndex.length ? "Message search is enabled for this private catalog. " : ""}Verified status means the supplied capture ranges were overlap-linked and bounded.`
      : "This catalog is empty. Build it from one or more normalized archive JSON files to create a local register.";
  };

  const renderMessageResults = (query) => {
    if (!messageResults || !messageResultList || !messageResultCount) return;
    if (!query || !messageIndex.length) {
      messageResults.hidden = true;
      messageResultList.innerHTML = "";
      return;
    }
    const matches = messageIndex.filter((entry) => searchableMessageText(entry).includes(query)).slice(0, 100);
    messageResults.hidden = false;
    messageResultCount.textContent = `${numberLabel(matches.length)}${matches.length === 100 ? "+" : ""} result${matches.length === 1 ? "" : "s"}`;
    if (!matches.length) {
      messageResultList.innerHTML = `<div class="message-result-hint" role="status">No message text matches this search.</div>`;
      return;
    }
    messageResultList.innerHTML = matches.map((entry) => {
      const content = String(entry.content || "").trim() || "(message has no text; open the archive for attachments or embeds)";
      const author = entry.author_name || "Unknown author";
      const archiveTitle = entry.archive_title || "Untitled archive";
      const accessibleLabel = `${author} in ${archiveTitle}: ${content.slice(0, 160)}`;
      return `<a class="message-result" role="listitem" href="${escapeHtml(safeMessageHref(entry))}" aria-label="${escapeHtml(accessibleLabel)}"><span class="message-result-copy"><span class="message-result-head"><span class="message-result-author">${escapeHtml(author)}</span><span class="message-result-archive">in ${escapeHtml(archiveTitle)}</span></span><span class="message-result-snippet">${escapeHtml(content)}</span></span><span class="message-result-time">${escapeHtml(dateLabel(entry.timestamp))}</span></a>`;
    }).join("");
  };

  const render = () => {
    const query = String(search?.value || "").trim().toLowerCase();
    const visible = query ? archives.filter((entry) => searchableText(entry).includes(query)) : archives;
    renderStats(visible);
    renderMessageResults(query);
    if (!visible.length) {
      list.innerHTML = `<div class="catalog-empty" role="status"><strong>${archives.length ? "No archives match this search." : "No archives have been indexed yet."}</strong><p>${archives.length ? "Try a different conversation title, participant, or source filename." : "Run the catalog builder with your private archive files, then reopen this page."}</p></div>`;
      return;
    }
    list.innerHTML = visible.map((entry) => {
      const people = Array.isArray(entry.participant_names) && entry.participant_names.length
        ? entry.participant_names.join(" · ")
        : "Participants unavailable";
      const status = coverageLabel(entry);
      const ranges = Number(entry.coverage_range_count || 0);
      const rangeLabel = ranges ? `${ranges} range${ranges === 1 ? "" : "s"}` : "coverage not assessed";
      const accessibleLabel = `${entry.title || "Untitled archive"}; ${numberLabel(entry.message_count)} messages; ${status}; opens archived conversation.`;
      return `<a class="archive-row" role="listitem" href="${escapeHtml(safeHref(entry.viewer_path))}" aria-label="${escapeHtml(accessibleLabel)}"><span class="archive-avatar" aria-hidden="true">${escapeHtml(initials(entry.title))}</span><span class="archive-row-copy"><span class="archive-row-title">${escapeHtml(entry.title || "Untitled archive")}</span><span class="archive-row-people">${escapeHtml(people)}</span><span class="archive-row-source">${escapeHtml(entry.source_file || "source file unavailable")}</span></span><span class="archive-row-meta"><span class="coverage-status ${coverageClass(entry)}">${escapeHtml(status)}</span><span>${numberLabel(entry.message_count)} messages · ${escapeHtml(rangeLabel)}</span><span class="archive-row-range">${escapeHtml(formatRange(entry))}</span></span><span class="archive-row-arrow" aria-hidden="true">›</span></a>`;
    }).join("");
  };

  search?.addEventListener("input", render);
  search?.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      search.value = "";
      render();
    }
  });
  if (messageIndex.length && search) {
    search.placeholder = "Find a conversation or message…";
    search.setAttribute("aria-label", "Find a conversation or message");
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement !== search && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
      event.preventDefault();
      search?.focus();
    }
  });
  render();
})();
