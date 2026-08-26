async (options = {}) => {
  const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const callFromContent = (value) => {
    const text = clean(value).split(/\s+—\s+/)[0].trim();
    let match = text.match(/^You missed a call from (.+?) that lasted (.+?)\.$/i);
    if (match) {
      return {
        type: "voice",
        status: "missed",
        initiator_name: clean(match[1]),
        duration_label: clean(match[2]),
      };
    }
    match = text.match(/^(.+?) started a call that lasted (.+?)\.$/i);
    if (match) {
      return {
        type: "voice",
        status: "completed",
        initiator_name: clean(match[1]),
        duration_label: clean(match[2]),
      };
    }
    return null;
  };
  const requestedDirection = options && (options.direction === "older" || options.direction === "newer")
    ? options.direction
    : "none";
  const previousScrollTop = Number.isFinite(Number(options?.previous_scroll_top))
    ? Number(options.previous_scroll_top)
    : null;
  const classes = (element) =>
    element && typeof element.className === "string" ? element.className : "";
  const isHttp = (value) => /^https?:\/\//i.test(String(value ?? ""));
  const youtubeVideoId = (value) => {
    if (!isHttp(value)) return null;
    try {
      const parsed = new URL(String(value), location.href);
      const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
      let candidate = null;
      if (host === "youtu.be" || host === "www.youtu.be") {
        candidate = parsed.pathname.replace(/^\/+/, "").split("/", 1)[0];
      } else if (["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"].includes(host)) {
        if (parsed.pathname.toLowerCase() === "/watch") candidate = parsed.searchParams.get("v");
        else if (/^\/(?:shorts|embed|live)\//i.test(parsed.pathname)) candidate = parsed.pathname.split("/")[2];
      }
      return candidate && /^[A-Za-z0-9_-]{6,20}$/.test(candidate) ? candidate : null;
    } catch {
      return null;
    }
  };
  const youtubeThumbnailUrl = (value) => {
    const id = youtubeVideoId(value);
    return id ? `https://i.ytimg.com/vi/${id}/hqdefault.jpg` : null;
  };
  const slug = (value) => clean(value).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const displayTimezone = typeof Intl !== "undefined" && Intl.DateTimeFormat
    ? Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
    : "UTC";
  const mimeFor = (value) => {
    const extension = String(value ?? "").split(/[?#]/, 1)[0].split(".").pop().toLowerCase();
    return {
      png: "image/png",
      jpg: "image/jpeg",
      jpeg: "image/jpeg",
      gif: "image/gif",
      webp: "image/webp",
      svg: "image/svg+xml",
      json: "application/json",
      mp3: "audio/mpeg",
      wav: "audio/wav",
      ogg: "audio/ogg",
      mp4: "video/mp4",
      webm: "video/webm",
      mov: "video/quicktime",
      pdf: "application/pdf",
      txt: "text/plain",
      zip: "application/zip",
    }[extension] || "application/octet-stream";
  };
  const fileName = (value) => {
    try {
      const path = new URL(value, location.href).pathname;
      return path.split("/").filter(Boolean).pop() || "attachment";
    } catch {
      return "attachment";
    }
  };
  const stickerFormatInfo = (formatType, reference) => {
    const normalizedType = String(formatType ?? "").trim();
    if (normalizedType === "1") return { format: "png", mime: "image/png", animated: false };
    if (normalizedType === "2") return { format: "apng", mime: "image/apng", animated: true };
    if (normalizedType === "3") return { format: "lottie", mime: "application/json", animated: true };
    if (normalizedType === "4") return { format: "gif", mime: "image/gif", animated: true };
    const extension = String(reference ?? "").split(/[?#]/, 1)[0].split(".").pop().toLowerCase();
    if (extension === "gif") return { format: "gif", mime: "image/gif", animated: true };
    if (extension === "json") return { format: "lottie", mime: "application/json", animated: true };
    if (extension === "apng") return { format: "apng", mime: "image/apng", animated: true };
    return { format: extension || "png", mime: mimeFor(reference) || "image/png", animated: false };
  };
  const stickerReferenceUrl = (id, formatType) => {
    const safeId = String(id ?? "").trim();
    if (!/^[A-Za-z0-9_-]+$/.test(safeId)) return null;
    const extension = stickerFormatInfo(formatType).format === "lottie"
      ? "json"
      : stickerFormatInfo(formatType).format === "gif"
        ? "gif"
        : "png";
    return `https://cdn.discordapp.com/stickers/${safeId}.${extension}`;
  };
  const messageId = (article) => {
    const content = article.querySelector('[id^="message-content-"]');
    const fromContent = content?.id?.match(/^message-content-(.+)$/)?.[1];
    if (fromContent) return fromContent;
    return article.getAttribute("data-list-item-id")?.match(/(\d+)$/)?.[1] || null;
  };
  const authorIdFromAvatar = (src) => {
    try {
      return new URL(src, location.href).pathname.match(/\/avatars\/([^/]+)\//)?.[1] || null;
    } catch {
      return null;
    }
  };
  const readVisibleProfile = () => {
    const aside = Array.from(document.querySelectorAll("aside")).find((element) =>
      Boolean(element.querySelector("h2[id^='user-profile-sidebar-heading-']")),
    );
    if (!aside) return null;

    const avatarElement = aside.querySelector('img[src*="/avatars/"]');
    const avatarRef = avatarElement?.getAttribute("src");
    const participantId = isHttp(avatarRef) ? authorIdFromAvatar(avatarRef) : null;
    const mediaReference = (element) => {
      if (!element) return null;
      const candidates = [
        element.getAttribute("src"),
        element.getAttribute("data-src"),
        element.style?.backgroundImage,
        getComputedStyle(element).backgroundImage,
      ];
      for (const candidate of candidates) {
        const match = String(candidate || "").match(/https?:\/\/[^\s)"']+/i);
        if (match && isHttp(match[0])) return match[0];
      }
      return null;
    };
    const bannerElement = aside.querySelector('[class*="banner"]');
    const avatarDecorationElement = aside.querySelector('[class*="avatarDecoration"], [class*="avatar-decoration"]');
    const bannerRef = mediaReference(bannerElement) || mediaReference(bannerElement?.querySelector("img"));
    const avatarDecorationRef = mediaReference(avatarDecorationElement) || mediaReference(avatarDecorationElement?.querySelector("img"));
    const displayName = clean(
      aside.querySelector('[class*="displayNameRow"] [data-text-variant*="heading"]')?.textContent
      || aside.querySelector('[class*="displayNameRow"]')?.textContent,
    );
    const username = clean(aside.querySelector('[class*="userTagUsername"]')?.textContent);
    const pronouns = clean(
      aside.querySelector('[class*="pronouns"] [aria-hidden="true"]')?.textContent
      || aside.querySelector('[class*="pronouns"]')?.textContent,
    );
    const avatarWrapper = avatarElement?.closest('[role="img"][aria-label]');
    const presenceLabel = clean(avatarWrapper?.getAttribute("aria-label"));
    const presence = presenceLabel.match(/,\s*(.+)$/)?.[1]?.trim() || null;
    const badgeContainer = aside.querySelector('[aria-label="User Badges"]');
    const badges = Array.from(badgeContainer?.querySelectorAll('a[aria-label]') || []).map((badge) => {
      const detailId = badge.getAttribute("aria-describedby");
      const detail = detailId ? clean(document.getElementById(detailId)?.textContent) : "";
      const iconRef = badge.querySelector("img[src]")?.getAttribute("src");
      return {
        label: clean(badge.getAttribute("aria-label")),
        detail: detail || null,
        icon_ref: isHttp(iconRef) ? iconRef : null,
      };
    }).filter((badge) => badge.label || badge.detail || badge.icon_ref);
    const mutualSection = Array.from(aside.querySelectorAll('[class*="mutuals"]')).find((element) =>
      /mutual friends/i.test(clean(element.textContent)),
    );
    const mutualFriends = Array.from(mutualSection?.querySelectorAll('[role="img"][aria-label]') || []).map((friend) => {
      const friendAvatar = friend.querySelector('img[src*="/avatars/"]')?.getAttribute("src");
      const friendLabel = clean(friend.getAttribute("aria-label"));
      return {
        id: authorIdFromAvatar(friendAvatar) || null,
        display_name: friendLabel || null,
        avatar_ref: isHttp(friendAvatar) ? friendAvatar : null,
      };
    }).filter((friend) => friend.id || friend.display_name || friend.avatar_ref);
    const memberSection = Array.from(aside.querySelectorAll("section")).find((section) =>
      /^Member Since\s*/i.test(clean(section.textContent)),
    );
    const memberText = clean(memberSection?.textContent);
    const memberSince = memberText.replace(/^Member Since\s*/i, "").trim() || null;
    const customStatusElement = aside.querySelector('[class*="customStatus"], [class*="custom-status"]');
    const customStatus = clean(customStatusElement?.innerText || customStatusElement?.textContent) || null;
    const activities = Array.from(aside.querySelectorAll('[class*="activity"]'))
      .map((element) => clean(element.innerText || element.textContent))
      .filter((value) => value && value !== customStatus && value.length <= 240)
      .filter((value, index, values) => values.indexOf(value) === index)
      .slice(0, 8);
    const profile = {
      presence: presence || null,
      pronouns: pronouns || null,
      member_since: memberSince,
      banner_ref: isHttp(bannerRef) ? bannerRef : null,
      avatar_decoration_ref: isHttp(avatarDecorationRef) ? avatarDecorationRef : null,
      custom_status: customStatus,
      activities,
      badges,
      mutual_friends: mutualFriends,
      captured_at: new Date().toISOString(),
      source: "visible_profile_card",
    };
    if (!profile.presence && !profile.pronouns && !profile.member_since && !profile.banner_ref && !profile.avatar_decoration_ref && !profile.custom_status && !activities.length && !badges.length && !mutualFriends.length) return null;
    return {
      participant_id: participantId,
      display_name: displayName || null,
      username: username || null,
      avatar_ref: isHttp(avatarRef) ? avatarRef : null,
      profile,
    };
  };
  const channelMatch = location.pathname.match(/^\/channels\/@me\/([^/]+)/);
  if (!channelMatch) {
    throw new Error("Open the intended Discord direct message before capturing.");
  }
  const channelId = channelMatch[1];
  const messageNodes = Array.from(document.querySelectorAll('[role="article"][data-list-item-id]'));
  if (!messageNodes.length) {
    throw new Error("No rendered messages were found in the open direct message.");
  }
  let messageScroller = messageNodes[0]?.parentElement || null;
  while (messageScroller && messageScroller !== document.body) {
    const style = getComputedStyle(messageScroller);
    if (messageScroller.scrollHeight > messageScroller.clientHeight + 80 && /(auto|scroll)/.test(style.overflowY)) break;
    messageScroller = messageScroller.parentElement;
  }
  const scrollTop = Number(messageScroller?.scrollTop || 0);
  const scrollHeight = Number(messageScroller?.scrollHeight || 0);
  const viewportHeight = Number(messageScroller?.clientHeight || 0);
  const scrollPosition = {
    scroll_top: scrollTop,
    scroll_height: scrollHeight,
    viewport_height: viewportHeight,
    at_start: !messageScroller || scrollTop <= 4,
    at_end: !messageScroller || scrollTop + viewportHeight >= scrollHeight - 4,
    requested_direction: requestedDirection,
    moved_pixels: previousScrollTop === null ? null : Math.round(Math.abs(scrollTop - previousScrollTop)),
    previous_scroll_top: previousScrollTop,
  };

  const participants = new Map();
  const messages = [];
  const attachmentsByMessage = new Map();
  let currentAuthor = null;

  for (const article of messageNodes) {
    const id = messageId(article);
    const timeElement = article.querySelector('time[datetime]');
    const timestamp = timeElement?.getAttribute("datetime");
    if (!id || !timestamp) continue;
    const sourceTimestampLabel = clean(
      timeElement?.parentElement?.parentElement?.querySelector('[class*="hiddenVisually"]')?.innerText
      || timeElement?.parentElement?.parentElement?.querySelector('[class*="hiddenVisually"]')?.textContent,
    );
    const sourceDisplay = {};
    if (sourceTimestampLabel) {
      sourceDisplay.label = sourceTimestampLabel;
      const separator = sourceTimestampLabel.match(/^(.*)\s+at\s+(.+)$/i);
      if (separator) {
        sourceDisplay.date = separator[1].trim();
        sourceDisplay.time = separator[2].trim();
      }
    }

    const usernameContainer = article.querySelector('[id^="message-username-"]');
    const usernameElement = usernameContainer?.querySelector('[class*="username"]') || usernameContainer;
    const avatarElement = article.querySelector('img[class*="avatar"]');
    const avatarRef = avatarElement?.getAttribute("src");
    const avatarId = isHttp(avatarRef) ? authorIdFromAvatar(avatarRef) : null;
    const displayName = clean(usernameElement?.innerText || usernameContainer?.innerText);
    if (usernameContainer || avatarId) {
      const authorId = avatarId || `author-${slug(displayName) || participants.size + 1}`;
      currentAuthor = {
        id: authorId,
        displayName: displayName || authorId,
        avatarRef: isHttp(avatarRef) ? avatarRef : null,
      };
      const participant = participants.get(authorId) || {
        id: authorId,
        display_name: currentAuthor.displayName,
        username: currentAuthor.displayName,
      };
      if (currentAuthor.displayName) {
        participant.display_name = currentAuthor.displayName;
        participant.username = currentAuthor.displayName;
      }
      if (currentAuthor.avatarRef) participant.avatar_ref = currentAuthor.avatarRef;
      participants.set(authorId, participant);
    }
    if (!currentAuthor) continue;

    const contentElement = article.querySelector('[id^="message-content-"]');
    const content = clean(contentElement?.innerText || contentElement?.textContent);
    const message = {
      id: String(id),
      author_id: currentAuthor.id,
      timestamp,
      content,
      grouped: !/\bgroupStart(?:_|\b)/i.test(classes(article)),
      channel_id: channelId,
      attachments: [],
      stickers: [],
      custom_emojis: [],
      reactions: [],
      embeds: [],
      message_link: `${location.origin}/channels/@me/${channelId}/${id}`,
    };
    if (Object.keys(sourceDisplay).length) message.source_display = sourceDisplay;
    const call = callFromContent(content);
    if (call) {
      message.call = call;
      message.content = clean(content).split(/\s+—\s+/)[0].trim();
    }

    const accessories = article.querySelector('[id^="message-accessories-"]');
    const attachments = new Map();
    const addAttachment = (url) => {
      if (!isHttp(url) || attachments.has(url)) return;
      const name = fileName(url);
      attachments.set(url, { name, url, mime: mimeFor(name) });
    };
    if (accessories) {
      for (const anchor of accessories.querySelectorAll('a[href]')) {
        const href = anchor.href;
        if (/\/attachments\/\d+\/\d+/i.test(href)) {
          addAttachment(href);
        }
      }
      for (const media of accessories.querySelectorAll('audio[src], video[src], source[src]')) {
        const src = media.getAttribute("src");
        if (/\/attachments\/\d+\/\d+/i.test(src || "")) addAttachment(src);
      }
      if (!attachments.size) {
        for (const image of accessories.querySelectorAll('img[src]')) {
          const src = image.getAttribute("src");
          if (isHttp(src) && /\/attachments\/\d+\/\d+/i.test(src || "")) addAttachment(src);
        }
      }
    }
    message.attachments = Array.from(attachments.values());

    const assetId = (url) => {
      try {
        return new URL(url, location.href).pathname.split("/").filter(Boolean).pop()?.split(".", 1)[0] || null;
      } catch {
        return null;
      }
    };
    const seenStickerUrls = new Set();
    const addSticker = (sticker) => {
      if (!sticker || (!sticker.id && !sticker.url)) return;
      const key = String(sticker.id || sticker.url);
      if (seenStickerUrls.has(key)) return;
      seenStickerUrls.add(key);
      message.stickers.push(sticker);
    };
    const seenEmojiUrls = new Set();
    for (const image of article.querySelectorAll('img[src]')) {
      const src = image.getAttribute("src");
      if (!isHttp(src)) continue;
      const lower = String(src).toLowerCase();
      const name = clean(image.getAttribute("alt") || image.getAttribute("aria-label") || fileName(src));
      if (/\/stickers\//i.test(lower) && !seenStickerUrls.has(src)) {
        const formatInfo = stickerFormatInfo(null, src);
        addSticker({ name: name || "sticker", id: assetId(src), url: src, mime: formatInfo.mime, format: formatInfo.format, animated: formatInfo.animated });
      } else if (/\/(?:emojis|emoji)\//i.test(lower) && !seenEmojiUrls.has(src)) {
        seenEmojiUrls.add(src);
        message.custom_emojis.push({ name: name || "custom emoji", id: assetId(src), url: src, mime: mimeFor(src) });
      }
    }

    for (const stickerNode of article.querySelectorAll('[data-type="sticker"][data-id]')) {
      const id = clean(stickerNode.getAttribute("data-id"));
      const formatType = clean(stickerNode.getAttribute("data-format-type"));
      const formatInfo = stickerFormatInfo(formatType, stickerNode.getAttribute("src"));
      const wrapper = stickerNode.closest('[role="img"][aria-label]');
      const label = clean(wrapper?.getAttribute("aria-label"));
      const labelMatch = label.match(/^Sticker,\s*([^,]+?)(?:,|$)/i);
      const name = clean(stickerNode.getAttribute("data-name") || labelMatch?.[1] || label || "sticker");
      const source = stickerNode.getAttribute("src");
      addSticker({
        name,
        id,
        url: isHttp(source) ? source : stickerReferenceUrl(id, formatType),
        mime: formatInfo.mime,
        format: formatInfo.format,
        animated: formatInfo.animated,
      });
    }

    if (accessories) {
      const embedRoots = new Set();
      const outermostEmbedRoot = (candidate) => {
        let root = candidate;
        while (root?.parentElement) {
          const ancestor = root.parentElement.closest('[class*="embed"]');
          if (!ancestor) break;
          root = ancestor;
        }
        return root;
      };
      for (const titleElement of accessories.querySelectorAll('[class*="embedTitle"]')) {
        const root = titleElement.closest('[class*="embed"]') || titleElement.parentElement;
        if (root) embedRoots.add(outermostEmbedRoot(root));
      }
      // Discord image-only link previews do not render an embedTitle node.
      // Promote the outermost media-bearing embed container so the rendered
      // image is retained even when the message body only contains a URL.
      for (const candidate of accessories.querySelectorAll('[class*="embed"]')) {
        if (!candidate.querySelector('img[src], video[src], audio[src]')) continue;
        const ancestorEmbed = candidate.parentElement?.closest('[class*="embed"]');
        if (!ancestorEmbed) embedRoots.add(candidate);
      }
      for (const root of embedRoots) {
        const titleElement = root.querySelector('[class*="embedTitle"]');
        const descriptionElement = root.querySelector('[class*="embedDescription"]');
        const providerElement = root.querySelector('[class*="embedProvider"]');
        const link = titleElement?.closest('a[href]')?.href || root.querySelector('a[href]')?.href;
        const images = Array.from(root.querySelectorAll('img[src]'))
          .map((element) => element.getAttribute("src"))
          .filter((src) => isHttp(src));
        const linkedImages = Array.from(root.querySelectorAll('a[data-role="img"][href]'))
          .map((element) => element.getAttribute("href"))
          .filter((src) => isHttp(src));
        const video = root.querySelector('video[src], video source[src]')?.getAttribute("src");
        const audio = root.querySelector('audio[src], audio source[src]')?.getAttribute("src");
        const embed = {};
        const title = clean(titleElement?.innerText || titleElement?.textContent);
        const description = clean(descriptionElement?.innerText || descriptionElement?.textContent);
        const provider = clean(providerElement?.innerText || providerElement?.textContent);
        if (title) embed.title = title;
        if (description) embed.description = description;
        if (provider) embed.site_name = provider;
        if (isHttp(link)) embed.url = link;
        const embedImages = [...new Set([...images, ...linkedImages])]
          .filter((src) => !/\/attachments\/\d+\/\d+/i.test(src));
        const hasThumbnailContainer = Boolean(root.querySelector('[class*="embedThumbnail"]'));
        if (hasThumbnailContainer && isHttp(embedImages[0])) embed.thumbnail_url = embedImages[0];
        else if (isHttp(embedImages[0])) embed.image_url = embedImages[0];
        if (isHttp(embedImages[1])) embed.thumbnail_url = embedImages[1];
        if (isHttp(video)) embed.video_url = video;
        if (isHttp(audio)) embed.audio_url = audio;
        if (!embed.image_url && !embed.thumbnail_url) {
          const derivedThumbnail = youtubeThumbnailUrl(embed.url);
          if (derivedThumbnail) {
            embed.thumbnail_url = derivedThumbnail;
            embed.thumbnail_source = "derived_youtube_thumbnail";
            embed.type = "video";
            embed.site_name = embed.site_name || "YouTube";
          }
        }
        if (Object.keys(embed).length) message.embeds.push(embed);
      }
    }

    if (!message.embeds.length) {
      const directLink = clean(content).match(/^https?:\/\/\S+$/i)?.[0];
      const derivedThumbnail = youtubeThumbnailUrl(directLink);
      if (derivedThumbnail) {
        message.embeds.push({
          url: directLink,
          thumbnail_url: derivedThumbnail,
          thumbnail_source: "derived_youtube_thumbnail",
          type: "video",
          site_name: "YouTube",
        });
      }
    }

    const reactionRoots = Array.from(article.querySelectorAll('[class*="reaction"]')).filter((element) => {
      const text = clean(element.innerText || element.textContent);
      return element.querySelector('[class*="reactionCount"]') || (/\d/.test(text) && text.length < 40);
    });
    const seenReactions = new Set();
    for (const root of reactionRoots) {
      const countElement = root.querySelector('[class*="reactionCount"]');
      const emojiElement = root.querySelector('img[alt], [class*="emoji"], [data-name]');
      const emoji = clean(emojiElement?.getAttribute("alt") || emojiElement?.getAttribute("data-name") || emojiElement?.textContent || root.textContent).replace(/\d+$/, "").trim();
      const countMatch = clean(countElement?.innerText || root.innerText).match(/\d+/);
      const key = `${emoji}:${countMatch?.[0] || "1"}`;
      if (!emoji || seenReactions.has(key)) continue;
      seenReactions.add(key);
      message.reactions.push({
        emoji,
        count: countMatch ? Number.parseInt(countMatch[0], 10) : 1,
        me: /reactionMe|isMe|selected/i.test(classes(root)),
      });
    }

    const replyElement = article.querySelector('[class*="repliedMessage"], [class*="repliedText"]');
    const replyLink = replyElement?.querySelector('a[href*="/channels/"]')?.href;
    const replyId = replyLink?.match(/\/([^/]+?)(?:[?#]|$)/)?.[1];
    if (replyId) message.reply_to = replyId;
    messages.push(message);
  }

  const orderedMessages = [...messages].sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)) || String(a.id).localeCompare(String(b.id)));
  const oldestMessage = orderedMessages[0] || null;
  const newestMessage = orderedMessages[orderedMessages.length - 1] || null;
  const visibleProfile = readVisibleProfile();
  if (visibleProfile) {
    const profileParticipantId = visibleProfile.participant_id
      || Array.from(participants.values()).find((participant) =>
        (visibleProfile.username && participant.username === visibleProfile.username)
        || (visibleProfile.display_name && participant.display_name === visibleProfile.display_name),
      )?.id;
    if (profileParticipantId) {
      const participant = participants.get(profileParticipantId) || { id: profileParticipantId };
      if (visibleProfile.display_name) participant.display_name = visibleProfile.display_name;
      if (visibleProfile.username) participant.username = visibleProfile.username;
      if (visibleProfile.avatar_ref) participant.avatar_ref = visibleProfile.avatar_ref;
      participant.profile = visibleProfile.profile;
      participants.set(profileParticipantId, participant);
    }
  }
  const captureRange = {
    version: 1,
    message_count: messages.length,
    oldest_message_id: oldestMessage?.id || null,
    oldest_timestamp: oldestMessage?.timestamp || null,
    newest_message_id: newestMessage?.id || null,
    newest_timestamp: newestMessage?.timestamp || null,
    ...scrollPosition,
  };
  const headingElement = document.querySelector('main h1, h1');
  const headingLines = String(headingElement?.innerText || document.title.replace(/^•\s*/, ""))
    .split(/\r?\n+/)
    .map(clean)
    .filter(Boolean);
  const heading = headingLines[0] || `Discord DM ${channelId}`;
  const channelHandle = headingLines[1] || null;
  const remoteMediaHosts = {};
  const addRemoteMediaHost = (value) => {
    if (!isHttp(value)) return;
    try {
      const host = new URL(value, location.href).hostname.toLowerCase().replace(/\.$/, "");
      if (host) remoteMediaHosts[host] = (remoteMediaHosts[host] || 0) + 1;
    } catch {
      // Ignore malformed references; the normalized importer will retain its source record.
    }
  };
  const diagnostics = {
    visible_message_nodes: messageNodes.length,
    rendered_message_count: messages.length,
    skipped_visible_nodes: Math.max(0, messageNodes.length - messages.length),
    attachments: messages.reduce((total, message) => total + message.attachments.length, 0),
    embeds: messages.reduce((total, message) => total + message.embeds.length, 0),
    embed_media: messages.reduce((total, message) => total + message.embeds.reduce((count, embed) => count + ["image_url", "thumbnail_url", "video_url", "audio_url"].filter((key) => Boolean(embed[key])).length, 0), 0),
    stickers: messages.reduce((total, message) => total + message.stickers.length, 0),
    custom_emojis: messages.reduce((total, message) => total + message.custom_emojis.length, 0),
    reactions: messages.reduce((total, message) => total + message.reactions.length, 0),
    replies: messages.filter((message) => Boolean(message.reply_to)).length,
    calls: messages.filter((message) => Boolean(message.call)).length,
    profile_captured: Boolean(visibleProfile),
    evidence: {
      dom_snapshot: false,
      screenshot: false,
      note: "For rendered proof, evaluate tools/discord_visible_evidence.js in the same attended tab, save its html value under private-data, and attach it with capture-session attach-evidence; save a tab screenshot separately.",
    },
  };
  for (const participant of participants.values()) {
    addRemoteMediaHost(participant.avatar_ref);
    const profile = participant.profile || {};
    addRemoteMediaHost(profile.banner_ref);
    addRemoteMediaHost(profile.avatar_decoration_ref);
    for (const badge of profile.badges || []) addRemoteMediaHost(badge.icon_ref);
    for (const friend of profile.mutual_friends || []) addRemoteMediaHost(friend.avatar_ref);
  }
  for (const message of messages) {
    for (const attachment of message.attachments) addRemoteMediaHost(attachment.url);
    for (const sticker of message.stickers) addRemoteMediaHost(sticker.url);
    for (const emoji of message.custom_emojis) addRemoteMediaHost(emoji.url);
    for (const embed of message.embeds) {
      for (const key of ["image_url", "thumbnail_url", "video_url", "audio_url"]) addRemoteMediaHost(embed[key]);
    }
  }
  diagnostics.remote_media_hosts = Object.fromEntries(Object.entries(remoteMediaHosts).sort(([a], [b]) => a.localeCompare(b)));
  return {
    metadata: {
      kind: "direct_message",
      title: heading || `Discord DM ${channelId}`,
      channel_handle: channelHandle,
      channel_id: channelId,
      captured_at: new Date().toISOString(),
      display_timezone: displayTimezone,
      capture_range: captureRange,
      capture_diagnostics: diagnostics,
      source: {
        label: "Discord visible conversation capture",
        url: location.href.split("#", 1)[0],
        notes: [
          "Captured from the user-opened direct message rendered in the browser.",
          "This capture contains only the messages currently rendered by Discord; load overlapping ranges and merge them to cover a virtualized history.",
          `Capture range: ${messages.length} rendered message(s), ${scrollPosition.at_start ? "at the beginning" : "not at the beginning"}, ${scrollPosition.at_end ? "at the end" : "not at the end"}.`,
          requestedDirection === "none"
            ? "No scroll movement was requested for this capture step."
            : `The browser UI was moved toward the ${requestedDirection} history boundary before this read-only capture${scrollPosition.moved_pixels === null ? "" : ` (${scrollPosition.moved_pixels} pixel(s))`}.`,
          "No login, user token, message sending, account search, or remote asset download was used.",
          "Remote media is preserved as reference-only URLs until explicitly copied into a private archive.",
          visibleProfile
            ? "Visible participant profile metadata was captured because the profile card was open during this attended step."
            : "No participant profile card was open during this capture step; profile metadata was not inferred.",
        ],
      },
    },
    participants: Array.from(participants.values()),
    messages,
  };
}
