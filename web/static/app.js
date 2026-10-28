// Twitch Drops Miner Web Client
// Socket.IO and API communication

// Global state
const state = {
    connected: false,
    channels: {},
    campaigns: {},
    settings: {},
    currentDrop: null,
    countdownTimer: null,  // Track the active countdown timer
    translations: {},  // Store current translations
    previewEnabled: false  // Explicit opt-in for this page only
};

// ==================== UI Utilities ====================

function showToast(message, type = 'info') {
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        document.body.appendChild(toastContainer);
    }
    
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    
    toastContainer.appendChild(toast);
    
    // Animate in
    requestAnimationFrame(() => {
        toast.style.opacity = '1';
        toast.style.transform = 'translateY(0)';
    });

    // Remove after 5 seconds
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}

function showConfirmModal(message, onConfirm) {
    if (document.querySelector('.modal-overlay')) return;
    const previousFocus = document.activeElement;
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    const modal = document.createElement('div');
    modal.className = 'modal-box';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'confirmation-message');
    const text = document.createElement('div');
    text.id = 'confirmation-message';
    text.className = 'modal-text';
    text.textContent = message;
    const btnContainer = document.createElement('div');
    btnContainer.className = 'modal-buttons';
    const t = state.translations;
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'secondary-btn';
    cancelBtn.textContent = t.gui?.settings?.cancel_btn || 'Cancel';
    const confirmBtn = document.createElement('button');
    confirmBtn.className = 'primary-btn';
    confirmBtn.textContent = t.gui?.settings?.confirm_btn || 'Confirm';
    btnContainer.appendChild(cancelBtn);
    btnContainer.appendChild(confirmBtn);
    modal.appendChild(text);
    modal.appendChild(btnContainer);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    cancelBtn.focus();
    requestAnimationFrame(() => {
        overlay.style.opacity = '1';
        modal.style.transform = 'scale(1)';
    });
    let closed = false;
    const close = (confirmed = false) => {
        if (closed) return;
        closed = true;
        overlay.remove();
        if (previousFocus?.isConnected) previousFocus.focus();
        if (confirmed) onConfirm();
    };
    cancelBtn.onclick = () => close();
    confirmBtn.onclick = () => close(true);
    overlay.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            event.preventDefault();
            close();
        } else if (event.key === 'Tab') {
            event.preventDefault();
            (document.activeElement === cancelBtn ? confirmBtn : cancelBtn).focus();
        }
    });
}

// ==================== Version Checking ====================

async function fetchAndDisplayVersion() {
    try {
        const response = await fetch('/api/version');
        if (!response.ok) throw new Error('Failed to fetch version');

        const data = await response.json();
        const versionElement = document.getElementById('current-version');
        if (versionElement) {
            let versionText = data.current_version;
            // Add (latest) indicator if we know the latest version and it matches
            if (data.latest_version && data.current_version === data.latest_version) {
                versionText += ' (latest)';
            }
            versionElement.textContent = versionText;

            // Translate footer version text
            const footerVersionText = document.getElementById('footer-version-text');
            if (footerVersionText && state.translations.gui?.footer) {
                const versionLabel = state.translations.gui.footer.version || 'Version:';
                // Preserve the span inside
                const span = footerVersionText.querySelector('span');
                footerVersionText.textContent = versionLabel + ' ';
                footerVersionText.appendChild(span);
            }
        }

        // Display update notification if available
        if (data.update_available && data.latest_version) {
            const updateIndicator = document.getElementById('footer-update-indicator');
            const latestVersionSpan = document.getElementById('latest-version');
            const updateLink = document.getElementById('footer-update-link');

            if (updateIndicator && latestVersionSpan && updateLink) {
                latestVersionSpan.textContent = data.latest_version;
                updateLink.href = data.download_url;
                updateIndicator.style.display = 'inline-block';

                // Translate update message
                if (state.translations.gui?.footer) {
                    const updateLabel = state.translations.gui.footer.update_available || 'Update Available:';
                    const linkText = document.createTextNode(` ⚠ ${updateLabel} `);
                    // Clear existing text nodes but keep the span
                    const span = updateLink.querySelector('span'); // latest-version span
                    updateLink.textContent = '';
                    updateLink.appendChild(linkText);
                    updateLink.appendChild(span);
                }

                // Log to console
                console.log(`Update available: ${data.latest_version} (current: ${data.current_version})`);
            }
        }
    } catch (error) {
        console.warn('Could not fetch version information:', error);
        // Set placeholder text if fetch fails
        const versionElement = document.getElementById('current-version');
        const loadingText = state.translations.gui?.footer?.loading || 'Loading...';
        if (versionElement && versionElement.textContent === loadingText) {
            versionElement.textContent = 'Unknown';
        }
    }
}

// Initialize Socket.IO connection
const socket = io({
    transports: ['websocket', 'polling'],
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
    reconnectionAttempts: Infinity
});

// ==================== Socket.IO Event Handlers ====================

socket.on('connect', () => {
    console.log('Connected to server');
    state.connected = true;
    globalThis.browserLoginPanel?.load();
    const connText = state.translations.gui?.websocket?.connected || 'Connected';
    document.getElementById('connection-indicator').textContent = '● ' + connText;
    document.getElementById('connection-indicator').className = 'connected';
});

socket.on('disconnect', () => {
    console.log('Disconnected from server');
    state.connected = false;
    const disconnText = state.translations.gui?.websocket?.disconnected || 'Disconnected';
    document.getElementById('connection-indicator').textContent = '● ' + disconnText;
    document.getElementById('connection-indicator').className = 'disconnected';
});

socket.on('initial_state', (data) => {
    console.log('Received initial state', data);
    if (data.status) updateStatus(data.status);

    // Batch update channels to prevent UI freezing
    if (data.channels) {
        data.channels.forEach(ch => {
            state.channels[ch.id] = ch;
        });
        renderChannels();
    }

    // Batch update campaigns to prevent UI freezing
    if (data.campaigns) {
        data.campaigns.forEach(camp => {
            state.campaigns[camp.id] = camp;
        });
        renderInventory();
    }

    // Batch update console logs
    if (data.console) {
        const consoleEl = document.getElementById('console-output');
        const fragment = document.createDocumentFragment();
        data.console.forEach(line => {
            const div = document.createElement('div');
            div.textContent = line;
            fragment.appendChild(div);
        });
        consoleEl.appendChild(fragment);
        consoleEl.scrollTop = consoleEl.scrollHeight;
        while (consoleEl.children.length > 1000) {
            consoleEl.removeChild(consoleEl.firstChild);
        }
    }

    if (data.settings) updateSettingsUI(data.settings, true);
    if (data.login) updateLoginStatus(data.login);
    if (data.manual_mode) updateManualModeUI(data.manual_mode);
    // Restore current drop progress if it exists
    if (data.current_drop) {
        updateDropProgress(data.current_drop);
    } else {
        clearDropProgress();
    }

    if (data.wanted_items) {
        renderWantedItems(data.wanted_items);
    }
});

socket.on('status_update', (data) => {
    updateStatus(data.status);
});

socket.on('console_output', (data) => {
    addConsoleLine(data.message);
});

socket.on('channel_add', (data) => {
    updateChannel(data);
});

socket.on('channel_update', (data) => {
    updateChannel(data);
});

socket.on('channel_remove', (data) => {
    removeChannel(data.id);
});

socket.on('channels_clear', () => {
    clearChannels();
});

socket.on('channels_batch_update', (data) => {
    // Replace all channels atomically to prevent flickering
    state.channels = {};
    data.channels.forEach(ch => {
        state.channels[ch.id] = ch;
    });
    renderChannels();
});

socket.on('channel_watching', (data) => {
    setWatchingChannel(data.id);
});

socket.on('channel_watching_clear', () => {
    clearWatchingChannel();
});

socket.on('drop_progress', (data) => {
    updateDropProgress(data);
});

socket.on('drop_progress_stop', () => {
    clearDropProgress();
});

socket.on('campaign_add', (data) => {
    addCampaign(data);
});

socket.on('inventory_clear', () => {
    clearInventory();
});

socket.on('inventory_batch_update', (data) => {
    // Replace all campaigns atomically to prevent flickering
    state.campaigns = {};
    data.campaigns.forEach(camp => {
        state.campaigns[camp.id] = camp;
    });
    renderInventory();
    refreshGamesIfLinkStatusChanged();
});

socket.on('drop_update', (data) => {
    updateDrop(data.campaign_id, data.drop, data.campaign, data.drops);
});

socket.on('login_status', (data) => {
    updateLoginStatus(data);
});

socket.on('session_status', (data) => {
    globalThis.browserLoginPanel?.updateStatus(data);
});

socket.on('settings_updated', (data) => {
    updateSettingsUI(data, true);
});

socket.on('games_available', (data) => {
    state.availableGames = data.games;
});

socket.on('theme_change', (data) => {
    if (data.dark_mode) {
        document.body.classList.add('dark-mode');
    } else {
        document.body.classList.remove('dark-mode');
    }
});

socket.on('notification', (data) => {
    if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(data.title, {
            body: data.message,
            icon: '/static/icon.png'
        });
    }
});

socket.on('attention_required', (data) => {
    if (data.sound) {
        // Play notification sound
        const audio = new Audio('/static/notification.mp3');
        audio.play().catch(() => { });
    }
    // Flash title
    flashTitle();
});

socket.on('manual_mode_update', (data) => {
    updateManualModeUI(data);
});

socket.on('language_changed', (data) => {
    console.log('Language changed to:', data.language);
    fetchAndApplyTranslations();
});

socket.on('wanted_items_update', (data) => {
    renderWantedItems(data);
});

// ==================== UI Update Functions ====================

function updateStatus(status) {
    document.getElementById('status-text').textContent = status;

    // Loading overlay disabled - UI remains responsive during backend operations
    // Backend now uses batch updates to prevent flickering
}

function addConsoleLine(message) {
    addConsoleLineRaw(message);
}

function addConsoleLineRaw(line) {
    const console = document.getElementById('console-output');
    const div = document.createElement('div');
    div.textContent = line;
    console.appendChild(div);
    // Auto-scroll to bottom
    console.scrollTop = console.scrollHeight;
    // Limit lines
    while (console.children.length > 1000) {
        console.removeChild(console.firstChild);
    }
}

function updateChannel(channelData) {
    state.channels[channelData.id] = channelData;
    renderChannels();
}

function removeChannel(channelId) {
    delete state.channels[channelId];
    renderChannels();
}

function clearChannels() {
    state.channels = {};
    renderChannels();
}

function setWatchingChannel(channelId) {
    Object.values(state.channels).forEach(ch => ch.watching = false);
    if (state.channels[channelId]) {
        state.channels[channelId].watching = true;
    }
    renderChannels();
}

function clearWatchingChannel() {
    Object.values(state.channels).forEach(ch => ch.watching = false);
    renderChannels();
}

function channelMatchesGameFilter(channel, gamesToWatchSet) {
    return channel.watching ||
        gamesToWatchSet.size === 0 ||
        Boolean(channel.game && gamesToWatchSet.has(channel.game.toLowerCase()));
}

function updateNowWatching() {
    const card = document.getElementById('now-watching');
    if (!card) return;
    const channel = Object.values(state.channels).find(ch => ch.watching);
    const t = state.translations.gui?.channels || {};
    const img = document.getElementById('now-watching-img');
    const preview = document.getElementById('now-watching-preview');
    const button = document.getElementById('now-watching-toggle');
    if (!channel) state.previewEnabled = false;
    const enabled = state.previewEnabled && Boolean(channel?.login);
    preview.hidden = !enabled;
    button.disabled = !channel?.login;
    button.setAttribute('aria-pressed', String(Boolean(enabled)));
    button.textContent = enabled ? (t.hide_preview || 'Hide thumbnail') : (t.show_preview || 'Show thumbnail');
    button.title = t.preview_help || 'Loads Twitch thumbnails and uses extra bandwidth. Off on each page load.';
    document.getElementById('now-watching-title').textContent = t.now_watching || 'Now Watching';
    document.getElementById('now-watching-live').textContent = t.online || 'Online';
    document.getElementById('now-watching-off').textContent = t.preview_off || 'Thumbnail is off';
    document.getElementById('now-watching-off').hidden = enabled;
    if (!enabled) {
        img.style.backgroundImage = '';
        delete img.dataset.src;
    }
    if (!channel) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');
    if (enabled) {
        const login = String(channel.login).toLowerCase();
        // Only opted-in pages load thumbnails; channel events refresh at most once per minute.
        const url = `https://static-cdn.jtvnw.net/previews-ttv/live_user_${encodeURIComponent(login)}-440x248.jpg?t=${Math.floor(Date.now() / 60000)}`;
        if (img.dataset.src !== url) {
            img.dataset.src = url;
            img.style.backgroundImage = `url("${url}")`;
        }
    }
    const viewersText = state.translations.gui?.channels?.viewers || 'viewers';
    const viewers = channel.viewers !== null && channel.viewers !== undefined
        ? ` | ${Number(channel.viewers).toLocaleString()} ${viewersText}`
        : '';
    document.getElementById('now-watching-info').textContent = channel.name + viewers;
}

function toggleNowWatchingPreview() {
    state.previewEnabled = !state.previewEnabled;
    updateNowWatching();
}

function renderChannels() {
    updateNowWatching();
    const container = document.getElementById('channels-list');
    container.innerHTML = '';

    const t = state.translations;
    const channels = Object.values(state.channels);
    if (channels.length === 0) {
        const emptyMsg = t.gui?.channels?.no_channels || 'No channels tracked yet...';
        container.replaceChildren(
            makeElement('p', { class: 'empty-message' }, emptyMsg),
        );
        return;
    }

    // Get the games to watch list from settings
    const gamesToWatch = state.settings.games_to_watch || [];
    const gamesToWatchSet = new Set(gamesToWatch.map(g => g.toLowerCase()));

    // Only an explicit opt-in exposes fallback games. Keep the watched channel
    // visible while a settings update changes the selected-game filter.
    const filteredChannels = channels.filter(
        channel => state.settings.priority_list_only === false ||
            channelMatchesGameFilter(channel, gamesToWatchSet)
    );

    if (filteredChannels.length === 0) {
        const emptyMsg = t.gui?.channels?.no_channels_for_games || 'No channels found for selected games...';
        container.replaceChildren(
            makeElement('p', { class: 'empty-message' }, emptyMsg),
        );
        return;
    }

    // Flat list: watching first, then online, then by viewers
    const sortedChannels = [...filteredChannels].sort((a, b) => {
        if (Boolean(a.watching) !== Boolean(b.watching)) return b.watching ? 1 : -1;
        if (Boolean(a.online) !== Boolean(b.online)) return b.online ? 1 : -1;
        return (b.viewers || 0) - (a.viewers || 0);
    });

    const formatViewers = n => {
        if (n >= 1e6) return `${+(n / 1e6).toFixed(2)}M`;
        if (n >= 1e3) return `${+(n / 1e3).toFixed(2)}K`;
        return String(n);
    };

    sortedChannels.forEach(channel => {
        const div = document.createElement('div');
        div.className = 'channel-item';
        if (channel.watching) div.classList.add('watching');
        div.classList.add(channel.online ? 'online' : 'offline');

        const name = String(channel.name || '?');
        let hue = 0;
        for (const ch of name) hue = (hue * 31 + ch.charCodeAt(0)) % 360;
        const avatar = makeElement('span', { class: 'channel-avatar' }, name[0].toUpperCase());
        avatar.style.background = `hsl(${hue}, 45%, 38%)`;

        const nameDiv = makeElement('div', { class: 'channel-name' }, name, el => {
            if (channel.drops_enabled) {
                el.appendChild(document.createTextNode(' '));
                el.appendChild(makeElement('span', { class: 'channel-badge drops' }, 'DROPS'));
            }
            if (channel.acl_based) {
                el.appendChild(document.createTextNode(' '));
                el.appendChild(makeElement('span', { class: 'channel-badge acl' }, 'ACL'));
            }
        });
        const stateDiv = makeElement('div', { class: 'channel-info' }, channel.online ? t.gui?.channels?.online : t.gui?.channels?.offline, el => {
            if (channel.game) {
                el.appendChild(makeElement('span', { class: 'channel-game' }, ` · ${channel.game}`));
            }
        });
        const textDiv = makeElement('div', { class: 'channel-text' }, '', el => {
            el.appendChild(nameDiv);
            el.appendChild(stateDiv);
        });

        const viewersDiv = makeElement('span', { class: 'channel-viewers' },
            channel.viewers !== null && channel.viewers !== undefined ? formatViewers(channel.viewers) : '');
        const link = makeElement('a', {
            class: 'channel-ext',
            href: channel.url,
            target: '_blank',
            rel: 'noopener noreferrer',
            title: name,
            'aria-label': name,
        });
        link.addEventListener('click', e => e.stopPropagation());

        div.replaceChildren(avatar, textDiv, viewersDiv, link);
        div.onclick = () => selectChannel(channel.id);
        container.appendChild(div);
    });
}

function updateDropArt() {
    const art = document.getElementById('drop-art');
    if (!art) return;
    const drop = state.currentDrop;
    const campaign = drop && (state.campaigns[drop.campaign_id] ||
        Object.values(state.campaigns).find(c => c.game_name === drop.game_name));
    const raw = campaign?.game_box_art_url;
    if (!raw) {
        art.classList.add('hidden');
        return;
    }
    const url = raw.replace('{width}', '285').replace('{height}', '380');
    if (art.dataset.src !== url) {
        art.dataset.src = url;
        art.querySelector('.drop-art-bg').style.backgroundImage = `url("${url}")`;
        art.querySelector('.drop-art-poster').src = url;
    }
    art.querySelector('.drop-art-poster').alt = drop.game_name || '';
    art.classList.remove('hidden');
}

function updateDropProgress(data) {
    // Check if this is a new drop or if remaining seconds changed significantly
    const isNewDrop = !state.currentDrop || state.currentDrop.drop_id !== data.drop_id;

    // Store old remaining seconds before updating state
    const oldRemaining = state.currentDrop ? state.currentDrop.remaining_seconds : null;

    // Update state with new data
    state.currentDrop = data;

    document.getElementById('no-drop-message').style.display = 'none';
    document.getElementById('drop-info').style.display = 'block';

    document.getElementById('drop-name').textContent = data.drop_name;
    updateDropArt();

    // Make campaign name clickable with link to Twitch
    const dropGameEl = document.getElementById('drop-game');
    if (data.campaign_id) {
        const campaignUrl = `https://www.twitch.tv/drops/campaigns?dropID=${data.campaign_id}`;
        dropGameEl.replaceChildren(
            makeElement('a', { href: campaignUrl, target: '_blank', rel: 'noopener noreferrer', class: 'drop-campaign-link' }, data.campaign_name),
            document.createTextNode(` (${data.game_name})`),
        );
    } else {
        dropGameEl.textContent = `${data.campaign_name} (${data.game_name})`;
    }

    const progress = data.progress * 100;
    const fill = document.getElementById('progress-fill');
    fill.style.width = `${progress}%`;
    fill.textContent = `${Math.round(progress)}%`;

    document.getElementById('progress-text').textContent =
        `${data.current_minutes} / ${data.required_minutes} minutes`;

    // Only reset the timer if it's a new drop or if backend time differs by more than 2 seconds
    // This prevents constant timer resets from periodic backend updates
    const shouldResetTimer = isNewDrop || oldRemaining === null || Math.abs(oldRemaining - data.remaining_seconds) > 2;

    if (shouldResetTimer) {
        // Cancel any existing countdown timer before starting a new one
        if (state.countdownTimer !== null) {
            clearTimeout(state.countdownTimer);
            state.countdownTimer = null;
        }

        // Start countdown with the new value from backend
        updateRemainingTime(data.remaining_seconds);
    }
    // Otherwise, let the existing timer continue counting down smoothly
}

function updateRemainingTime(seconds) {
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    document.getElementById('progress-time').textContent =
        `Time remaining: ${minutes}:${secs.toString().padStart(2, '0')}`;

    if (seconds > 0) {
        // Store the timer ID so we can cancel it if needed
        state.countdownTimer = setTimeout(() => updateRemainingTime(seconds - 1), 1000);
    } else {
        state.countdownTimer = null;
    }
}

function clearDropProgress() {
    state.currentDrop = null;

    // Cancel any active countdown timer
    if (state.countdownTimer !== null) {
        clearTimeout(state.countdownTimer);
        state.countdownTimer = null;
    }

    document.getElementById('no-drop-message').style.display = 'block';
    document.getElementById('drop-info').style.display = 'none';
}

function addCampaign(campaignData) {
    state.campaigns[campaignData.id] = campaignData;
    renderInventory();
    refreshGamesIfLinkStatusChanged();
}

function clearInventory() {
    state.campaigns = {};
    renderInventory();
    refreshGamesIfLinkStatusChanged();
}

function updateDrop(campaignId, dropData, campaignData = {}, dropsData = null) {
    const campaign = state.campaigns[campaignId];
    if (campaign) {
        Object.assign(campaign, campaignData);
        if (Array.isArray(dropsData)) {
            campaign.drops = dropsData;
            renderInventory();
            return;
        }
        const drops = campaign.drops;
        const index = drops.findIndex(d => d.id === dropData.id);
        if (index !== -1) {
            drops[index] = dropData;
            renderInventory();
        }
    }
}

// ==================== Inventory Filtering ====================

function getInventoryFilters() {
    // Get filter state from UI checkboxes and selected games array
    return {
        show_active: document.getElementById('filter-active')?.checked || false,
        link_status: document.getElementById('filter-link-status')?.value || 'all',
        show_upcoming: document.getElementById('filter-upcoming')?.checked || false,
        show_expired: document.getElementById('filter-expired')?.checked || false,
        show_finished: document.getElementById('filter-finished')?.checked || false,
        game_name_search: [...selectedInventoryGames],  // Array of selected game names
        // Benefit type filters (default to true if checkbox doesn't exist)
        show_benefit_item: document.getElementById('filter-benefit-item')?.checked !== false,
        show_benefit_badge: document.getElementById('filter-benefit-badge')?.checked !== false,
        show_benefit_emote: document.getElementById('filter-benefit-emote')?.checked !== false,
        show_benefit_other: document.getElementById('filter-benefit-other')?.checked !== false
    };
}


function campaignMatchesFilters(campaign, filters) {
    const isFinished = campaign.mining_finished ??
        (campaign.total_drops > 0 && campaign.claimed_drops === campaign.total_drops);

    const hasGameFilter = filters.game_name_search && filters.game_name_search.length > 0;

    // Finished campaigns are excluded unless the user explicitly includes them.
    if (!filters.show_finished && isFinished) return false;

    // Link state narrows the status result instead of joining its OR group.
    if (filters.link_status === 'linked' && !campaign.linked) return false;
    if (filters.link_status === 'not_linked' && campaign.linked) return false;

    // Active, upcoming, and expired remain OR-based status filters.
    const hasStatusFilters = filters.show_active || filters.show_upcoming || filters.show_expired;

    if (hasStatusFilters) {
        const statusMatch = (filters.show_active && campaign.active) ||
            (filters.show_upcoming && campaign.upcoming) ||
            (filters.show_expired && campaign.expired);
        if (!statusMatch) return false;
    }

    // Check game name filter (AND logic with status filters, OR logic among selected games)
    if (hasGameFilter) {
        const gameName = campaign.game_name;
        // Campaign must match at least ONE of the selected games
        const gameMatch = filters.game_name_search.includes(gameName);
        if (!gameMatch) {
            return false;
        }
    }

    // Check benefit type filter - campaign must have at least one drop with a matching benefit type
    // Only filter if at least one benefit type is UNCHECKED (otherwise show all)
    const allBenefitsEnabled = filters.show_benefit_item && filters.show_benefit_badge &&
        filters.show_benefit_emote && filters.show_benefit_other;

    if (!allBenefitsEnabled && campaign.drops) {
        let benefitMatch = false;
        for (const drop of campaign.drops) {
            if (drop.benefits && drop.benefits.length > 0) {
                for (const benefit of drop.benefits) {
                    const benefitType = (benefit.type || '').toUpperCase();
                    // Map filter checkboxes to actual API benefit types
                    if (filters.show_benefit_item && benefitType === 'DIRECT_ENTITLEMENT') benefitMatch = true;
                    if (filters.show_benefit_badge && benefitType === 'BADGE') benefitMatch = true;
                    if (filters.show_benefit_emote && benefitType === 'EMOTE') benefitMatch = true;
                    if (filters.show_benefit_other && benefitType === 'UNKNOWN') benefitMatch = true;
                }
            }
        }
        if (!benefitMatch) {
            return false;
        }
    }


    return true;
}


function onInventoryFilterChange() {
    // Save filter state to settings and re-render inventory
    saveSettings();
    renderInventory();
}

function clearInventoryFilters() {
    // Uncheck all filter checkboxes
    document.getElementById('filter-active').checked = false;
    document.getElementById('filter-link-status').value = 'all';
    document.getElementById('filter-upcoming').checked = false;
    document.getElementById('filter-expired').checked = false;
    document.getElementById('filter-finished').checked = false;
    document.getElementById('inventory-game-search').value = '';

    // Reset benefit type filters to checked (show all)
    if (document.getElementById('filter-benefit-item')) document.getElementById('filter-benefit-item').checked = true;
    if (document.getElementById('filter-benefit-badge')) document.getElementById('filter-benefit-badge').checked = true;
    if (document.getElementById('filter-benefit-emote')) document.getElementById('filter-benefit-emote').checked = true;
    if (document.getElementById('filter-benefit-other')) document.getElementById('filter-benefit-other').checked = true;

    // Clear selected games
    selectedInventoryGames = [];
    updateGameTagsDisplay();

    // Save and re-render
    saveSettings();
    renderInventory();
}


// ==================== Game Dropdown & Tags ====================

// Track selected games for inventory filter
let selectedInventoryGames = [];
let gameDropdownFocusedIndex = -1;
let gameDropdownVisible = false;

function getAvailableGamesForDropdown() {
    // Combine games from campaigns and availableGames Set
    const gamesFromCampaigns = Object.values(state.campaigns).map(c => c.game_name);
    const gamesFromSettings = Array.from(availableGames || []);

    // Merge and deduplicate
    const allGames = [...new Set([...gamesFromCampaigns, ...gamesFromSettings])];

    // Sort alphabetically
    return allGames.sort((a, b) => a.localeCompare(b));
}

function renderGameDropdown(searchTerm = '') {
    const dropdown = document.getElementById('game-dropdown-list');
    const allGames = getAvailableGamesForDropdown();

    // Filter games by search term (case-insensitive)
    const searchLower = searchTerm.toLowerCase().trim();
    const filteredGames = searchLower
        ? allGames.filter(game => game.toLowerCase().includes(searchLower))
        : allGames;

    dropdown.innerHTML = '';

    if (filteredGames.length === 0) {
        dropdown.replaceChildren(makeElement('div', { class: 'dropdown-item no-results' }, 'No games found'));
        gameDropdownFocusedIndex = -1;
        return;
    }

    filteredGames.forEach((gameName, index) => {
        const isSelected = selectedInventoryGames.includes(gameName);
        const isFocused = index === gameDropdownFocusedIndex;

        const item = document.createElement('div');
        item.className = 'dropdown-item' + (isFocused ? ' focused' : '');
        item.dataset.gameName = gameName;
        item.dataset.index = index;

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = isSelected;
        checkbox.id = `game-dropdown-${index}`;

        const label = document.createElement('label');
        label.setAttribute('for', `game-dropdown-${index}`);
        label.textContent = gameName;

        item.appendChild(checkbox);
        item.appendChild(label);

        // Click handler for the entire item
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleGameSelection(gameName);
        });

        dropdown.appendChild(item);
    });
}

function toggleGameSelection(gameName) {
    const index = selectedInventoryGames.indexOf(gameName);
    if (index >= 0) {
        // Remove game
        selectedInventoryGames.splice(index, 1);
    } else {
        // Add game
        selectedInventoryGames.push(gameName);
    }

    updateGameTagsDisplay();
    renderGameDropdown(document.getElementById('inventory-game-search').value);
    saveSettings();
    renderInventory();
}

function removeGameTag(gameName) {
    const index = selectedInventoryGames.indexOf(gameName);
    if (index >= 0) {
        selectedInventoryGames.splice(index, 1);
        updateGameTagsDisplay();
        renderGameDropdown(document.getElementById('inventory-game-search').value);
        saveSettings();
        renderInventory();
    }
}

function updateGameTagsDisplay() {
    const container = document.getElementById('selected-game-tags');
    container.innerHTML = '';

    selectedInventoryGames.forEach(gameName => {
        const tag = document.createElement('div');
        tag.className = 'game-tag';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'game-tag-name';
        nameSpan.textContent = gameName;

        const removeBtn = document.createElement('button');
        removeBtn.className = 'game-tag-remove';
        removeBtn.textContent = '×';
        removeBtn.setAttribute('aria-label', `Remove ${gameName}`);
        removeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            removeGameTag(gameName);
        });

        tag.appendChild(nameSpan);
        tag.appendChild(removeBtn);
        container.appendChild(tag);
    });
}

function showGameDropdown() {
    const dropdown = document.getElementById('game-dropdown-list');
    dropdown.style.display = 'block';
    gameDropdownVisible = true;
    gameDropdownFocusedIndex = -1;
    renderGameDropdown(document.getElementById('inventory-game-search').value);
}

function closeGameDropdown() {
    const dropdown = document.getElementById('game-dropdown-list');
    dropdown.style.display = 'none';
    gameDropdownVisible = false;
    gameDropdownFocusedIndex = -1;
}

function handleGameSearchKeydown(event) {
    if (!gameDropdownVisible) {
        return;
    }

    const dropdown = document.getElementById('game-dropdown-list');
    const items = dropdown.querySelectorAll('.dropdown-item:not(.no-results)');
    const maxIndex = items.length - 1;

    if (event.key === 'ArrowDown') {
        event.preventDefault();
        gameDropdownFocusedIndex = Math.min(gameDropdownFocusedIndex + 1, maxIndex);
        renderGameDropdown(document.getElementById('inventory-game-search').value);

        // Scroll focused item into view
        const focusedItem = dropdown.querySelector('.dropdown-item.focused');
        if (focusedItem) {
            focusedItem.scrollIntoView({ block: 'nearest' });
        }
    } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        gameDropdownFocusedIndex = Math.max(gameDropdownFocusedIndex - 1, 0);
        renderGameDropdown(document.getElementById('inventory-game-search').value);

        // Scroll focused item into view
        const focusedItem = dropdown.querySelector('.dropdown-item.focused');
        if (focusedItem) {
            focusedItem.scrollIntoView({ block: 'nearest' });
        }
    } else if (event.key === 'Enter') {
        event.preventDefault();
        if (gameDropdownFocusedIndex >= 0 && gameDropdownFocusedIndex <= maxIndex) {
            const focusedItem = items[gameDropdownFocusedIndex];
            const gameName = focusedItem.dataset.gameName;
            if (gameName) {
                toggleGameSelection(gameName);
            }
        }
    } else if (event.key === 'Escape') {
        event.preventDefault();
        closeGameDropdown();
        document.getElementById('inventory-game-search').blur();
    }
}

// Cards stay in fixed columns, so opening one only pushes down the cards below it.
function inventoryColumnCount(container) {
    if (container.classList.contains('list-view')) return 1;
    const style = getComputedStyle(container);
    const min = parseFloat(style.getPropertyValue('--inventory-card-min')) || 350;
    const gap = parseFloat(style.columnGap) || 0;
    return Math.max(1, Math.floor((container.clientWidth + gap) / (min + gap)));
}

function applyInventoryViewMode(listView) {
    const grid = document.getElementById('inventory-grid');
    grid.classList.toggle('list-view', listView);
    if (grid.dataset.columns && String(inventoryColumnCount(grid)) !== grid.dataset.columns) renderInventory();
    document.getElementById('inventory-view-cards')?.setAttribute('aria-pressed', String(!listView));
    document.getElementById('inventory-view-list')?.setAttribute('aria-pressed', String(listView));
}

function setInventoryViewMode(listView) {
    if ((state.settings.inventory_list_view === true) === listView) return;
    state.settings.inventory_list_view = listView;
    applyInventoryViewMode(listView);
    // Saving before the first settings load would persist empty defaults (e.g. the Mining list).
    if (Array.isArray(state.settings.games_to_watch)) saveSettings();
}

const expandedInventoryCampaigns = new Set();
let inventoryWatchSavePending = false;
let inventoryWatchPendingGame = null;
const gameWatchWrites = { confirmed: null, draft: null, pending: [], tail: Promise.resolve(), revision: 0 };

function applyGameWatchEdit(edit, current) {
    const key = name => name.toLowerCase();
    const unique = games => {
        const seen = new Set();
        return games.filter(name => {
            if (seen.has(key(name))) return false;
            seen.add(key(name));
            return true;
        });
    };
    if (edit.append) return current.some(name => key(name) === key(edit.append))
        ? [...current] : [...current, edit.append];
    const known = new Set(edit.before.map(key));
    const existing = new Set(current.map(key));
    return unique([
        ...edit.after.filter(name => known.has(key(name)) && existing.has(key(name))),
        ...current.filter(name => !known.has(key(name))),
        ...edit.after.filter(name => !known.has(key(name)))
    ]);
}

function reconcileGameWatchSettings(settings) {
    if (!Array.isArray(settings.games_to_watch)) return settings;
    if (JSON.stringify(gameWatchWrites.confirmed) !== JSON.stringify(settings.games_to_watch)) gameWatchWrites.revision++;
    gameWatchWrites.confirmed = [...settings.games_to_watch];
    gameWatchWrites.draft = gameWatchWrites.pending.reduce(
        (games, pending) => applyGameWatchEdit(pending, games), gameWatchWrites.confirmed);
    return gameWatchWrites.pending.length && JSON.stringify(gameWatchWrites.draft) !== JSON.stringify(settings.games_to_watch)
        ? { ...settings, games_to_watch: [...gameWatchWrites.draft] } : settings;
}

function queueSettingsWrite(settings, appendGame = null) {
    if (gameWatchWrites.confirmed === null) {
        gameWatchWrites.confirmed = [...(state.settings.games_to_watch || [])];
        gameWatchWrites.draft = [...gameWatchWrites.confirmed];
    }
    const edit = { before: [...gameWatchWrites.draft],
        after: [...(settings.games_to_watch || gameWatchWrites.draft)], append: appendGame };
    gameWatchWrites.pending.push(edit);
    const refresh = () => {
        gameWatchWrites.draft = gameWatchWrites.pending.reduce(
            (games, pending) => applyGameWatchEdit(pending, games), gameWatchWrites.confirmed);
        if (JSON.stringify(state.settings.games_to_watch) !== JSON.stringify(gameWatchWrites.draft)) {
            state.settings.games_to_watch = [...gameWatchWrites.draft];
        }
        renderGamesToWatch();
        renderChannels();
        renderInventory();
    };
    refresh();
    const operation = gameWatchWrites.tail.then(async () => {
        try {
            const games = applyGameWatchEdit(edit, gameWatchWrites.confirmed);
            const revision = gameWatchWrites.revision;
            if (appendGame && gameWatchWrites.confirmed.some(name => name.toLowerCase() === appendGame.toLowerCase())) return;
            const response = await fetch('/api/settings', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...settings, games_to_watch: games })
            });
            if (!response.ok) throw new Error('settings_save_failed');
            const data = await response.json();
            if (data.success !== true || !Array.isArray(data.settings?.games_to_watch)
                || (appendGame && !data.settings.games_to_watch.includes(appendGame))) throw new Error('settings_save_failed');
            if (gameWatchWrites.revision === revision
                || JSON.stringify(gameWatchWrites.confirmed) === JSON.stringify(games)) {
                gameWatchWrites.confirmed = [...data.settings.games_to_watch];
            }
            return data;
        } finally {
            gameWatchWrites.pending.splice(gameWatchWrites.pending.indexOf(edit), 1);
            refresh();
        }
    });
    gameWatchWrites.tail = operation.catch(() => {});
    return operation;
}

function canAddInventoryGame(campaign) {
    return campaign?.linked === true && typeof campaign.game_name === 'string'
        && campaign.game_name.trim().length > 0
        && Array.isArray(state.settings.games_to_watch)
        && !state.settings.games_to_watch.some(
            game => game.toLowerCase() === campaign.game_name.toLowerCase());
}

async function addInventoryGame(campaignId, button) {
    const campaign = state.campaigns[campaignId];
    if (inventoryWatchSavePending || !canAddInventoryGame(campaign)) return;
    const hadFocus = document.activeElement === button;
    inventoryWatchSavePending = true;
    inventoryWatchPendingGame = campaign.game_name;
    const t = state.translations.gui?.inventory || {};
    document.querySelectorAll('.inventory-add-game').forEach(control => { control.disabled = true; });
    button.textContent = t.adding_game || 'Adding…';
    button.setAttribute('aria-busy', 'true');
    try {
        await queueSettingsWrite({}, campaign.game_name);
    } catch (_) {
        showToast(t.add_game_error || 'Could not save mining list. Try again.', 'error');
    } finally {
        const restoreFocus = hadFocus && (document.activeElement === button || document.activeElement === document.body);
        inventoryWatchSavePending = false;
        inventoryWatchPendingGame = null;
        renderInventory();
        if (restoreFocus) {
            const target = document.getElementById(`inventory-add-${encodeURIComponent(campaignId)}`)
                || document.getElementById(`inventory-toggle-${encodeURIComponent(campaignId)}`);
            target?.focus();
        }
    }
}

function renderInventory() {
    updateDropArt();
    const container = document.getElementById('inventory-grid');
    const focusedId = container.contains(document.activeElement) ? document.activeElement.id : null;
    container.replaceChildren();

    const t = state.translations;
    const allCampaigns = Object.values(state.campaigns);

    // Apply filters
    const filters = getInventoryFilters();
    const campaigns = allCampaigns.filter(campaign => campaignMatchesFilters(campaign, filters));

    if (allCampaigns.length === 0) {
        const emptyMsg = t.gui?.inventory?.no_campaigns || 'No campaigns loaded yet...';
        container.replaceChildren(makeElement('p', { class: 'empty-message' }, emptyMsg));
        return;
    }

    if (campaigns.length === 0) {
        container.replaceChildren(makeElement('p', { class: 'empty-message' }, 'No campaigns match the current filters.'));
        return;
    }

    const columnCount = inventoryColumnCount(container);
    container.dataset.columns = String(columnCount);
    const columns = Array.from({ length: columnCount }, () => makeElement('div', { class: 'inventory-column' }));
    container.replaceChildren(...columns);

    campaigns.forEach((campaign, index) => {
        const card = document.createElement('div');
        card.className = 'campaign-card';
        const expanded = expandedInventoryCampaigns.has(campaign.id);
        const contentId = `inventory-rewards-${encodeURIComponent(campaign.id)}`;

        let statusClass = '';
        let statusText = '';
        if (campaign.active) {
            statusClass = 'active';
            statusText = t.gui?.inventory?.status?.active || 'Active';
        } else if (campaign.upcoming) {
            statusClass = 'upcoming';
            statusText = t.gui?.inventory?.status?.upcoming || 'Upcoming';
        } else if (campaign.expired) {
            statusClass = 'expired';
            statusText = t.gui?.inventory?.status?.expired || 'Expired';
        }

        const claimedText = t.gui?.inventory?.status?.claimed || 'Claimed';
        const ignoredText = t.gui?.inventory?.status?.ignored || 'Ignored';
        const skippedText = t.gui?.inventory?.status?.skipped || 'Skipped';
        const claimedCountText = t.gui?.inventory?.claimed_drops || 'claimed';

        // Build drops elements
        const dropsEl = makeElement('div', { class: 'campaign-drops' });
        campaign.drops.forEach(drop => {
            const policyClass = drop.is_ignored ? ' ignored' : (drop.is_skipped ? ' skipped' : '');
            let policyStatus = '';
            let policyReason = '';
            if (drop.is_ignored) {
                policyStatus = `⊘ ${ignoredText}`;
                if (drop.ignored_reason === 'keyword') {
                    const template = t.gui?.inventory?.ignored_keyword_reason || 'Ignored by keyword: {keyword}';
                    policyReason = template.replace('{keyword}', drop.ignored_keyword || '');
                } else if (drop.ignored_reason === 'precondition') {
                    const template = t.gui?.inventory?.ignored_precondition_reason || 'Ignored because it depends on {drop}';
                    policyReason = template.replace('{drop}', drop.ignored_precondition || '');
                }
            } else if (drop.is_skipped) {
                policyStatus = `⊘ ${skippedText}`;
                policyReason = t.gui?.inventory?.skipped_branch_reason || 'Skipped because no mineable reward depends on this drop.';
            }

            const dropItem = makeElement('div', {
                class: `drop-item${drop.is_claimed ? ' claimed' : ''}${drop.can_claim ? ' active' : ''}${policyClass}`,
                ...(policyReason ? { title: policyReason } : {}),
            });
            if (drop.name !== campaign.name) {
                dropItem.appendChild(makeElement('div', { class: 'drop-item-header' }, null, header =>
                    header.appendChild(makeElement('strong', { class: 'drop-item-info' }, drop.name))));
            }
            const benefitsList = makeElement('div', { class: 'benefits-list' });
            if (drop.benefits && drop.benefits.length > 0) {
                drop.benefits.forEach(benefit => {
                    benefitsList.appendChild(
                        makeElement('div', { class: 'benefit-item' }, '', el => {
                            el.appendChild(makeImageElement(benefit.image_url, benefit.name, 'benefit-icon'));
                            el.appendChild(makeElement('div', { class: 'benefit-info' }, '', el2 => {
                                el2.appendChild(makeElement('span', { class: 'benefit-name' }, benefit.name));
                                const labels = t.gui?.inventory?.filters || {};
                                const benefitLabel = { DIRECT_ENTITLEMENT: labels.item, BADGE: labels.badge,
                                    EMOTE: labels.emote, UNKNOWN: labels.other }[benefit.type] || labels.other || 'Other';
                                el2.appendChild(makeElement('span', { class: 'benefit-type' }, benefitLabel));
                            }));
                        })
                    );
                });
            }
            dropItem.appendChild(benefitsList);
            const percent = Number.isFinite(drop.progress) ? Math.max(0, Math.min(100, Math.round(drop.progress * 100))) : 0;
            const progress = makeElement('div', { class: 'inventory-drop-progress', role: 'progressbar',
                'aria-label': drop.name, 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': percent });
            const fill = makeElement('span');
            fill.style.width = `${percent}%`;
            progress.appendChild(fill);
            dropItem.appendChild(progress);
            dropItem.appendChild(makeElement('div', {}, `${drop.current_minutes} / ${drop.required_minutes} minutes (${Math.round(drop.progress * 100)}%)`));
            if (drop.is_claimed) {
                dropItem.appendChild(makeElement('div', {}, `✓ ${claimedText}`));
            } else if (policyStatus) {
                dropItem.appendChild(makeElement('div', { class: 'drop-policy-status' }, policyStatus));
            }
            dropsEl.appendChild(dropItem);
        });

        // Campaign name link
        const campaignNameLink = makeElement('a', { id: `inventory-campaign-${encodeURIComponent(campaign.id)}`, href: campaign.campaign_url, target: '_blank', rel: 'noopener noreferrer', class: 'campaign-name-link', title: campaign.name }, '', el => {
            el.appendChild(makeElement('span', { class: 'campaign-name-text' }, campaign.name));
            el.appendChild(makeElement('span', { class: 'external-link-icon' }, '🔗'));
        });

        const linkStatusBadge = makeElement('span', {
            class: `campaign-badge ${campaign.linked ? 'linked' : 'not-linked'}`
        }, campaign.linked ? (t.gui?.inventory?.filters?.linked || 'Linked')
            : (t.gui?.inventory?.filters?.not_linked || 'Not Linked'));

        const campaignGameDiv = makeElement('span', { class: 'campaign-game' }, '', el => {
            if (campaign.game_box_art_url) {
                const iconUrl = campaign.game_box_art_url.replace('{width}', '52').replace('{height}', '70');
                el.appendChild(makeImageElement(iconUrl, campaign.game_name, 'game-icon'));
            }
            const titles = makeElement('span', { class: 'campaign-titles' });
            titles.appendChild(makeElement('strong', { class: 'campaign-game-name', title: campaign.game_name }, campaign.game_name));
            campaignNameLink.className += ' campaign-subtitle';
            titles.appendChild(campaignNameLink);
            el.appendChild(titles);
        });

        const overview = makeElement('div', { class: 'campaign-overview' });
        const toggle = makeElement('button', { type: 'button', class: 'campaign-toggle',
            id: `inventory-toggle-${encodeURIComponent(campaign.id)}`,
            'aria-expanded': expanded, 'aria-controls': contentId });
        toggle.setAttribute('aria-label', `${campaign.game_name} — ${campaign.name}`);
        overview.appendChild(campaignGameDiv);
        const dates = makeElement('span', { class: 'campaign-date-range' });
        for (const [field, key, fallback] of [['starts_at', 'starts', 'Starts: {time}'], ['ends_at', 'ends', 'Ends: {time}']]) {
            const date = new Date(campaign[field]);
            if (!campaign[field] || Number.isNaN(date.getTime())) continue;
            const label = t.gui?.inventory?.[key] || fallback;
            dates.appendChild(makeElement('time', { datetime: campaign[field], title: label.replace('{time}', date.toLocaleString()) },
                label.replace('{time}', date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }))));
        }
        overview.appendChild(dates);
        toggle.appendChild(makeElement('span', { class: 'campaign-chevron', 'aria-hidden': 'true' }));
        const controls = makeElement('div', { class: 'campaign-controls' });
        controls.appendChild(linkStatusBadge);
        controls.appendChild(toggle);
        overview.appendChild(controls);
        const actions = makeElement('div', { class: 'campaign-actions' });
        if (canAddInventoryGame(campaign) || (campaign.linked === true && campaign.game_name === inventoryWatchPendingGame)) {
            const add = makeElement('button', { type: 'button', class: 'inventory-add-game' },
                campaign.game_name === inventoryWatchPendingGame ? (t.gui?.inventory?.adding_game || 'Adding…')
                    : (t.gui?.inventory?.add_game || 'Add to mining list'));
            add.disabled = inventoryWatchSavePending;
            add.id = `inventory-add-${encodeURIComponent(campaign.id)}`;
            add.addEventListener('click', () => addInventoryGame(campaign.id, add));
            actions.appendChild(add);
        }
        if (!campaign.linked && campaign.link_url) {
            const link = makeElement('a', { id: `inventory-link-${encodeURIComponent(campaign.id)}`, href: campaign.link_url, target: '_blank', rel: 'noopener noreferrer', class: 'link-account-btn' },
                t.gui?.inventory?.link_account || 'Link account');
            actions.appendChild(link);
        }
        overview.appendChild(actions);

        const campaignStatus = makeElement('div', { class: 'campaign-status' }, '', el => {
            el.appendChild(makeElement('span', {}, statusText));
            let countText = `${campaign.claimed_drops} / ${campaign.total_drops} ${claimedCountText}`;
            if (campaign.ignored_drops > 0) {
                const ignoredCount = t.gui?.inventory?.ignored_drops || '{count} ignored';
                countText += ` · ${ignoredCount.replace('{count}', campaign.ignored_drops)}`;
            }
            if (campaign.skipped_drops > 0) {
                const skippedCount = t.gui?.inventory?.skipped_drops || '{count} skipped';
                countText += ` · ${skippedCount.replace('{count}', campaign.skipped_drops)}`;
            }
            el.appendChild(makeElement('span', {}, countText));
        });

        const campaignInfo = makeElement('div', { class: 'campaign-info' });
        campaignInfo.appendChild(campaignStatus);

        const content = makeElement('div', { class: 'campaign-content', id: contentId });
        content.hidden = !expanded;
        content.appendChild(campaignInfo);
        content.appendChild(dropsEl);
        toggle.addEventListener('click', () => {
            const open = toggle.getAttribute('aria-expanded') !== 'true';
            if (open) expandedInventoryCampaigns.add(campaign.id);
            else expandedInventoryCampaigns.delete(campaign.id);
            toggle.setAttribute('aria-expanded', String(open));
            content.hidden = !open;
        });
        // The whole overview opens the card; its links and buttons keep their own actions.
        overview.addEventListener('click', event => {
            if (!event.target.closest('a, button, .campaign-badge') && !getSelection()?.toString()) toggle.click();
        });
        card.replaceChildren(overview, content);

        columns[index % columnCount].appendChild(card);
    });
    if (focusedId) {
        const prefix = ['inventory-add-', 'inventory-link-', 'inventory-campaign-']
            .find(value => focusedId.startsWith(value));
        const target = document.getElementById(focusedId)
            || (prefix ? document.getElementById('inventory-toggle-' + focusedId.slice(prefix.length)) : null);
        target?.focus({ preventScroll: true });
    }
}

function updateLoginStatus(data) {
    state.login = data;
    const statusEl = document.getElementById('login-status');
    const t = state.translations;
    if (data.user_id) {
        const userIdLabel = t.gui?.login?.user_id_label || 'User ID:';
        const loggedIn = t.login?.status?.logged_in || 'Logged in';
        statusEl.textContent = `${loggedIn} (${userIdLabel} ${data.user_id})`;
        statusEl.style.color = 'var(--success-color)';
    } else {
        statusEl.textContent = t.login?.status?.required || 'Login required';
        statusEl.style.color = 'var(--text-secondary)';
    }
    statusEl.title = statusEl.textContent;
    const avatarEl = document.getElementById('user-avatar');
    if (avatarEl) {
        const avatarUrl = data.avatar_url;
        if (typeof avatarUrl === 'string' && avatarUrl.startsWith('https://')) {
            avatarEl.textContent = '';
            avatarEl.style.backgroundImage = `url("${avatarUrl}")`;
        } else {
            const displayName = data.username || data.user_name || data.name || '';
            avatarEl.textContent = displayName ? String(displayName)[0].toUpperCase() : '';
            avatarEl.style.backgroundImage = '';
        }
        avatarEl.classList.toggle('logged-in', Boolean(data.user_id));
        avatarEl.classList.toggle('avatar-image', Boolean(data.user_id) && typeof avatarUrl === 'string' && avatarUrl.startsWith('https://'));
    }
    globalThis.browserLoginPanel?.updateLogin(data);
}

function updateSettingsUI(settings, authoritative = false, changedFields = []) {
    // Initial loads/socket updates own the watch list; unrelated partial-save replies do not.
    if (!authoritative && Array.isArray(state.settings.games_to_watch)) {
        settings = { ...settings, games_to_watch: state.settings.games_to_watch };
    } else {
        settings = reconcileGameWatchSettings(settings);
    }
    // Unrelated saves may return snapshots older than a completed priority write.
    if (!authoritative && !changedFields.includes('reward_priority') && Array.isArray(state.settings.reward_priority)) {
        settings = {...settings, reward_priority: state.settings.reward_priority};
    }
    // Reward-type checkboxes update state optimistically; only authoritative updates replace them.
    if (!authoritative && !changedFields.includes('mining_benefits') && state.settings.mining_benefits) {
        settings = {...settings, mining_benefits: state.settings.mining_benefits};
    }
    state.settings = settings;
    const allowUnlinked = document.getElementById('allow-unlinked-campaigns');
    if (allowUnlinked) allowUnlinked.checked = settings.allow_unlinked_campaigns === true;
    document.getElementById('dark-mode').checked = settings.dark_mode || false;
    document.getElementById('priority-list-only').checked = settings.priority_list_only !== false;
    applyInventoryViewMode(settings.inventory_list_view || false);
    document.getElementById('connection-quality').value = settings.connection_quality || 1;
    document.getElementById('minimum-refresh-interval').value = settings.minimum_refresh_interval_minutes || 30;

    const miningHours = settings.mining_hours || { mode: 'always', start: '08:00', end: '22:00' };
    const rangeMode = miningHours.mode === 'range';
    document.getElementById('mining-hours-always').checked = !rangeMode;
    document.getElementById('mining-hours-range').checked = rangeMode;
    document.getElementById('mining-hours-start').value = miningHours.start || '08:00';
    document.getElementById('mining-hours-end').value = miningHours.end || '22:00';
    toggleMiningHoursRange(rangeMode);

    const dropBlacklist = document.getElementById('drop-name-blacklist');
    if (dropBlacklist) {
        dropBlacklist.value = Array.isArray(settings.drop_name_blacklist)
            ? settings.drop_name_blacklist.join('\n')
            : '';
    }

    // Update proxy settings and indicator
    const proxyUrl = settings.proxy || '';
    const proxyInput = document.getElementById('proxy-url');
    if (proxyInput) proxyInput.value = proxyUrl;

    const proxyIndicator = document.getElementById('proxy-indicator');
    if (proxyIndicator) {
        proxyIndicator.style.display = proxyUrl ? 'inline-flex' : 'none';
        proxyIndicator.title = proxyUrl ? `Proxy active: ${proxyUrl}` : 'Proxy disabled';
    }

    const discordWebhookInput = document.getElementById('discord-webhook-url');
    if (discordWebhookInput) {
        discordWebhookInput.value = '';
        discordWebhookInput.placeholder = settings.discord_configured
            ? '••••••••' : 'https://discord.com/api/webhooks/ID/TOKEN';
    }

    // Populate Telegram fields if present in settings (the server never
    // echoes the stored bot token; it returns a mask placeholder instead).
    const botTokenInput = document.getElementById('telegram-bot-token');
    const chatIdInput = document.getElementById('telegram-chat-id');
    if (botTokenInput) {
        botTokenInput.value = '';
        if (settings.telegram_configured) {
            botTokenInput.placeholder = '••••••••';
        }
    }
    if (chatIdInput) chatIdInput.value = settings.telegram_chat_id || '';

    // Update language dropdown if we have the current language
    if (settings.language) {
        const languageSelect = document.getElementById('language');
        if (languageSelect) {
            languageSelect.value = settings.language;
        }
    }

    if (settings.dark_mode) {
        document.body.classList.add('dark-mode');
    } else {
        document.body.classList.remove('dark-mode');
    }

    // Update available games if provided in settings
    if (settings.games_available) {
        availableGames = new Set(settings.games_available);
    }

    // Restore inventory filters from settings
    if (settings.inventory_filters) {
        document.getElementById('filter-active').checked = settings.inventory_filters.show_active || false;
        document.getElementById('filter-link-status').value = settings.inventory_filters.link_status || 'all';
        document.getElementById('filter-upcoming').checked = settings.inventory_filters.show_upcoming || false;
        document.getElementById('filter-expired').checked = settings.inventory_filters.show_expired || false;
        document.getElementById('filter-finished').checked = settings.inventory_filters.show_finished || false;

        // Restore selected games array
        selectedInventoryGames = Array.isArray(settings.inventory_filters.game_name_search)
            ? [...settings.inventory_filters.game_name_search]
            : [];  // Handle old string format gracefully
        updateGameTagsDisplay();

        // Restore benefit type filters (default to true if not set)
        if (document.getElementById('filter-benefit-item')) document.getElementById('filter-benefit-item').checked = settings.inventory_filters.show_benefit_item !== false;
        if (document.getElementById('filter-benefit-badge')) document.getElementById('filter-benefit-badge').checked = settings.inventory_filters.show_benefit_badge !== false;
        if (document.getElementById('filter-benefit-emote')) document.getElementById('filter-benefit-emote').checked = settings.inventory_filters.show_benefit_emote !== false;
        if (document.getElementById('filter-benefit-other')) document.getElementById('filter-benefit-other').checked = settings.inventory_filters.show_benefit_other !== false;
    }

    // Reward types: mined checkboxes and optional preferred order
    if (typeof renderRewardPriority === 'function') renderRewardPriority(settings.reward_priority || []);


    // Update games to watch lists
    renderGamesToWatch();

    // Re-render channels list to apply filter based on updated games to watch
    renderChannels();

    // Re-render inventory to apply filters
    renderInventory();
}

function updateManualModeUI(manualModeInfo) {
    const manualBadge = document.getElementById('manual-mode-badge');
    const autoBadge = document.getElementById('auto-mode-badge');
    const manualGameName = document.getElementById('manual-game-name');
    const manualControls = document.getElementById('manual-mode-controls');
    const manualModeGame = document.getElementById('manual-mode-game');

    if (manualModeInfo.active) {
        // Show manual mode badge, hide auto badge
        manualBadge.classList.remove('hidden');
        autoBadge.classList.add('hidden');
        manualGameName.textContent = manualModeInfo.game_name || '';

        // Show manual mode controls in drop progress section
        if (manualControls) {
            manualControls.classList.remove('hidden');
            if (manualModeGame) {
                manualModeGame.textContent = manualModeInfo.game_name || '';
            }
        }
    } else {
        // Hide manual mode badge, show auto badge
        manualBadge.classList.add('hidden');
        autoBadge.classList.remove('hidden');

        // Hide manual mode controls
        if (manualControls) {
            manualControls.classList.add('hidden');
        }
    }
}

// ==================== mining list Management ====================

let availableGames = new Set(); // All games from campaigns
let draggedElement = null;

socket.on('games_available', (data) => {
    availableGames = new Set(data.games || []);
    renderGamesToWatch();
});

// Account linking is per game, so a game counts as linked if any of its campaigns is.
// Games without a known campaign are absent from the map (status unknown).
function getGameLinkStatus() {
    const status = new Map();
    Object.values(state.campaigns).forEach(campaign => {
        if (!campaign.game_name) return;
        const key = campaign.game_name.toLowerCase();
        status.set(key, status.get(key) === true || campaign.linked === true);
    });
    return status;
}

function linkStatusSignature(linkStatus) {
    const shown = new Set([...availableGames, ...(state.settings.games_to_watch || [])]
        .map(game => game.toLowerCase()));
    return Array.from(linkStatus.entries()).filter(([game]) => shown.has(game)).sort().join('|');
}

// Inventory updates arrive often while mining; rebuilding the lists would interrupt
// a drag or a priority edit, so only re-render when a game's link status changed.
let renderedLinkSignature = null;
function refreshGamesIfLinkStatusChanged() {
    if (linkStatusSignature(getGameLinkStatus()) !== renderedLinkSignature) {
        renderGamesToWatch();
    }
}

function makeGameLinkBadge(linkStatus, game) {
    const linked = linkStatus.get(game.toLowerCase());
    if (linked === undefined) return null;
    const t = state.translations.gui?.settings;
    return linked
        ? makeElement('span', { class: 'game-link-badge linked', title: t?.game_linked_title || 'Account is linked' }, t?.game_linked || 'LINKED')
        : makeElement('span', { class: 'game-link-badge not-linked', title: t?.game_not_linked_title || 'Account is not linked' }, t?.game_not_linked || 'NOT LINKED');
}

function selectLinkedGames() {
    const linkStatus = getGameLinkStatus();
    const existing = state.settings.games_to_watch || [];
    const selected = new Set(existing.map(game => game.toLowerCase()));
    const newGames = Array.from(availableGames).sort((a, b) => a.localeCompare(b)).filter(game => {
        const key = game.toLowerCase();
        if (selected.has(key) || linkStatus.get(key) !== true) return false;
        selected.add(key);
        return true;
    });
    if (newGames.length === 0) return;

    state.settings.games_to_watch = [...existing, ...newGames];
    renderGamesToWatch();
    renderChannels();
    saveSettings();
}

function renderGamesToWatch() {
    const focused = document.activeElement?.id;
    const selectedGames = state.settings.games_to_watch || [];
    const filterText = document.getElementById('games-filter')?.value.toLowerCase() || '';
    const linkFilter = document.getElementById('games-link-filter')?.value || 'all';
    const linkStatus = getGameLinkStatus();
    renderedLinkSignature = linkStatusSignature(linkStatus);

    // Render selected games (sortable)
    renderSelectedGames(selectedGames, linkStatus);

    // Render available games (checkboxes for unselected games)
    const selectedKeys = new Set(selectedGames.map(game => game.toLowerCase()));
    const unselectedGames = Array.from(availableGames)
        .filter(game => !selectedKeys.has(game.toLowerCase()))
        .filter(game => game.toLowerCase().includes(filterText))
        .filter(game => {
            const linked = linkStatus.get(game.toLowerCase());
            if (linkFilter === 'linked') return linked === true;
            if (linkFilter === 'not_linked') return linked === false;
            return true;
        })
        // Linked games first, then the rest; alphabetical within each group
        .sort((a, b) => {
            const aLinked = linkStatus.get(a.toLowerCase()) === true;
            const bLinked = linkStatus.get(b.toLowerCase()) === true;
            return (bLinked - aLinked) || a.localeCompare(b);
        });

    renderAvailableGames(unselectedGames, filterText, linkStatus, linkFilter);
    const control = focused && document.activeElement?.id !== focused ? document.getElementById(focused) : null;
    if (control && !control.disabled) control.focus();
}

function renderSelectedGames(games, linkStatus = getGameLinkStatus()) {
    const container = document.getElementById('selected-games-list');
    if (!container) return;

    const t = state.translations;
    container.innerHTML = '';

    if (games.length === 0) {
        const emptyMsg = t.gui?.settings?.no_games_selected || 'No games selected. Check games below to add them.';
        container.replaceChildren(makeElement('p', { class: 'empty-message' }, emptyMsg));
        return;
    }

    games.forEach((game, index) => {
        const div = document.createElement('div');
        div.className = 'sortable-item';
        div.draggable = true;
        div.dataset.game = game;
        const priorityInput = makeElement('input', {
            type: 'number',
            id: `game-priority-${encodeURIComponent(game)}`,
            class: 'priority-input',
            value: String(index + 1),
            min: '1',
            max: String(games.length),
            'aria-label': (t.gui?.settings?.game_priority || 'Priority for {game}').replace('{game}', game)
        });

        div.replaceChildren(
            makeElement('span', { class: 'drag-handle' }, '☰'),
            priorityInput,
            makeElement('span', { class: 'game-name' }, game),
            ...[makeGameLinkBadge(linkStatus, game)].filter(Boolean),
            makeElement('button', {
                class: 'remove-btn',
                id: `game-remove-${encodeURIComponent(game)}`,
                title: (t.gui?.settings?.remove_game || 'Remove {game}').replace('{game}', game),
                'aria-label': (t.gui?.settings?.remove_game || 'Remove {game}').replace('{game}', game)
            }, '✕')
        );

        // Event listener for priority change
        priorityInput.addEventListener('change', (e) => {
            const priority = Number(e.target.value);
            if (e.target.value.trim() && Number.isInteger(priority)) {
                changeGamePriority(game, priority - 1);
            } else {
                e.target.value = String(index + 1); // Reset on invalid
            }
        });

        // Event listener for the delete button
        const removeBtn = div.querySelector('.remove-btn');
        removeBtn.addEventListener('click', () => removeGameFromWatch(game));

        // Drag event handlers
        div.addEventListener('dragstart', handleDragStart);
        div.addEventListener('dragover', handleDragOver);
        div.addEventListener('drop', handleDrop);
        div.addEventListener('dragend', handleDragEnd);

        container.appendChild(div);
    });
}

function renderAvailableGames(games, filterText, linkStatus = getGameLinkStatus(), linkFilter = 'all') {
    const container = document.getElementById('available-games-list');
    if (!container) return;

    const t = state.translations;
    container.innerHTML = '';

    if (games.length === 0) {
        if (filterText || linkFilter !== 'all') {
            const emptyMsg = t.gui?.settings?.no_games_match || 'No games match your search.';
            const addHint = filterText
                ? (t.gui?.settings?.add_game_hint || ' Click "Add Game" to add it manually.')
                : '';
            container.replaceChildren(makeElement('p', { class: 'empty-message' }, `${emptyMsg}${addHint}`));
        } else {
            const emptyMsg = t.gui?.settings?.all_games_selected || 'All games are selected or no games available.';
            container.replaceChildren(makeElement('p', { class: 'empty-message' }, emptyMsg));
        }
        return;
    }

    games.forEach(game => {
        const label = document.createElement('label');
        label.className = 'game-checkbox';
        label.replaceChildren(
            makeElement('input', { type: 'checkbox', value: game, id: `game-available-${encodeURIComponent(game)}` }),
            makeElement('span', {}, game),
            ...[makeGameLinkBadge(linkStatus, game)].filter(Boolean),
        );

        const checkbox = label.querySelector('input[type="checkbox"]');
        checkbox.addEventListener('change', (e) => toggleGameWatch(game, e.target.checked));

        container.appendChild(label);
    });
}

// Drag and drop handlers
function handleDragStart(e) {
    draggedElement = e.target;
    e.target.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/html', e.target.innerHTML);
}

function handleDragOver(e) {
    if (e.preventDefault) {
        e.preventDefault();
    }
    e.dataTransfer.dropEffect = 'move';

    const target = e.target.closest('.sortable-item');
    // Rows only move within their own list (Mining list or reward types).
    if (target && target !== draggedElement && target.parentNode === draggedElement?.parentNode) {
        const container = target.parentNode;
        const allItems = [...container.querySelectorAll('.sortable-item')];
        const draggedIndex = allItems.indexOf(draggedElement);
        const targetIndex = allItems.indexOf(target);

        if (draggedIndex < targetIndex) {
            target.parentNode.insertBefore(draggedElement, target.nextSibling);
        } else {
            target.parentNode.insertBefore(draggedElement, target);
        }
    }
    return false;
}

function handleDrop(e) {
    if (e.stopPropagation) {
        e.stopPropagation();
    }
    return false;
}

function handleDragEnd(e) {
    e.target.classList.remove('dragging');

    // Update the order in state
    const container = document.getElementById('selected-games-list');
    const items = container.querySelectorAll('.sortable-item');
    const newOrder = Array.from(items).map(item => item.dataset.game);

    state.settings.games_to_watch = newOrder;

    // Re-render to update priority numbers
    renderSelectedGames(newOrder);

    // Re-render channels list to apply updated filter
    renderChannels();

    // Save settings
    saveSettings();
}

function toggleGameWatch(gameName, checked) {
    const games = state.settings.games_to_watch || [];

    if (checked && !games.includes(gameName)) {
        games.push(gameName);
    } else if (!checked) {
        const index = games.indexOf(gameName);
        if (index > -1) {
            games.splice(index, 1);
        }
    }

    state.settings.games_to_watch = games;
    renderGamesToWatch();
    renderChannels();
    saveSettings();
}

function changeGamePriority(gameName, newIndex) {
    if (!Number.isInteger(newIndex)) return;
    const games = [...(state.settings.games_to_watch || [])];
    const currentIndex = games.indexOf(gameName);

    if (currentIndex > -1) {
        games.splice(currentIndex, 1);

        // Ensure newIndex is within bounds
        newIndex = Math.max(0, Math.min(newIndex, games.length));
        games.splice(newIndex, 0, gameName);

        state.settings.games_to_watch = games;
        renderGamesToWatch();
        renderChannels();
        saveSettings();
    }
}

function removeGameFromWatch(gameName) {
    const games = state.settings.games_to_watch || [];
    const index = games.indexOf(gameName);
    if (index > -1) {
        games.splice(index, 1);
        state.settings.games_to_watch = games;
        renderGamesToWatch();
        renderChannels();
        saveSettings();
    }
}

function selectAllGames() {
    const existing = state.settings.games_to_watch || [];
    const selected = new Set(existing.map(game => game.toLowerCase()));
    const newGames = Array.from(availableGames).sort().filter(game => {
        const key = game.toLowerCase();
        if (selected.has(key)) return false;
        selected.add(key);
        return true;
    });
    
    state.settings.games_to_watch = [...existing, ...newGames];
    renderGamesToWatch();
    renderChannels();
    saveSettings();
}

function deselectAllGames() {
    if (!state.settings.games_to_watch || state.settings.games_to_watch.length === 0) {
        return;
    }
    
    const t = state.translations;
    const msg = t.gui?.settings?.deselect_all_warning || 'Are you sure you want to remove all games from your watch list?';
    
    showConfirmModal(msg, () => {
        state.settings.games_to_watch = [];
        renderGamesToWatch();
        renderChannels();
        saveSettings();
    });
}

function addGameFromSearch() {
    const searchInput = document.getElementById('games-filter');
    const searchLower = searchInput.value.trim().toLowerCase();

    if (!searchLower) {
        return;
    }

    let gameToAdd = searchInput.value.trim();
    let isManualAdd = true;
    
    // Find matching games from availableGames
    const matches = Array.from(availableGames).filter(g => g.toLowerCase().includes(searchLower));
    
    // 1. Check for exact case-insensitive match
    const exactMatch = matches.find(g => g.toLowerCase() === searchLower);
    
    if (exactMatch) {
        gameToAdd = exactMatch;
        isManualAdd = false;
    } else if (matches.length === 1) {
        // 2. Check for a single partial match
        gameToAdd = matches[0];
        isManualAdd = false;
    } else if (matches.length > 1) {
        // Multiple matches found and no exact match. Don't add to avoid ambiguity.
        const t = state.translations;
        const msg = t.gui?.settings?.multiple_games_found || 'Multiple games found for your search. Please be more specific.';
        showToast(msg, 'warning');
        return;
    }

    const games = state.settings.games_to_watch || [];
    
    // Check if already selected (case-insensitive)
    if (games.some(g => g.toLowerCase() === gameToAdd.toLowerCase())) {
        searchInput.value = ''; // Clear input if already added
        renderGamesToWatch(); // Just re-render to clear any filtering state if needed
        return;
    }

    const finishAdding = (gameName) => {
        // Confirmation can outlive a settings update; append to the current list.
        const current = state.settings.games_to_watch || [];
        if (current.some(game => game.toLowerCase() === gameName.toLowerCase())) return;
        state.settings.games_to_watch = [...current, gameName];

        // Add to available games set so it shows up in lists
        availableGames.add(gameName);

        // Clear search and update UI
        searchInput.value = '';
        renderGamesToWatch();
        renderChannels();
        saveSettings();
    };

    // Warn if adding manually
    if (isManualAdd) {
        const t = state.translations;
        let msg = t.gui?.settings?.manual_game_warning || '\"{game}\" is not in the available campaign list. Add it to mining list anyway?';
        msg = msg.replace('{game}', gameToAdd);
        showConfirmModal(msg, () => finishAdding(gameToAdd));
    } else {
        finishAdding(gameToAdd);
    }
}

function flashTitle() {
    const originalTitle = document.title;
    let count = 0;
    const interval = setInterval(() => {
        document.title = count % 2 === 0 ? '🔔 Attention!' : originalTitle;
        count++;
        if (count >= 10) {
            document.title = originalTitle;
            clearInterval(interval);
        }
    }, 1000);
}

// ==================== API Functions ====================

async function selectChannel(channelId) {
    try {
        const response = await fetch('/api/channels/select', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel_id: channelId })
        });

        if (!response.ok) {
            const errorData = await response.json();
            console.error('Failed to select channel:', errorData.detail || 'Unknown error');
            addConsoleLine(`Error selecting channel: ${errorData.detail || 'Unknown error'}`);
        }
    } catch (error) {
        console.error('Failed to select channel:', error);
        addConsoleLine(`Error selecting channel: ${error.message}`);
    }
}

async function exitManualMode() {
    try {
        const response = await fetch('/api/mode/exit-manual', {
            method: 'POST'
        });

        const result = await response.json();
        if (!result.success) {
            console.log('Exit manual mode:', result.message || 'Already in automatic mode');
        }
    } catch (error) {
        console.error('Failed to exit manual mode:', error);
        addConsoleLine(`Error exiting manual mode: ${error.message}`);
    }
}

async function verifyProxy() {
    const proxyInput = document.getElementById('proxy-url');
    const proxyUrl = proxyInput ? proxyInput.value.trim() : '';
    const resultDiv = document.getElementById('proxy-verify-result');

    if (!resultDiv) return;

    // Reset display
    resultDiv.style.display = 'block';
    resultDiv.className = 'verify-result loading';
    resultDiv.textContent = 'Verifying connection...';

    if (!proxyUrl) {
        resultDiv.className = 'verify-result error';
        resultDiv.textContent = 'Please enter a proxy URL first.';
        return;
    }

    try {
        const response = await fetch('/api/settings/verify-proxy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proxy: proxyUrl })
        });

        const data = await response.json();

        if (data.success) {
            resultDiv.className = 'verify-result success';
            resultDiv.textContent = `✓ ${data.message}`;
        } else {
            resultDiv.className = 'verify-result error';
            resultDiv.textContent = `✗ ${data.message}`;
        }
    } catch (error) {
        resultDiv.className = 'verify-result error';
        resultDiv.textContent = `Error: ${error.message}`;
    }
}

async function saveDiscordSettings(webhookUrl, removeWebhook = false) {
    const dc = state.translations.gui?.settings?.discord || {};
    try {
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ discord_webhook_url: webhookUrl, discord_remove_webhook: removeWebhook })
        });
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (data.success !== true || !data.settings) throw new Error();
        updateSettingsUI(data.settings);
    } catch (error) {
        throw new Error(dc.save_error || 'Failed to save Discord settings.');
    }
}

async function updateDiscordSettings(action) {
    if (state.discordActionPending) return;
    const input = document.getElementById('discord-webhook-url');
    const result = document.getElementById('discord-test-result');
    if (!result) return;
    const dc = state.translations.gui?.settings?.discord || {};
    const webhookUrl = input ? input.value.trim() : '';
    result.style.display = 'block';
    if (action !== 'remove' && !webhookUrl && !state.settings.discord_configured) {
        result.className = 'verify-result error';
        result.textContent = dc.missing_credentials || 'Please enter a Discord Webhook URL.';
        return;
    }
    state.discordActionPending = true;
    const controls = ['save-discord-btn', 'test-discord-btn', 'remove-discord-btn', 'discord-webhook-url']
        .map(id => document.getElementById(id)).filter(Boolean);
    controls.forEach(control => { control.disabled = true; });
    result.className = 'verify-result loading';
    result.textContent = `${action === 'test' ? (dc.test_connection || 'Test Connection') : (dc.save_settings || 'Save Settings')}…`;
    let saving = false;
    try {
        if (action === 'test') {
            const response = await fetch('/api/settings/test-discord', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ discord_webhook_url: webhookUrl })
            });
            if (!response.ok || (await response.json()).success !== true) throw new Error();
        }
        saving = true;
        await saveDiscordSettings(action === 'remove' ? '' : webhookUrl, action === 'remove');
        result.className = 'verify-result success';
        result.textContent = action === 'test' ? (dc.success || 'Discord connection successful!')
            : action === 'remove' ? (dc.removed || 'Webhook removed. Discord notifications are disabled.')
            : (dc.saved || 'Settings saved successfully!');
    } catch (error) {
        result.className = 'verify-result error';
        result.textContent = saving ? (dc.save_error || 'Failed to save Discord settings.')
            : (dc.error || 'Failed to connect to Discord. Please check your Webhook URL.');
    } finally {
        state.discordActionPending = false;
        controls.forEach(control => { control.disabled = false; });
    }
}

async function testTelegramConnection() {
    const botTokenInput = document.getElementById('telegram-bot-token');
    const chatIdInput = document.getElementById('telegram-chat-id');
    const resultDiv = document.getElementById('telegram-test-result');

    if (!resultDiv) return;

    const botToken = botTokenInput ? botTokenInput.value.trim() : '';
    const chatId = chatIdInput ? chatIdInput.value.trim() : '';
    const tg = state.translations.gui?.settings?.telegram || {};

    // Reset display
    resultDiv.style.display = 'block';
    resultDiv.className = 'verify-result loading';
    resultDiv.textContent = `${tg.test_connection || 'Test Connection'}…`;

    if ((!botToken && !state.settings.telegram_configured) || !chatId) {
        resultDiv.className = 'verify-result error';
        resultDiv.textContent = tg.missing_credentials || 'Please enter a bot token and chat ID.';
        return;
    }

    try {
        const response = await fetch('/api/settings/test-telegram', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ telegram_bot_token: botToken, telegram_chat_id: chatId })
        });

        if (!response.ok) {
            throw new Error(`${tg.error || 'Telegram connection failed.'} (HTTP ${response.status})`);
        }
        const data = await response.json();

        if (data.success) {
            // A successful test does not guarantee that settings were saved.
            await saveTelegramSettings(botToken, chatId);
            resultDiv.className = 'verify-result success';
            resultDiv.textContent = tg.success || '✓ Telegram connection successful!';
        } else {
            resultDiv.className = 'verify-result error';
            resultDiv.textContent = tg.error || 'Telegram connection failed.';
        }
    } catch (error) {
        resultDiv.className = 'verify-result error';
        resultDiv.textContent = error.message || tg.error || 'Telegram connection failed.';
    }
}

async function saveTelegramSettings(botToken, chatId) {
    const settings = {
        telegram_bot_token: botToken,
        telegram_chat_id: chatId
    };

    const tg = state.translations.gui?.settings?.telegram || {};
    const failureMessage = tg.save_error || 'Failed to save Telegram settings.';
    let response;
    let data;
    try {
        response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        data = await response.json();
        if (!data.success || !data.settings) {
            throw new Error(failureMessage);
        }
    } catch (error) {
        const status = response && !response.ok ? ` (HTTP ${response.status})` : '';
        throw new Error(`${failureMessage}${status}`);
    }
    updateSettingsUI(data.settings);
}

async function handleSaveTelegramClick() {
    const botTokenInput = document.getElementById('telegram-bot-token');
    const chatIdInput = document.getElementById('telegram-chat-id');
    const resultDiv = document.getElementById('telegram-test-result');

    if (!resultDiv) return;

    const botToken = botTokenInput ? botTokenInput.value.trim() : '';
    const chatId = chatIdInput ? chatIdInput.value.trim() : '';
    const tg = state.translations.gui?.settings?.telegram || {};

    // Reset display
    resultDiv.style.display = 'block';
    resultDiv.className = 'verify-result loading';
    resultDiv.textContent = `${tg.save_settings || 'Save Settings'}…`;

    // A blank token keeps the stored credential. Clearing the chat ID disables alerts.
    if (!state.settings.telegram_configured && (!botToken || !chatId)) {
        resultDiv.className = 'verify-result error';
        resultDiv.textContent = tg.missing_credentials || 'Please enter a bot token and chat ID.';
        return;
    }

    try {
        await saveTelegramSettings(botToken, chatId);
        resultDiv.className = 'verify-result success';
        resultDiv.textContent = tg.saved || '✓ Settings saved successfully!';
    } catch (error) {
        resultDiv.className = 'verify-result error';
        resultDiv.textContent = error.message || tg.save_error || 'Failed to save Telegram settings.';
    }
}

function parseDropNameBlacklist(value) {
    return String(value || '')
        .split(/\r?\n/)
        .map(keyword => keyword.trim())
        .filter(Boolean);
}

const rewardTypes = ['BADGE', 'EMOTE', 'DIRECT_ENTITLEMENT', 'UNKNOWN'];
const rewardLabelKeys = {BADGE: 'badge', EMOTE: 'emote', DIRECT_ENTITLEMENT: 'item', UNKNOWN: 'other'};
let rewardPrioritySaving = false;
function displayedRewardOrder() {
    const order = state.settings.reward_priority || [];
    return order.length ? [...order] : [...rewardTypes];
}
function renderRewardPriority(order = state.settings.reward_priority || []) {
    const t = state.translations.gui?.settings || {};
    const labels = state.translations.gui?.inventory?.filters || {};
    const enabled = document.getElementById('reward-priority-enabled');
    if (!enabled) return;
    enabled.checked = order.length > 0;
    enabled.disabled = rewardPrioritySaving;
    for (const [id, key] of Object.entries({'reward-priority-title': 'reward_priority_title',
        'reward-priority-help': 'reward_priority_help', 'reward-priority-enable-label': 'reward_priority_enable'}))
        document.getElementById(id).textContent = t[key] || '';
    const benefits = state.settings.mining_benefits || {};
    const list = document.getElementById('reward-priority-order');
    const focused = document.activeElement?.closest('.reward-type-row')?.parentNode === list
        ? document.activeElement.id : null;
    list.classList.toggle('ordered', enabled.checked);
    list.replaceChildren();
    for (const [index, type] of (order.length ? order : rewardTypes).entries()) {
        const name = labels[rewardLabelKeys[type]] || type;
        const row = makeElement('div', { class: 'sortable-item reward-type-row' });
        row.dataset.rewardType = type;
        row.draggable = enabled.checked && !rewardPrioritySaving;
        const rank = makeElement('input', { type: 'number', class: 'priority-input', id: `reward-rank-${type}`,
            value: String(index + 1), min: '1', max: String(rewardTypes.length),
            'aria-label': (t.reward_priority_rank || 'Position of {type}').replace('{type}', name) });
        rank.disabled = !enabled.checked || rewardPrioritySaving;
        rank.addEventListener('change', () => {
            const position = Number(rank.value);
            if (!rank.value.trim() || !Number.isInteger(position)) {
                rank.value = String(index + 1); // Reset on invalid, like the Mining list
                return;
            }
            const next = displayedRewardOrder().filter(item => item !== type);
            next.splice(Math.max(0, Math.min(position - 1, next.length)), 0, type);
            setTimeout(() => saveRewardPriority(next), 0);
        });
        const checkbox = makeElement('input', { type: 'checkbox', id: `reward-mined-${type}` });
        checkbox.checked = benefits[type] === true;
        checkbox.disabled = !state.settings.mining_benefits; // A save before load would store a partial set
        const status = makeElement('span', { class: 'reward-type-excluded' },
            checkbox.checked || checkbox.disabled ? '' : (t.reward_priority_not_mined || 'Not mined'));
        checkbox.addEventListener('change', () => {
            // Keep re-renders consistent while the generic settings save is queued.
            state.settings.mining_benefits = { ...state.settings.mining_benefits, [type]: checkbox.checked };
            status.textContent = checkbox.checked ? '' : (t.reward_priority_not_mined || 'Not mined');
            row.classList.toggle('excluded', !checkbox.checked);
            saveSettings();
        });
        const label = makeElement('label', { class: 'reward-type-label' });
        label.appendChild(checkbox);
        label.appendChild(makeElement('span', {}, name));
        row.replaceChildren(makeElement('span', { class: 'drag-handle', 'aria-hidden': 'true' }, '☰'), rank, label, status);
        row.classList.toggle('excluded', !checkbox.checked);
        row.addEventListener('dragstart', handleDragStart);
        row.addEventListener('dragover', handleDragOver);
        row.addEventListener('drop', handleDrop);
        row.addEventListener('dragend', event => {
            event.target.classList.remove('dragging');
            if (!list.classList.contains('ordered')) return;
            saveRewardPriority([...list.children].map(item => item.dataset.rewardType));
        });
        list.appendChild(row);
    }
    const control = focused ? document.getElementById(focused) : null;
    if (control && !control.disabled) control.focus();
}
async function saveRewardPriority(order) {
    if (rewardPrioritySaving) return;
    if (JSON.stringify(order) === JSON.stringify(state.settings.reward_priority || [])) {
        renderRewardPriority();
        return;
    }
    const error = document.getElementById('reward-priority-error');
    error.hidden = true;
    const focused = document.activeElement?.id;
    rewardPrioritySaving = true;
    renderRewardPriority(order);
    let restoreFocus = Boolean(focused);
    const focusEvents = ['focusin', 'pointerdown', 'keydown'];
    const onFocusMove = event => {
        if (event.type !== 'keydown' || event.key === 'Tab') restoreFocus = false;
    };
    if (focused) {
        for (const type of focusEvents) document.addEventListener(type, onFocusMove, true);
    }
    try {
        const response = await fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({reward_priority:order})});
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (data.success !== true || JSON.stringify(data.settings?.reward_priority) !== JSON.stringify(order)) throw new Error();
        updateSettingsUI(data.settings, false, ['reward_priority']);
    } catch (_) {
        error.textContent = state.translations.gui?.settings?.reward_priority_error || '';
        error.hidden = false;
    } finally {
        if (focused) {
            for (const type of focusEvents) document.removeEventListener(type, onFocusMove, true);
        }
        rewardPrioritySaving = false;
        renderRewardPriority();
        const control = restoreFocus ? document.getElementById(focused) : null;
        if (control && !control.disabled) control.focus();
    }
}

function toggleMiningHoursRange(enabled) {
    const container = document.getElementById('mining-hours-range-inputs');
    if (container) container.style.display = enabled ? '' : 'none';
}

async function onMiningHoursChange() {
    const always = document.getElementById('mining-hours-always');
    const range = document.getElementById('mining-hours-range');
    const start = document.getElementById('mining-hours-start');
    const end = document.getElementById('mining-hours-end');
    const error = document.getElementById('mining-hours-error');
    const controls = [always, range, start, end];
    const previous = state.settings.mining_hours || { mode: 'always', start: '08:00', end: '22:00' };
    error.hidden = true;
    error.textContent = '';
    toggleMiningHoursRange(range.checked);
    if (range.checked && (!start.reportValidity() || !end.reportValidity())) return;
    const requested = {
        mode: range.checked ? 'range' : 'always',
        start: start.value || previous.start,
        end: end.value || previous.end
    };
    const message = state.translations.gui?.settings?.mining_hours_save_error ||
        'Could not save mining hours. Please try again.';
    controls.forEach(control => { control.disabled = true; });
    try {
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mining_hours: requested })
        });
        if (!response.ok) throw new Error(message);
        const data = await response.json();
        const saved = data.settings?.mining_hours;
        if (data.success !== true || !saved ||
            ['mode', 'start', 'end'].some(key => saved[key] !== requested[key])) throw new Error(message);
        updateSettingsUI(data.settings);
    } catch (failure) {
        always.checked = previous.mode !== 'range';
        range.checked = previous.mode === 'range';
        start.value = previous.start;
        end.value = previous.end;
        toggleMiningHoursRange(range.checked);
        error.textContent = message;
        error.hidden = false;
    } finally {
        controls.forEach(control => { control.disabled = false; });
    }
}

async function saveSettings() {
    const settings = {
        dark_mode: document.getElementById('dark-mode').checked,
        inventory_list_view: state.settings.inventory_list_view === true,
        language: document.getElementById('language').value,
        connection_quality: parseInt(document.getElementById('connection-quality').value),
        minimum_refresh_interval_minutes: parseInt(document.getElementById('minimum-refresh-interval').value),
        games_to_watch: state.settings.games_to_watch || [],
        drop_name_blacklist: parseDropNameBlacklist(
            document.getElementById('drop-name-blacklist')?.value
        ),
        inventory_filters: getInventoryFilters(),
        // Omitted until settings load, so an early save cannot overwrite the saved types.
        mining_benefits: state.settings.mining_benefits,
    };

    try {
        await queueSettingsWrite(settings);
        console.log('Settings saved automatically');
    } catch (error) {
        console.error('Failed to save settings:', error);
    }
}

/* The proxy saves alone: a rejected value must not block or partially apply autosaves. */
async function saveProxy() {
    const button = document.getElementById('set-proxy-btn');
    const proxyInput = document.getElementById('proxy-url');
    const resultDiv = document.getElementById('proxy-verify-result');
    const newValue = proxyInput ? proxyInput.value.trim() : '';
    if (button.disabled || newValue === (state.settings.proxy || '')) return;
    const message = state.translations.gui?.settings?.proxy_save_error ||
        'Could not save the proxy. Re-enter its password and try again.';
    button.disabled = true;
    try {
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proxy: newValue })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.success !== true) throw new Error('proxy_save_failed');
        state.settings.proxy = data.settings?.proxy ?? newValue;
        if (proxyInput) proxyInput.value = state.settings.proxy;
        resultDiv.style.display = 'none';
    } catch (error) {
        if (proxyInput) proxyInput.value = state.settings.proxy || '';
        resultDiv.className = 'verify-result error';
        resultDiv.textContent = message;
        resultDiv.style.display = 'block';
    } finally {
        button.disabled = false;
    }
}

async function onPriorityModeChange() {
    const input = document.getElementById('priority-list-only');
    if (input.disabled) return;
    const requested = input.checked;
    const message = state.translations.gui?.settings?.priority_list_only_save_error ||
        'Could not save the game priority option. Please try again.';
    input.disabled = true;
    try {
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ priority_list_only: requested })
        });
        if (!response.ok) throw new Error(message);
        const data = await response.json();
        if (data.success !== true || data.settings?.priority_list_only !== requested) throw new Error(message);
        updateSettingsUI(data.settings);
    } catch (failure) {
        input.checked = state.settings.priority_list_only !== false;
        renderChannels();
        showToast(message, 'error');
    } finally {
        input.disabled = false;
    }
}

async function onUnlinkedMiningChange() {
    const input = document.getElementById('allow-unlinked-campaigns');
    const error = document.getElementById('allow-unlinked-error');
    const requested = input.checked;
    const message = state.translations.gui?.settings?.allow_unlinked_campaigns_save_error ||
        'Could not save the account-link mining option. Please try again.';
    input.disabled = true;
    error.hidden = true;
    error.textContent = '';
    try {
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ allow_unlinked_campaigns: requested })
        });
        if (!response.ok) throw new Error(message);
        const data = await response.json();
        if (data.success !== true || data.settings?.allow_unlinked_campaigns !== requested) throw new Error(message);
        updateSettingsUI(data.settings);
    } catch (failure) {
        input.checked = state.settings.allow_unlinked_campaigns === true;
        error.textContent = message;
        error.hidden = false;
    } finally {
        input.disabled = false;
    }
}

async function fetchAndPopulateLanguages() {
    try {
        const response = await fetch('/api/languages');
        const data = await response.json();

        const languageSelect = document.getElementById('language');
        if (!languageSelect) {
            console.warn('Language select element not found');
            return;
        }

        // Clear existing options
        languageSelect.innerHTML = '';

        // Populate with available languages
        data.available.forEach(lang => {
            const option = document.createElement('option');
            option.value = lang;
            option.textContent = lang;
            languageSelect.appendChild(option);
        });

        // Set current language
        if (data.current) {
            languageSelect.value = data.current;
        }
    } catch (error) {
        console.error('Failed to fetch languages:', error);
        const languageSelect = document.getElementById('language');
        if (languageSelect) {
            languageSelect.replaceChildren(makeElement('option', { value: '' }, 'Failed to load languages'));
        }
        addConsoleLine('Error: Unable to fetch available languages. Please check your connection or try again later.');
    }
}

async function fetchAndApplyTranslations() {
    try {
        const response = await fetch('/api/translations');
        const data = await response.json();

        state.translations = data;
        applyTranslations(data);
        console.log('Translations applied for language:', data.language_name);
    } catch (error) {
        console.error('Failed to fetch translations:', error);
    }
}

function applyTranslations(t) {
    globalThis.browserLoginPanel?.render();
    translateHistory();
    // Update tab buttons
    const tabButtons = {
        'main': document.querySelector('[data-tab="main"]'),
        'inventory': document.querySelector('[data-tab="inventory"]'),
        'history': document.querySelector('[data-tab="history"]'),
        'settings': document.querySelector('[data-tab="settings"]'),
        'help': document.querySelector('[data-tab="help"]')
    };

    if (tabButtons.main && t.gui?.tabs) tabButtons.main.textContent = t.gui.tabs.main;
    if (tabButtons.inventory && t.gui?.tabs) tabButtons.inventory.textContent = t.gui.tabs.inventory;
    if (tabButtons.history && t.gui?.tabs) tabButtons.history.textContent = t.gui.tabs.history;
    if (tabButtons.settings && t.gui?.tabs) tabButtons.settings.textContent = t.gui.tabs.settings;
    if (tabButtons.help && t.gui?.tabs) tabButtons.help.textContent = t.gui.tabs.help;

    // Update Main tab - Login section
    const mainTab = document.getElementById('main-tab');
    if (mainTab && t.gui?.login) {
        const loginHeader = mainTab.querySelector('.login-panel h2');
        if (loginHeader) loginHeader.textContent = t.gui.login.name;

        if (state.login) updateLoginStatus(state.login);
    }

    // Update Progress section
    if (mainTab && t.gui?.progress) {
        // ID: progress-header
        const progressHeader = document.getElementById('progress-header');
        if (progressHeader) progressHeader.textContent = t.gui.progress.name;

        const noDropMsg = document.getElementById('no-drop-message');
        if (noDropMsg) noDropMsg.textContent = t.gui.progress.no_drop;

        const exitManualBtn = document.getElementById('exit-manual-btn');
        if (exitManualBtn) exitManualBtn.textContent = t.gui.progress.return_to_auto;
    }

    // Update Console section
    if (mainTab && t.gui) {
        // ID: console-header
        const consoleHeader = document.getElementById('console-header');
        if (consoleHeader) consoleHeader.textContent = t.gui.output;
    }

    // Update Channels section
    if (mainTab && t.gui?.channels) {
        // ID: channels-header
        const channelsHeader = document.getElementById('channels-header');
        if (channelsHeader) channelsHeader.textContent = t.gui.channels.name;
        // Channel list will re-render with translated empty messages
        renderChannels();
    }

    // Update Inventory tab
    const inventoryTab = document.getElementById('inventory-tab');
    if (inventoryTab && t.gui?.inventory) {
        // Inventory will re-render with translated status and empty messages
        renderInventory();
    }

    // Update Settings tab
    const settingsTab = document.getElementById('settings-tab');
    if (settingsTab && t.gui?.settings) {
        // Use IDs for robust selection
        const generalHeader = document.getElementById('settings-general-header');
        if (generalHeader) generalHeader.textContent = t.gui.settings.general.name;

        const allowUnlinkedLabel = document.getElementById('allow-unlinked-label');
        if (allowUnlinkedLabel) allowUnlinkedLabel.textContent = t.gui.settings.allow_unlinked_campaigns;
        const allowUnlinkedWarning = document.getElementById('allow-unlinked-warning');
        if (allowUnlinkedWarning) allowUnlinkedWarning.textContent = t.gui.settings.allow_unlinked_campaigns_warning;
        const allowUnlinkedError = document.getElementById('allow-unlinked-error');
        if (allowUnlinkedError && !allowUnlinkedError.hidden) allowUnlinkedError.textContent = t.gui.settings.allow_unlinked_campaigns_save_error;
        if (typeof renderRewardPriority === 'function') renderRewardPriority();

        const dropBlacklistHeader = document.getElementById('settings-drop-blacklist-header');
        if (dropBlacklistHeader) dropBlacklistHeader.textContent = t.gui.settings.drop_name_blacklist;

        const dropBlacklistHelp = document.getElementById('settings-drop-blacklist-help');
        if (dropBlacklistHelp) dropBlacklistHelp.textContent = t.gui.settings.drop_name_blacklist_help;

        const dropBlacklistInput = document.getElementById('drop-name-blacklist');
        if (dropBlacklistInput) dropBlacklistInput.placeholder = t.gui.settings.drop_name_blacklist_placeholder;

        const gamesHeader = document.getElementById('settings-games-header');
        if (gamesHeader) gamesHeader.textContent = t.gui.settings.games_to_watch;

        const actionsHeader = document.getElementById('settings-actions-header');
        if (actionsHeader) actionsHeader.textContent = t.gui.settings.actions;

        const darkModeLabel = settingsTab.querySelector('label:has(#dark-mode)');
        if (darkModeLabel) {
            const checkbox = darkModeLabel.querySelector('input');
            darkModeLabel.textContent = '';
            darkModeLabel.appendChild(checkbox);
            darkModeLabel.appendChild(document.createTextNode(' ' + t.gui.settings.general.dark_mode));
        }

        const priorityListOnlyLabel = settingsTab.querySelector('label:has(#priority-list-only)');
        if (priorityListOnlyLabel && t.gui.settings.priority_list_only) {
            const checkbox = priorityListOnlyLabel.querySelector('input');
            priorityListOnlyLabel.textContent = '';
            priorityListOnlyLabel.appendChild(checkbox);
            priorityListOnlyLabel.appendChild(document.createTextNode(' ' + t.gui.settings.priority_list_only));
        }

        const connQualityLabel = settingsTab.querySelector('label:has(#connection-quality)');
        if (connQualityLabel) {
            const input = connQualityLabel.querySelector('input');
            connQualityLabel.textContent = t.gui.settings.connection_quality + ' ';
            connQualityLabel.appendChild(input);
        }

        const refreshLabel = settingsTab.querySelector('label:has(#minimum-refresh-interval)');
        if (refreshLabel) {
            const input = refreshLabel.querySelector('input');
            refreshLabel.textContent = t.gui.settings.minimum_refresh + ' ';
            refreshLabel.appendChild(input);
        }


        const hoursHeader = document.getElementById('settings-hours-header');
        if (hoursHeader && t.gui.settings.mining_hours) hoursHeader.textContent = t.gui.settings.mining_hours;

        const hoursHelp = document.getElementById('settings-hours-help');
        if (hoursHelp && t.gui.settings.mining_hours_help) hoursHelp.textContent = t.gui.settings.mining_hours_help;

        const hoursAlwaysLabel = document.getElementById('settings-hours-always-label');
        if (hoursAlwaysLabel && t.gui.settings.mining_hours_always) hoursAlwaysLabel.textContent = t.gui.settings.mining_hours_always;

        const hoursRangeLabel = document.getElementById('settings-hours-range-label');
        if (hoursRangeLabel && t.gui.settings.mining_hours_range) hoursRangeLabel.textContent = t.gui.settings.mining_hours_range;

        const hoursStartLabel = document.getElementById('settings-hours-start-label');
        if (hoursStartLabel && t.gui.settings.mining_hours_start) hoursStartLabel.textContent = t.gui.settings.mining_hours_start + ' ';

        const hoursEndLabel = document.getElementById('settings-hours-end-label');
        if (hoursEndLabel && t.gui.settings.mining_hours_end) hoursEndLabel.textContent = t.gui.settings.mining_hours_end + ' ';

        const gamesHelp = document.getElementById('settings-games-help');
        if (gamesHelp) gamesHelp.textContent = t.gui.settings.games_help;

        const searchInput = document.getElementById('games-filter');
        if (searchInput) searchInput.placeholder = t.gui.settings.search_games;

        const selectLinkedBtn = document.getElementById('select-linked-btn');
        if (selectLinkedBtn && t.gui.settings.select_linked) selectLinkedBtn.textContent = t.gui.settings.select_linked;

        const gamesLinkFilter = document.getElementById('games-link-filter');
        if (gamesLinkFilter && t.gui.inventory?.filters) {
            const f = t.gui.inventory.filters;
            gamesLinkFilter.querySelector('[value="all"]').textContent = f.all;
            gamesLinkFilter.querySelector('[value="linked"]').textContent = f.linked;
            gamesLinkFilter.querySelector('[value="not_linked"]').textContent = f.not_linked;
            if (f.account_link) gamesLinkFilter.setAttribute('aria-label', f.account_link);
        }

        const selectAllBtn = document.getElementById('select-all-btn');
        if (selectAllBtn) selectAllBtn.textContent = t.gui.settings.select_all;

        const deselectAllBtn = document.getElementById('deselect-all-btn');
        if (deselectAllBtn) deselectAllBtn.textContent = t.gui.settings.deselect_all;

        const addGameBtn = document.getElementById('add-game-btn');
        if (addGameBtn && t.gui.settings.add_game) addGameBtn.textContent = t.gui.settings.add_game;

        const selectedGamesHeader = settingsTab.querySelector('.selected-games h3');
        if (selectedGamesHeader) selectedGamesHeader.textContent = t.gui.settings.selected_games;

        const availableGamesHeader = settingsTab.querySelector('.available-games h3');
        if (availableGamesHeader) availableGamesHeader.textContent = t.gui.settings.available_games;

        for (const id of ['inventory-reload-label', 'games-reload-label']) {
            const label = document.getElementById(id);
            if (label) label.textContent = t.gui.settings.reload_campaigns;
        }

        const discordTrans = t.gui?.settings?.discord;
        if (discordTrans) {
            const labels = {
                'settings-discord-header': 'name', 'settings-discord-help': 'description',
                'settings-discord-webhook-url-label': 'webhook_url',
                'settings-discord-webhook-url-hint': 'webhook_url_hint',
                'settings-discord-credentials-help': 'credentials_help',
                'save-discord-btn': 'save_settings', 'test-discord-btn': 'test_connection',
                'remove-discord-btn': 'remove_webhook'
            };
            for (const [id, key] of Object.entries(labels)) {
                const element = document.getElementById(id);
                if (element) element.textContent = discordTrans[key];
            }
        }

        // Update Telegram Notifications section.
        const tgTrans = (t.settings && t.settings.telegram) || (t.gui && t.gui.settings && t.gui.settings.telegram) || null;
        if (tgTrans) {
            const telegramSection = settingsTab.querySelector('.settings-section:has(#telegram-bot-token)');
            if (telegramSection) {
                const heading = telegramSection.querySelector('h2');
                if (heading) heading.textContent = tgTrans.name || heading.textContent;

                const description = telegramSection.querySelector('.help-text');
                if (description) description.textContent = tgTrans.description || description.textContent;

                const botTokenLabel = document.getElementById('settings-telegram-bot-token-label');
                if (botTokenLabel) botTokenLabel.textContent = tgTrans.bot_token || botTokenLabel.textContent;

                const chatIdLabel = document.getElementById('settings-telegram-chat-id-label');
                if (chatIdLabel) chatIdLabel.textContent = tgTrans.chat_id || chatIdLabel.textContent;

                const yourUserId = document.getElementById('settings-telegram-your-user-id');
                if (yourUserId) yourUserId.textContent = tgTrans.your_user_id || yourUserId.textContent;

                const fromBotFather = document.getElementById('settings-telegram-get-from-botfather');
                if (fromBotFather) fromBotFather.textContent = tgTrans.get_from_botfather || fromBotFather.textContent;

                const saveTelegramBtn = document.getElementById('save-telegram-btn');
                if (saveTelegramBtn) saveTelegramBtn.textContent = tgTrans.save_settings || saveTelegramBtn.textContent;

                const testTelegramBtn = document.getElementById('test-telegram-btn');
                if (testTelegramBtn) testTelegramBtn.textContent = tgTrans.test_connection || testTelegramBtn.textContent;

                const credentialsHelp = document.getElementById('settings-telegram-credentials-help');
                if (credentialsHelp) credentialsHelp.textContent = tgTrans.credentials_help || credentialsHelp.textContent;
            }
        }

        const clearCacheBtn = document.getElementById('clear-cache-btn');
        if (clearCacheBtn) clearCacheBtn.textContent = t.gui.settings.clear_all_cache;

        const clearCacheHelp = document.getElementById('clear-cache-help');
        if (clearCacheHelp) clearCacheHelp.textContent = t.gui.settings.clear_all_cache_help;
        const diagnosticsButton = document.getElementById('dump-diagnostics-btn');
        if (diagnosticsButton) diagnosticsButton.textContent = t.gui.settings.dump_diagnostics;
        const diagnosticsHelp = document.getElementById('diagnostics-help');
        if (diagnosticsHelp) diagnosticsHelp.textContent = t.gui.settings.diagnostics_help;

        // Re-render games to watch with translated empty messages
        renderGamesToWatch();
    }

    // Update Help tab
    const helpTab = document.getElementById('help-tab');
    if (helpTab && t.gui?.help) {
        // Robust ID selection for Help tab headers
        const aboutHeader = document.getElementById('help-about-header');
        if (aboutHeader) aboutHeader.textContent = t.gui.help.about || 'About Twitch Drops Miner';

        const howtoHeader = document.getElementById('help-howto-header');
        if (howtoHeader) howtoHeader.textContent = t.gui.help.how_to_use || 'How to Use';

        const featuresHeader = document.getElementById('help-features-header');
        if (featuresHeader) featuresHeader.textContent = t.gui.help.features || 'Features';

        const notesHeader = document.getElementById('help-notes-header');
        if (notesHeader) notesHeader.textContent = t.gui.help.important_notes || 'Important Notes';

        // Build translated lists and allowlisted links with DOM nodes.
        const helpContent = helpTab.querySelector('.help-content');
        if (helpContent) {
            const howToItems = t.gui.help.how_to_use_items || [
                'Login using your Twitch account (OAuth device code flow)',
                'Link your accounts at <a href="https://www.twitch.tv/drops/campaigns" target="_blank">twitch.tv/drops/campaigns</a>',
                'The miner will automatically discover campaigns and start mining',
                'Configure priority games in Settings to focus on what you want',
                'Monitor progress in the Main and Inventory tabs'
            ];
            const featuresItems = t.gui.help.features_items || [
                'Stream-less drop mining - saves bandwidth',
                'Game priority and exclusion lists',
                'Tracks up to 199 channels simultaneously',
                'Automatic channel switching',
                'Real-time progress tracking'
            ];
            const notesItems = t.gui.help.important_notes_items || [
                'Do not watch streams on the same account while mining',
                'Keep your cookies.jar file secure',
                'Requires linked game accounts for drops'
            ];

            helpContent.replaceChildren(
                makeElement('h2', { id: 'help-about-header' }, t.gui.help.about || 'About Twitch Drops Miner'),
                makeElement('p', {}, t.gui.help.about_text || 'This application automatically mines timed Twitch drops without downloading stream data.'),
                makeElement('h3', { id: 'help-howto-header' }, t.gui.help.how_to_use || 'How to Use'),
                makeHelpList('ol', howToItems),
                makeElement('h3', { id: 'help-features-header' }, t.gui.help.features || 'Features'),
                makeHelpList('ul', featuresItems),
                makeElement('h3', { id: 'help-notes-header' }, t.gui.help.important_notes || 'Important Notes'),
                makeHelpList('ul', notesItems),
                ...(function () {
                    const tg = tgHelpSetup(t);
                    return tg
                        ? [makeElement('h3', { id: 'help-telegram-header' }, tg.title),
                           makeElement('p', {}, tg.description),
                           makeHelpList('ol', tg.steps)]
                        : [];
                })(),
                ...(function () {
                    const dc = t.gui?.settings?.discord;
                    return dc ? [makeElement('h3', { id: 'help-discord-header' }, dc.name),
                        makeElement('p', {}, dc.description), makeHelpList('ol', dc.setup_steps)] : [];
                })(),
                makeElement('div', { class: 'help-links' }, '', el =>
                    el.appendChild(makeElement('a', { href: 'https://github.com/rangermix/TwitchDropsMiner', target: '_blank', rel: 'noopener noreferrer' }, t.gui.help.github_repo || 'GitHub Repository'))
                ),
            );
        }
    }

    // Update Footer
    if (t.gui?.footer) {
        const loadingText = t.gui.footer.loading || 'Loading...';
        const currentVersionEl = document.getElementById('current-version');
        // Only update if it's the specific "Loading..." text to avoid overwriting the fetched version
        if (currentVersionEl && currentVersionEl.textContent === 'Loading...') {
            currentVersionEl.textContent = loadingText;
        }

        const footerVersionText = document.getElementById('footer-version-text');
        if (footerVersionText) {
            const versionLabel = t.gui.footer.version || 'Version:';
            const span = document.getElementById('current-version'); // Need to re-fetch or preserve
            footerVersionText.textContent = versionLabel + ' ';
            // Re-finding the span because textContent wiped it from parent
            if (span) footerVersionText.appendChild(span);
        }
    }

    // Update Badges tooltips
    if (t.gui?.badges) {
        const manualBadge = document.getElementById('manual-mode-badge');
        if (manualBadge && t.gui.badges.manual) manualBadge.title = t.gui.badges.manual.title;

        const autoBadge = document.getElementById('auto-mode-badge');
        if (autoBadge && t.gui.badges.auto) autoBadge.title = t.gui.badges.auto.title;

        const proxyBadge = document.getElementById('proxy-indicator');
        if (proxyBadge && t.gui.badges.proxy) proxyBadge.title = t.gui.badges.proxy.title; // Note: append logic in updateSettingsUI overrides this
    }

    // Update Wanted Drops Panel
    if (mainTab && t.gui?.wanted) {
        // ID: wanted-header
        const wantedHeader = document.getElementById('wanted-header');
        if (wantedHeader) wantedHeader.textContent = t.gui.wanted.name;
        // Re-render wanted items to update empty message
        // Since we don't store wanted items in state globally (only receives them), we rely on updateWantedItems triggering render
    }

    // Update Inventory Filters (re-using existing inventoryTab variable if available, or just querying)
    // Note: inventoryTab was declared above in "Update Inventory Status" section
    // But since that might be in a different block or not, let's be safe and just query element directly without const redeclaration if it conflicts.
    // However, looking at the code, the previous declaration was likely in the same function scope.
    // Simplest fix: use the existing element or re-query without 'const' if needed, but best to just use the one we have.
    // Actually, looking at the view_file, there was 'const inventoryTab' around line 1639.
    // So I should just reuse that variable or use a different name.

    if (inventoryTab && t.gui?.inventory?.filters) {
        const f = t.gui.inventory.filters;
        const updateLabel = (id, text) => {
            const el = document.getElementById(id)?.parentElement.querySelector('span');
            if (el) el.textContent = text;
        };
        updateLabel('filter-active', f.active);
        document.getElementById('filter-link-status-label').textContent = f.account_link;
        for (const [id, text] of [['filter-status-heading', f.status_heading], ['filter-rewards-heading', f.rewards_heading]]) {
            const heading = document.getElementById(id);
            if (heading && text) heading.textContent = text;
        }
        const linkStatus = document.getElementById('filter-link-status');
        linkStatus.querySelector('[value="all"]').textContent = f.all;
        linkStatus.querySelector('[value="linked"]').textContent = f.linked;
        linkStatus.querySelector('[value="not_linked"]').textContent = f.not_linked;
        updateLabel('filter-upcoming', f.upcoming);
        updateLabel('filter-expired', f.expired);
        updateLabel('filter-finished', f.finished);
        updateLabel('filter-benefit-item', f.item);
        updateLabel('filter-benefit-badge', f.badge);
        updateLabel('filter-benefit-emote', f.emote);
        updateLabel('filter-benefit-other', f.other);

        const clearBtn = document.getElementById('clear-filters-btn');
        if (clearBtn) clearBtn.textContent = f.clear;
        for (const [id, key] of [['inventory-view-cards', 'card_view'], ['inventory-view-list', 'list_view']]) {
            const label = t.gui?.inventory?.[key];
            if (label) document.getElementById(id)?.setAttribute('aria-label', label);
            if (label) document.getElementById(id)?.setAttribute('title', label);
        }

        const searchInput = document.getElementById('games-filter');
        if (searchInput) searchInput.placeholder = f.search_placeholder;

    }

    // Update header elements
    if (t.gui?.header) {
        const languageLabel = document.querySelector('.language-selector span');
        if (languageLabel) languageLabel.textContent = t.gui.header.language;

        const statusText = document.getElementById('status-text');
        if (statusText && statusText.textContent === 'Initializing...') {
            statusText.textContent = t.gui.header.initializing;
        }

        // Update connection indicator
        const connIndicator = document.getElementById('connection-indicator');
        if (connIndicator) {
            if (state.connected) {
                connIndicator.textContent = '● ' + (t.gui.websocket.connected || 'Connected');
            } else {
                connIndicator.textContent = '● ' + (t.gui.websocket.disconnected || 'Disconnected');
            }
        }
    }
}

async function requestCampaignRefresh(endpoint, button, actionName) {
    if (button) button.disabled = true;

    try {
        const response = await fetch(endpoint, { method: 'POST' });
        if (!response.ok) {
            throw new Error(`${actionName} failed with HTTP ${response.status}`);
        }
        // Status will update via Socket.IO when backend starts operation
    } catch (error) {
        console.error(`Failed to ${actionName}:`, error);
    } finally {
        if (button) button.disabled = false;
    }
}

// Inventory and the Mining list share one reload; every reload button waits for it.
function reloadButtons() {
    return [...document.querySelectorAll('.reload-campaigns-btn')];
}

async function reloadCampaigns() {
    const buttons = reloadButtons();
    if (buttons.some(button => button.disabled)) return;
    buttons.forEach(button => { button.disabled = true; button.setAttribute('aria-busy', 'true'); });
    try {
        await requestCampaignRefresh('/api/reload', null, 'reload campaigns');
    } finally {
        buttons.forEach(button => { button.disabled = false; button.removeAttribute('aria-busy'); });
    }
}

async function clearAllCache() {
    const button = document.getElementById('clear-cache-btn');
    await requestCampaignRefresh('/api/cache/clear', button, 'clear cache');
}

async function dumpDiagnostics() {
    const button = document.getElementById('dump-diagnostics-btn');
    const result = document.getElementById('diagnostics-result');
    if (!button || !result || button.disabled) return;
    const t = state.translations?.gui?.settings;
    if (!t) return;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    result.textContent = t.diagnostics_saving;
    result.className = 'help-text';
    const agent = navigator.userAgent || '';
    const browser = /Edg\//.test(agent) ? 'Edge' : /Firefox\//.test(agent) ? 'Firefox'
        : /Chrome\//.test(agent) ? 'Chrome' : /Safari\//.test(agent) ? 'Safari' : 'Other';
    const platform = /Android/.test(agent) ? 'Android' : /iPhone|iPad/.test(agent) ? 'iOS'
        : /Windows/.test(agent) ? 'Windows' : /Macintosh/.test(agent) ? 'macOS'
        : /Linux/.test(agent) ? 'Linux' : 'Other';
    try {
        const response = await fetch('/api/diagnostics', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-TDM-Request': '1' },
            body: JSON.stringify({ browser: {
                width: Math.min(32000, Math.max(0, Math.round(window.innerWidth))),
                height: Math.min(32000, Math.max(0, Math.round(window.innerHeight))),
                browser, platform, tab: 'settings', online: Boolean(navigator.onLine),
                socket_connected: Boolean(socket && socket.connected)
            } })
        });
        const data = await response.json();
        if (!response.ok || data.success !== true || typeof data.file !== 'string') {
            result.textContent = response.status === 429 ? t.diagnostics_busy : t.diagnostics_error;
            result.className = 'help-text error';
            return;
        }
        result.textContent = t.diagnostics_saved.replace('{path}', `data/${data.file}`);
        result.className = 'help-text success';
    } catch (_) {
        result.textContent = t.diagnostics_error;
        result.className = 'help-text error';
    } finally {
        button.disabled = false;
        button.removeAttribute('aria-busy');
    }
}


// ==================== Tab Management ====================

function switchTab(tabName) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });
    document.querySelectorAll('.tab-button').forEach(btn => {
        btn.classList.remove('active');
    });

    // Show selected tab
    document.getElementById(`${tabName}-tab`).classList.add('active');
    document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');

    // Lazy-load history data each time the History tab is opened
    if (tabName === 'history') {
        loadHistory();
    }
}

// ==================== Event Listeners ====================

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('now-watching-toggle').addEventListener('click', toggleNowWatchingPreview);
    // Fetch and display version information
    fetchAndDisplayVersion();

    // Tab switching
    document.querySelectorAll('.tab-button').forEach(button => {
        button.addEventListener('click', () => {
            switchTab(button.dataset.tab);
        });
    });

    // Settings - auto-save on change
    document.getElementById('dark-mode').addEventListener('change', (e) => {
        // Apply dark mode immediately for instant feedback
        if (e.target.checked) {
            document.body.classList.add('dark-mode');
        } else {
            document.body.classList.remove('dark-mode');
        }
        // Then save settings
        saveSettings();
    });

    // Re-render when the column count changes (resizes, or the Inventory tab becoming visible).
    new ResizeObserver(() => {
        const grid = document.getElementById('inventory-grid');
        // Next frame: re-rendering inside the callback would trigger a ResizeObserver loop warning.
        if (grid.dataset.columns && String(inventoryColumnCount(grid)) !== grid.dataset.columns) requestAnimationFrame(renderInventory);
    }).observe(document.getElementById('inventory-grid'));
    document.getElementById('inventory-view-cards').addEventListener('click', () => setInventoryViewMode(false));
    document.getElementById('inventory-view-list').addEventListener('click', () => setInventoryViewMode(true));
    document.getElementById('reward-priority-enabled').addEventListener('change', event =>
        saveRewardPriority(event.target.checked ? displayedRewardOrder() : []));
    document.getElementById('priority-list-only').addEventListener('change', onPriorityModeChange);
    document.getElementById('language').addEventListener('change', saveSettings);
    document.getElementById('connection-quality').addEventListener('change', saveSettings);
    document.getElementById('minimum-refresh-interval').addEventListener('change', saveSettings);
    ['mining-hours-always', 'mining-hours-range', 'mining-hours-start', 'mining-hours-end']
        .forEach(id => document.getElementById(id).addEventListener('change', onMiningHoursChange));
    document.getElementById('drop-name-blacklist').addEventListener('change', saveSettings);
    document.getElementById('allow-unlinked-campaigns').addEventListener('change', onUnlinkedMiningChange);
    // Proxy uses a manual "Set Proxy" button instead of auto-save
    document.getElementById('set-proxy-btn').addEventListener('click', saveProxy);
    document.getElementById('verify-proxy-btn').addEventListener('click', verifyProxy);
    document.getElementById('test-discord-btn').addEventListener('click', () => updateDiscordSettings('test'));
    document.getElementById('save-discord-btn').addEventListener('click', () => updateDiscordSettings('save'));
    document.getElementById('remove-discord-btn').addEventListener('click', () => updateDiscordSettings('remove'));
    document.getElementById('test-telegram-btn').addEventListener('click', testTelegramConnection);
    document.getElementById('save-telegram-btn').addEventListener('click', handleSaveTelegramClick);
    reloadButtons().forEach(button => button.addEventListener('click', reloadCampaigns));
    document.getElementById('clear-cache-btn').addEventListener('click', clearAllCache);
    document.getElementById('dump-diagnostics-btn').addEventListener('click', dumpDiagnostics);

    // History tab
    document.getElementById('history-btn-filter')?.addEventListener('click', () => {
        historyCurrentPage = 0;
        loadHistory();
    });
    document.getElementById('history-btn-export')?.addEventListener('click', exportHistoryCSV);
    document.getElementById('history-btn-stats')?.addEventListener('click', toggleHistoryStats);
    document.getElementById('history-btn-clear')?.addEventListener('click', clearHistory);
    document.getElementById('history-filter-game')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            historyCurrentPage = 0;
            loadHistory();
        }
    });


    // Games to watch management
    document.getElementById('select-linked-btn').addEventListener('click', selectLinkedGames);
    document.getElementById('games-link-filter').addEventListener('change', renderGamesToWatch);
    document.getElementById('select-all-btn').addEventListener('click', selectAllGames);
    document.getElementById('deselect-all-btn').addEventListener('click', deselectAllGames);
    document.getElementById('add-game-btn').addEventListener('click', addGameFromSearch);
    document.getElementById('games-filter').addEventListener('input', renderGamesToWatch);
    document.getElementById('games-filter').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            addGameFromSearch();
        }
    });

    // Inventory filters
    document.getElementById('filter-active').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-link-status').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-upcoming').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-expired').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-finished').addEventListener('change', onInventoryFilterChange);
    // Benefit type filters
    document.getElementById('filter-benefit-item').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-benefit-badge').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-benefit-emote').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-benefit-other').addEventListener('change', onInventoryFilterChange);
    document.getElementById('clear-filters-btn').addEventListener('click', clearInventoryFilters);


    // Inventory game search dropdown
    const gameSearchInput = document.getElementById('inventory-game-search');
    gameSearchInput.addEventListener('focus', () => {
        showGameDropdown();
    });
    gameSearchInput.addEventListener('input', (e) => {
        renderGameDropdown(e.target.value);
    });
    gameSearchInput.addEventListener('keydown', handleGameSearchKeydown);

    // Click outside to close dropdown
    document.addEventListener('click', (e) => {
        const container = document.querySelector('.game-dropdown-container');
        if (container && !container.contains(e.target) && gameDropdownVisible) {
            closeGameDropdown();
        }
    });

    // Manual mode controls
    const exitManualBtn = document.getElementById('exit-manual-btn');
    if (exitManualBtn) {
        exitManualBtn.addEventListener('click', exitManualMode);
    }

    // Fetch and populate available languages
    fetchAndPopulateLanguages();

    // Fetch and apply translations for the current language
    fetchAndApplyTranslations();

    // Request notification permission
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }
});


// ==================== Wanted Items Rendering ====================

function renderWantedItems(tree) {
    const container = document.getElementById('wanted-items-list');
    if (!container) return;

    container.innerHTML = '';

    if (!tree || tree.length === 0) {
        const emptyMsg = state.translations.gui?.wanted?.none || 'No wanted drops queued...';
        container.replaceChildren(makeElement('p', { class: 'empty-message-small' }, emptyMsg));
        return;
    }

    tree.forEach((gameGroup, index) => {
        const groupEl = document.createElement('div');
        groupEl.className = 'wanted-game-group';

        // Game Icon
        let iconUrl = gameGroup.game_icon;
        if (iconUrl) {
            iconUrl = iconUrl.replace('{width}', '144').replace('{height}', '192');
        }

        const headerChildren = [makeElement('span', { class: 'wanted-game-index' }, `#${index + 1}`)];
        if (iconUrl) {
            headerChildren.push(makeImageElement(iconUrl, gameGroup.game_name, 'wanted-game-icon'));
        }
        headerChildren.push(makeElement('span', { class: 'wanted-game-title' }, gameGroup.game_name));

        const headerEl = makeElement('div', { class: 'wanted-game-header' }, '', el => {
            headerChildren.forEach(child => el.appendChild(child));
        });
        groupEl.appendChild(headerEl);

        const campaignListEl = document.createElement('div');
        campaignListEl.className = 'wanted-campaign-list';

        gameGroup.campaigns.forEach(campaign => {
            const dropContainer = makeElement('div', {});
            const cardEl = makeElement('div', { class: 'wanted-card' }, '', el => {
                el.appendChild(makeElement('div', { class: 'wanted-card-header' }, '', h =>
                    h.appendChild(makeElement('a', { href: campaign.url, target: '_blank', rel: 'noopener noreferrer', class: 'wanted-card-campaign-link', title: campaign.name }, campaign.name))
                ));
                el.appendChild(makeElement('div', { class: 'wanted-card-body' }, '', b =>
                    b.appendChild(dropContainer)
                ));
            });

            campaign.drops.forEach(drop => {
                const dropEl = makeElement('div', { class: 'wanted-drop-item' }, '', el => {
                    el.appendChild(makeElement('span', { class: 'wanted-drop-name' }, drop.name));
                    drop.benefits.forEach(benefit => {
                        el.appendChild(makeElement('span', { class: 'wanted-benefit-pill' }, benefit));
                    });
                });
                dropContainer.appendChild(dropEl);
            });

            campaignListEl.appendChild(cardEl);
        });

        groupEl.appendChild(campaignListEl);
        container.appendChild(groupEl);
    });
}

// ==================== Drop History ====================

const HISTORY_PAGE_SIZE = 50;
let historyAllEntries = [];
let historyCurrentPage = 0;
let historyStatsVisible = false;
let historyTotal = null;
let historyStatsData = null;
let historyMessage = {key: 'loading', values: {}};
let historyRequestId = 0;

function historyText(key, values = {}) {
    let text = state.translations.gui?.history?.[key] || key;
    Object.entries(values).forEach(([name, value]) => {
        text = text.replaceAll('{' + name + '}', String(value));
    });
    return text;
}

function translateHistory() {
    document.querySelectorAll('[data-history-key]').forEach(element => {
        element.textContent = historyText(element.dataset.historyKey);
    });
    document.querySelectorAll('[data-history-placeholder]').forEach(element => {
        const label = historyText(element.dataset.historyPlaceholder);
        element.placeholder = label;
        element.setAttribute('aria-label', label);
    });
    if (historyTotal !== null) updateHistoryCount(historyTotal, historyAllEntries.length);
    if (historyMessage) setHistoryTbodyMessage(historyMessage.key, historyMessage.values);
    else renderHistoryPage(historyCurrentPage);
    renderHistoryPagination();
    if (historyStatsData) renderHistoryStats(historyStatsData);
}

async function loadHistory() {
    const requestId = ++historyRequestId;
    const game = document.getElementById('history-filter-game')?.value.trim() || '';
    const since = document.getElementById('history-filter-since')?.value || '';

    const params = new URLSearchParams();
    if (game) params.set('game', game);
    if (since) params.set('since', since);
    params.set('limit', '5000');

    try {
        const res = await fetch(`/api/history?${params.toString()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        if (requestId !== historyRequestId) return;
        historyAllEntries = data.entries || [];
        historyTotal = data.total;
        historyCurrentPage = Math.min(historyCurrentPage, Math.max(0, Math.ceil(historyAllEntries.length / HISTORY_PAGE_SIZE) - 1));
        updateHistoryCount(data.total, historyAllEntries.length);
        renderHistoryPage(historyCurrentPage);
        renderHistoryPagination();
    } catch (err) {
        if (requestId !== historyRequestId) return;
        historyAllEntries = [];
        historyTotal = null;
        const count = document.getElementById('history-count');
        if (count) count.textContent = '';
        renderHistoryPagination();
        setHistoryTbodyMessage('load_error', {error: err.message});
    }
}

function renderHistoryPage(page) {
    const tbody = document.getElementById('history-tbody');
    if (!tbody) return;

    const start = page * HISTORY_PAGE_SIZE;
    const slice = historyAllEntries.slice(start, start + HISTORY_PAGE_SIZE);

    if (slice.length === 0) {
        setHistoryTbodyMessage('empty');
        return;
    }

    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

    historyMessage = null;
    slice.forEach((entry, idx) => {
        const tr = document.createElement('tr');
        tr.style.cssText =
            'border-bottom: 1px solid var(--border-color, #333);' +
            (idx % 2 === 0
                ? 'background: var(--bg-row-even, transparent);'
                : 'background: var(--bg-row-odd, rgba(255,255,255,0.02));');

        const claimedAt = new Date(entry.claimed_at);
        const dateStr = isNaN(claimedAt) ? entry.claimed_at : claimedAt.toLocaleString();

        appendHistoryCell(tr, dateStr, 'white-space:nowrap');
        appendHistoryCell(tr, entry.game, 'font-weight:600');
        appendHistoryCell(tr, entry.campaign);
        appendHistoryCell(tr, entry.drop_name);
        appendHistoryCell(
            tr,
            Array.isArray(entry.benefits) ? entry.benefits.join(', ') : entry.benefits,
            'color:var(--text-muted,#888); font-size:0.85em'
        );
        appendHistoryCell(tr, String(entry.required_minutes), 'text-align:right');

        tbody.appendChild(tr);
    });
}

function appendHistoryCell(tr, text, extraStyle) {
    const td = document.createElement('td');
    td.style.cssText = 'padding: 8px 12px;' + (extraStyle || '');
    td.textContent = text;
    tr.appendChild(td);
}

function setHistoryTbodyMessage(key, values = {}) {
    historyMessage = {key, values};
    const tbody = document.getElementById('history-tbody');
    if (!tbody) return;
    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 6;
    td.style.cssText = 'padding:24px; text-align:center; color:var(--text-muted,#888)';
    td.textContent = historyText(key, values);
    tr.appendChild(td);
    tbody.appendChild(tr);
}

function updateHistoryCount(total, returned) {
    const el = document.getElementById('history-count');
    if (!el) return;
    el.textContent = returned < total
        ? historyText('filtered_count', {shown: returned, total})
        : historyText('count', {total});
}

function renderHistoryPagination() {
    const container = document.getElementById('history-pagination');
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);

    const totalPages = Math.ceil(historyAllEntries.length / HISTORY_PAGE_SIZE);
    if (totalPages <= 1) return;

    if (historyCurrentPage > 0) {
        container.appendChild(makeHistoryPageBtn(historyText('previous'), () => {
            historyCurrentPage--;
            renderHistoryPage(historyCurrentPage);
            renderHistoryPagination();
            scrollHistoryToTable();
        }));
    }

    historyPageRange(historyCurrentPage, totalPages).forEach((p) => {
        if (p === '…') {
            const span = document.createElement('span');
            span.textContent = '…';
            span.style.padding = '5px 8px';
            container.appendChild(span);
        } else {
            const btn = makeHistoryPageBtn(String(p + 1), () => {
                historyCurrentPage = p;
                renderHistoryPage(historyCurrentPage);
                renderHistoryPagination();
                scrollHistoryToTable();
            });
            if (p === historyCurrentPage) {
                btn.style.background = 'var(--accent,#7287fd)';
                btn.style.color = '#fff';
            }
            container.appendChild(btn);
        }
    });

    if (historyCurrentPage < totalPages - 1) {
        container.appendChild(makeHistoryPageBtn(historyText('next'), () => {
            historyCurrentPage++;
            renderHistoryPage(historyCurrentPage);
            renderHistoryPagination();
            scrollHistoryToTable();
        }));
    }
}

function makeHistoryPageBtn(label, onClick) {
    const btn = document.createElement('button');
    btn.textContent = label;
    btn.style.cssText =
        'padding:5px 12px; border-radius:6px; border:1px solid var(--border-color,#444);' +
        'background:var(--bg-button,#313244); color:var(--text-primary,#cdd6f4); cursor:pointer;';
    btn.addEventListener('click', onClick);
    return btn;
}

function historyPageRange(current, total) {
    if (total <= 7) return Array.from({ length: total }, (_, i) => i);

    const pages = [];
    if (current <= 3) {
        pages.push(0, 1, 2, 3, 4, '…', total - 1);
    } else if (current >= total - 4) {
        pages.push(0, '…', total - 5, total - 4, total - 3, total - 2, total - 1);
    } else {
        pages.push(0, '…', current - 1, current, current + 1, '…', total - 1);
    }
    return pages;
}

function scrollHistoryToTable() {
    document.getElementById('history-table')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function exportHistoryCSV() {
    const game = document.getElementById('history-filter-game')?.value.trim() || '';
    const since = document.getElementById('history-filter-since')?.value || '';

    const params = new URLSearchParams();
    if (game) params.set('game', game);
    if (since) params.set('since', since);

    const url = `/api/history/export.csv?${params.toString()}`;

    const a = document.createElement('a');
    a.href = url;
    a.download = game ? `drop_history_${game}.csv` : 'drop_history.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

async function toggleHistoryStats() {
    const panel = document.getElementById('history-stats-panel');
    if (!panel) return;

    historyStatsVisible = !historyStatsVisible;
    panel.style.display = historyStatsVisible ? 'block' : 'none';

    if (!historyStatsVisible) return;

    try {
        const res = await fetch('/api/history/stats');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        historyStatsData = data;
        document.getElementById('history-stats-error').textContent = '';
        renderHistoryStats(data);
    } catch (err) {
        document.getElementById('history-stats-error').textContent = historyText('stats_error', {error: err.message});
    }
}

function renderHistoryStats(data) {
    const totalEl = document.getElementById('stats-total');
    if (totalEl) totalEl.textContent = data.total_drops;

    const gameEl = document.getElementById('stats-by-game');
    if (gameEl) {
        while (gameEl.firstChild) gameEl.removeChild(gameEl.firstChild);

        const label = makeElement('div', {
            style: 'font-size:0.75rem; color:var(--text-muted,#888); margin-bottom:6px; text-transform:uppercase; letter-spacing:0.05em;'
        }, historyText('by_game'));
        gameEl.appendChild(label);

        Object.entries(data.by_game || {}).slice(0, 10).forEach(([game, count]) => {
            const row = makeElement('div', {
                style: 'display:flex; justify-content:space-between; gap:16px; padding:2px 0;'
            });
            row.appendChild(makeElement('span', {}, game));
            row.appendChild(makeElement('span', {
                style: 'font-weight:700; color:var(--accent,#7287fd)'
            }, count));
            gameEl.appendChild(row);
        });
    }

    const monthEl = document.getElementById('stats-by-month');
    if (monthEl) {
        while (monthEl.firstChild) monthEl.removeChild(monthEl.firstChild);

        const label = makeElement('div', {
            style: 'font-size:0.75rem; color:var(--text-muted,#888); margin-bottom:6px; text-transform:uppercase; letter-spacing:0.05em;'
        }, historyText('by_month'));
        monthEl.appendChild(label);

        Object.entries(data.by_month || {}).reverse().slice(0, 6).forEach(([month, count]) => {
            const row = makeElement('div', {
                style: 'display:flex; justify-content:space-between; gap:16px; padding:2px 0;'
            });
            row.appendChild(makeElement('span', {}, month));
            row.appendChild(makeElement('span', {
                style: 'font-weight:700; color:var(--accent,#7287fd)'
            }, count));
            monthEl.appendChild(row);
        });
    }
}

async function clearHistory() {
    const confirmed = window.confirm(
        historyText('clear_confirm')
    );
    if (!confirmed) return;

    try {
        const res = await fetch('/api/history', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm: true }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        historyRequestId++;
        historyAllEntries = [];
        historyTotal = 0;
        historyStatsData = {total_drops: 0, by_game: {}, by_month: {}};
        historyCurrentPage = 0;
        updateHistoryCount(0, 0);
        setHistoryTbodyMessage('cleared');
        renderHistoryPagination();

        if (historyStatsVisible) {
            const totalEl = document.getElementById('stats-total');
            if (totalEl) totalEl.textContent = '0';

            ['stats-by-game', 'stats-by-month'].forEach((id) => {
                const el = document.getElementById(id);
                if (el) while (el.firstChild) el.removeChild(el.firstChild);
            });
        }
    } catch (err) {
        alert(historyText('clear_error', {error: err.message}));
    }
}

// ==================== DOM Utilities ====================

const TRUSTED_HELP_LINKS = new Set(['https://www.twitch.tv/drops/campaigns',
    'https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks', 'https://t.me/BotFather']);

function tgHelpSetup(t) {
    const tg = (t.settings && t.settings.telegram) || (t.gui && t.gui.settings && t.gui.settings.telegram) || null;
    if (!tg) return null;
    return {
        title: tg.name || 'Telegram Notifications',
        description: tg.description || 'Receive instant notifications on Telegram when you claim drops. Setup instructions:',
        steps: tg.setup_steps || [
            'Go to @BotFather on Telegram',
            'Create a new bot with /newbot command',
            'Save the bot token you receive',
            'Start your new bot by searching for it and clicking /start (or send any message)',
            'Get your Chat ID by opening this URL in browser (replace TOKEN): https://api.telegram.org/botTOKEN/getUpdates',
            'Find your user ID in the response - it is the number in "from": {"id": YOUR_ID}',
            'Enter the token and Chat ID in Settings and click Test Connection'
        ]
    };
}

/**
 * @param {string} tag
 * @param {Record<string, string|number|boolean>} attrs
 * @param {string|number|null} text
 * @param {(el: HTMLElement) => void|null} callback
 */
function makeElement(tag, attrs = {}, text = null, callback = null) {
    const el = document.createElement(tag);
    Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, String(value)));
    if (text !== null && text !== undefined) {
        el.textContent = String(text);
    }
    if (callback) {
        callback(el);
    }
    return el;
}

function makeImageElement(src, alt, className) {
    const image = makeElement('img', { src, alt, class: className });
    image.onerror = () => {
        image.style.display = 'none';
    };
    return image;
}

function makeHelpList(tag, items) {
    return makeElement(tag, {}, null, list => {
        items.forEach(item => {
            list.appendChild(makeElement('li', {}, null, li => appendTrustedHelpContent(li, item)));
        });
    });
}

function appendTrustedHelpContent(parent, text) {
    const source = String(text);
    const linkPattern = /<a\b[^>]*\bhref=(["'])(https:\/\/support\.discord\.com\/hc\/en-us\/articles\/228383668-Intro-to-Webhooks|https:\/\/www\.twitch\.tv\/drops\/campaigns|https:\/\/t\.me\/BotFather)\1[^>]*>(.*?)<\/a>/gi;
    let lastIndex = 0;
    let match;
    let matched = false;

    while ((match = linkPattern.exec(source)) !== null) {
        matched = true;
        if (match.index > lastIndex) {
            parent.appendChild(document.createTextNode(source.slice(lastIndex, match.index)));
        }
        const href = match[2];
        if (TRUSTED_HELP_LINKS.has(href)) {
            parent.appendChild(makeElement('a', { href, target: '_blank', rel: 'noopener noreferrer' }, match[3]));
        } else {
            parent.appendChild(document.createTextNode(match[0]));
        }
        lastIndex = linkPattern.lastIndex;
    }

    if (!matched) {
        parent.textContent = source;
        return;
    }

    if (lastIndex < source.length) {
        parent.appendChild(document.createTextNode(source.slice(lastIndex)));
    }
}
