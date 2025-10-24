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
    translations: {}  // Store current translations
};

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
    const connText = state.translations.header?.connected || 'Connected';
    document.getElementById('connection-indicator').textContent = '● ' + connText;
    document.getElementById('connection-indicator').className = 'connected';
});

socket.on('disconnect', () => {
    console.log('Disconnected from server');
    state.connected = false;
    const disconnText = state.translations.header?.disconnected || 'Disconnected';
    document.getElementById('connection-indicator').textContent = '● ' + disconnText;
    document.getElementById('connection-indicator').className = 'disconnected';
});

socket.on('initial_state', (data) => {
    console.log('Received initial state', data);
    if (data.status) updateStatus(data.status);
    if (data.channels) data.channels.forEach(ch => updateChannel(ch));
    if (data.campaigns) data.campaigns.forEach(camp => addCampaign(camp));
    if (data.console) data.console.forEach(line => addConsoleLineRaw(line));
    if (data.settings) updateSettingsUI(data.settings);
    if (data.login) updateLoginStatus(data.login);
    if (data.manual_mode) updateManualModeUI(data.manual_mode);
    // Restore current drop progress if it exists
    if (data.current_drop) {
        updateDropProgress(data.current_drop);
    } else {
        clearDropProgress();
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
});

socket.on('drop_update', (data) => {
    updateDrop(data.campaign_id, data.drop);
});

socket.on('login_required', () => {
    showLoginForm();
});

socket.on('oauth_code_required', (data) => {
    showOAuthCode(data.url, data.code);
});

socket.on('login_status', (data) => {
    updateLoginStatus(data);
});

socket.on('login_clear', (data) => {
    if (data.login) document.getElementById('username').value = '';
    if (data.password) document.getElementById('password').value = '';
    if (data.token) document.getElementById('2fa-token').value = '';
});

socket.on('settings_updated', (data) => {
    updateSettingsUI(data);
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

function renderChannels() {
    const container = document.getElementById('channels-list');
    container.innerHTML = '';

    const t = state.translations;
    const channels = Object.values(state.channels);
    if (channels.length === 0) {
        const emptyMsg = t.channels?.no_channels || 'No channels tracked yet...';
        container.innerHTML = `<p class="empty-message">${emptyMsg}</p>`;
        return;
    }

    // Get the games to watch list from settings
    const gamesToWatch = state.settings.games_to_watch || [];
    const gamesToWatchSet = new Set(gamesToWatch);

    // Filter channels to only include those playing games in the watch list
    const filteredChannels = channels.filter(channel => {
        const gameName = channel.game;
        // Include channels if: they have a game AND it's in the watch list
        // OR if the watch list is empty (show all)
        return gamesToWatch.length === 0 || (gameName && gamesToWatchSet.has(gameName));
    });

    if (filteredChannels.length === 0) {
        const emptyMsg = t.channels?.no_channels_for_games || 'No channels found for selected games...';
        container.innerHTML = `<p class="empty-message">${emptyMsg}</p>`;
        return;
    }

    // Group channels by game
    const gameGroups = {};
    filteredChannels.forEach(channel => {
        const gameName = channel.game || 'No Game';
        const gameId = channel.game_id || 'no-game';
        const gameIcon = channel.game_icon;

        if (!gameGroups[gameId]) {
            gameGroups[gameId] = {
                name: gameName,
                icon: gameIcon,
                channels: []
            };
        }
        gameGroups[gameId].channels.push(channel);
    });

    // Sort games: prioritize games with watching channels, then by total viewers
    const sortedGames = Object.entries(gameGroups).sort(([idA, groupA], [idB, groupB]) => {
        const hasWatchingA = groupA.channels.some(ch => ch.watching);
        const hasWatchingB = groupB.channels.some(ch => ch.watching);

        if (hasWatchingA !== hasWatchingB) return hasWatchingB ? 1 : -1;

        // Sum total viewers for each game
        const totalViewersA = groupA.channels.reduce((sum, ch) => sum + (ch.viewers || 0), 0);
        const totalViewersB = groupB.channels.reduce((sum, ch) => sum + (ch.viewers || 0), 0);

        return totalViewersB - totalViewersA;
    });

    // Render each game group
    sortedGames.forEach(([gameId, group]) => {
        // Create game header
        const gameHeader = document.createElement('div');
        gameHeader.className = 'game-group-header';

        let iconHtml = '';
        if (group.icon) {
            // Resize the box art to 40x53 (Twitch's standard small size)
            const iconUrl = group.icon.replace('{width}', '40').replace('{height}', '53');
            iconHtml = `<img src="${iconUrl}" alt="${group.name}" class="game-icon" onerror="this.style.display='none'">`;
        }

        const channelCount = group.channels.length;
        const totalViewers = group.channels.reduce((sum, ch) => sum + (ch.viewers || 0), 0);

        const channelText = channelCount === 1
            ? (t.channels?.channel_count || 'channel')
            : (t.channels?.channel_count_plural || 'channels');
        const viewersText = t.channels?.viewers || 'viewers';

        gameHeader.innerHTML = `
            ${iconHtml}
            <div class="game-group-info">
                <div class="game-group-name">${group.name}</div>
                <div class="game-group-stats">${channelCount} ${channelText} • ${totalViewers.toLocaleString()} ${viewersText}</div>
            </div>
        `;

        container.appendChild(gameHeader);

        // Sort channels within game: watching first, then online, then by viewers
        group.channels.sort((a, b) => {
            if (a.watching !== b.watching) return b.watching ? 1 : -1;
            if (a.online !== b.online) return b.online ? 1 : -1;
            return (b.viewers || 0) - (a.viewers || 0);
        });

        // Render channels in this game
        group.channels.forEach(channel => {
            const div = document.createElement('div');
            div.className = 'channel-item';
            if (channel.watching) div.classList.add('watching');
            if (channel.online) div.classList.add('online');
            else div.classList.add('offline');

            let badges = '';
            if (channel.drops_enabled) badges += '<span class="channel-badge drops">DROPS</span>';
            if (channel.acl_based) badges += '<span class="channel-badge acl">ACL</span>';

            div.innerHTML = `
                <div class="channel-name">${channel.name} ${badges}</div>
                <div class="channel-info">
                    ${channel.viewers !== null ? channel.viewers.toLocaleString() + ' viewers' : 'Offline'}
                    ${channel.watching ? ' • <strong>WATCHING</strong>' : ''}
                </div>
            `;

            div.onclick = () => selectChannel(channel.id);
            container.appendChild(div);
        });
    });
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

    // Make campaign name clickable with link to Twitch
    const dropGameEl = document.getElementById('drop-game');
    if (data.campaign_id) {
        const campaignUrl = `https://www.twitch.tv/drops/campaigns?dropID=${data.campaign_id}`;
        dropGameEl.innerHTML = `<a href="${campaignUrl}" target="_blank" rel="noopener noreferrer" class="drop-campaign-link">${data.campaign_name}</a> (${data.game_name})`;
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
}

function clearInventory() {
    state.campaigns = {};
    renderInventory();
}

function updateDrop(campaignId, dropData) {
    if (state.campaigns[campaignId]) {
        const drops = state.campaigns[campaignId].drops;
        const index = drops.findIndex(d => d.id === dropData.id);
        if (index !== -1) {
            drops[index] = dropData;
            renderInventory();
        }
    }
}

function renderInventory() {
    const container = document.getElementById('inventory-grid');
    container.innerHTML = '';

    const t = state.translations;
    const campaigns = Object.values(state.campaigns);
    if (campaigns.length === 0) {
        const emptyMsg = t.inventory?.no_campaigns || 'No campaigns loaded yet...';
        container.innerHTML = `<p class="empty-message">${emptyMsg}</p>`;
        return;
    }

    campaigns.forEach(campaign => {
        const card = document.createElement('div');
        card.className = 'campaign-card';

        let statusClass = '';
        let statusText = '';
        if (campaign.active) {
            statusClass = 'active';
            statusText = t.inventory?.status?.active || 'Active';
        } else if (campaign.upcoming) {
            statusClass = 'upcoming';
            statusText = t.inventory?.status?.upcoming || 'Upcoming';
        } else if (campaign.expired) {
            statusClass = 'expired';
            statusText = t.inventory?.status?.expired || 'Expired';
        }

        const claimedText = t.inventory?.status?.claimed || 'Claimed';
        const dropsHtml = campaign.drops.map(drop => `
            <div class="drop-item ${drop.is_claimed ? 'claimed' : ''} ${drop.can_claim ? 'active' : ''}">
                <div><strong>${drop.name}</strong></div>
                <div>${drop.rewards}</div>
                <div>${drop.current_minutes} / ${drop.required_minutes} minutes (${Math.round(drop.progress * 100)}%)</div>
                ${drop.is_claimed ? `<div>✓ ${claimedText}</div>` : ''}
            </div>
        `).join('');

        // Make campaign name clickable if link_url is available
        const campaignNameHtml = campaign.link_url
            ? `<a href="${campaign.link_url}" target="_blank" rel="noopener noreferrer" class="campaign-name-link">${campaign.name} <span class="external-link-icon">🔗</span></a>`
            : `<div class="campaign-name">${campaign.name}</div>`;

        const claimedCountText = t.inventory?.claimed_drops || 'claimed';
        card.innerHTML = `
            <div class="campaign-header">
                <div class="campaign-game">${campaign.game_name}</div>
                ${campaignNameHtml}
            </div>
            <div class="campaign-status">
                <span>${statusText}</span>
                <span>${campaign.claimed_drops} / ${campaign.total_drops} ${claimedCountText}</span>
            </div>
            <div class="campaign-drops">
                ${dropsHtml}
            </div>
        `;

        container.appendChild(card);
    });
}

function showLoginForm() {
    document.getElementById('login-form').style.display = 'block';
    document.getElementById('oauth-code-display').style.display = 'none';
}

function showOAuthCode(url, code) {
    document.getElementById('login-form').style.display = 'none';
    document.getElementById('oauth-code-display').style.display = 'block';
    document.getElementById('oauth-url').href = url;
    document.getElementById('oauth-code').textContent = code;
}

function updateLoginStatus(data) {
    const statusEl = document.getElementById('login-status');
    const t = state.translations;
    if (data.user_id) {
        const userIdLabel = t.gui?.login?.user_id_label || 'User ID:';
        statusEl.textContent = `${data.status} (${userIdLabel} ${data.user_id})`;
        statusEl.removeAttribute('translation-key');
        statusEl.style.color = 'var(--success-color)';
        document.getElementById('login-form').style.display = 'none';
        document.getElementById('oauth-code-display').style.display = 'none';
    } else {
        const loggedOut = t.gui?.login?.logged_out || 'Not logged in';
        statusEl.textContent = data.status || loggedOut;
        statusEl.setAttribute('translation-key', 'logged_out');
        statusEl.style.color = 'var(--text-secondary)';
        // Check if OAuth is pending (for late-connecting clients)
        if (data.oauth_pending) {
            showOAuthCode(data.oauth_pending.url, data.oauth_pending.code);
        }
    }
}

function updateSettingsUI(settings) {
    state.settings = settings;
    document.getElementById('dark-mode').checked = settings.dark_mode || false;
    document.getElementById('connection-quality').value = settings.connection_quality || 1;
    document.getElementById('minimum-refresh-interval').value = settings.minimum_refresh_interval_minutes || 30;

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

    // Update games to watch lists
    renderGamesToWatch();

    // Re-render channels list to apply filter based on updated games to watch
    renderChannels();
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

// ==================== Games to Watch Management ====================

let availableGames = new Set(); // All games from campaigns
let draggedElement = null;

socket.on('games_available', (data) => {
    availableGames = new Set(data.games || []);
    renderGamesToWatch();
});

function renderGamesToWatch() {
    const selectedGames = state.settings.games_to_watch || [];
    const filterText = document.getElementById('games-filter')?.value.toLowerCase() || '';

    // Render selected games (sortable)
    renderSelectedGames(selectedGames);

    // Render available games (checkboxes for unselected games)
    const unselectedGames = Array.from(availableGames)
        .filter(game => !selectedGames.includes(game))
        .filter(game => game.toLowerCase().includes(filterText))
        .sort();

    renderAvailableGames(unselectedGames, filterText);
}

function renderSelectedGames(games) {
    const container = document.getElementById('selected-games-list');
    if (!container) return;

    const t = state.translations;
    container.innerHTML = '';

    if (games.length === 0) {
        const emptyMsg = t.settings?.no_games_selected || 'No games selected. Check games below to add them.';
        container.innerHTML = `<p class="empty-message">${emptyMsg}</p>`;
        return;
    }

    games.forEach((game, index) => {
        const div = document.createElement('div');
        div.className = 'sortable-item';
        div.draggable = true;
        div.dataset.game = game;
        div.innerHTML = `
            <span class="drag-handle">☰</span>
            <span class="priority-number">${index + 1}</span>
            <span class="game-name">${game}</span>
            <button class="remove-btn" onclick="removeGameFromWatch('${game}')">✕</button>
        `;

        // Drag event handlers
        div.addEventListener('dragstart', handleDragStart);
        div.addEventListener('dragover', handleDragOver);
        div.addEventListener('drop', handleDrop);
        div.addEventListener('dragend', handleDragEnd);

        container.appendChild(div);
    });
}

function renderAvailableGames(games, filterText) {
    const container = document.getElementById('available-games-list');
    if (!container) return;

    const t = state.translations;
    container.innerHTML = '';

    if (games.length === 0) {
        if (filterText) {
            const emptyMsg = t.settings?.no_games_match || 'No games match your search.';
            container.innerHTML = `<p class="empty-message">${emptyMsg}</p>`;
        } else {
            const emptyMsg = t.settings?.all_games_selected || 'All games are selected or no games available.';
            container.innerHTML = `<p class="empty-message">${emptyMsg}</p>`;
        }
        return;
    }

    games.forEach(game => {
        const label = document.createElement('label');
        label.className = 'game-checkbox';
        label.innerHTML = `
            <input type="checkbox" value="${game}" onchange="toggleGameWatch('${game}', this.checked)">
            <span>${game}</span>
        `;
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
    if (target && target !== draggedElement) {
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
    state.settings.games_to_watch = Array.from(availableGames).sort();
    renderGamesToWatch();
    renderChannels();
    saveSettings();
}

function deselectAllGames() {
    state.settings.games_to_watch = [];
    renderGamesToWatch();
    renderChannels();
    saveSettings();
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

async function submitLogin() {
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    const token = document.getElementById('2fa-token').value;

    try {
        await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password, token })
        });
    } catch (error) {
        console.error('Failed to submit login:', error);
    }
}

async function confirmOAuth() {
    // Signal that OAuth code has been entered
    try {
        await fetch('/api/oauth/confirm', {
            method: 'POST'
        });
        // Hide the OAuth form and show waiting message
        document.getElementById('oauth-code-display').style.display = 'none';
        const t = state.translations;
        const waitingAuth = t.gui?.login?.waiting_auth || 'Waiting for authentication...';
        const loginStatus = document.getElementById('login-status');
        loginStatus.textContent = waitingAuth;
        loginStatus.setAttribute('translation-key', 'waiting_auth');
    } catch (error) {
        console.error('Failed to confirm OAuth:', error);
    }
}

async function saveSettings() {
    const settings = {
        dark_mode: document.getElementById('dark-mode').checked,
        language: document.getElementById('language').value,
        connection_quality: parseInt(document.getElementById('connection-quality').value),
        minimum_refresh_interval_minutes: parseInt(document.getElementById('minimum-refresh-interval').value),
        games_to_watch: state.settings.games_to_watch || []
    };

    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        console.log('Settings saved automatically');
    } catch (error) {
        console.error('Failed to save settings:', error);
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
            languageSelect.innerHTML = '<option value="">Failed to load languages</option>';
        }
        addConsoleLine('Error: Unable to fetch available languages. Please check your connection or try again later.');
    }
}

async function fetchAndApplyTranslations() {
    try {
        const response = await fetch('/api/translations');
        const data = await response.json();

        state.translations = data.translations;
        applyTranslations(data.translations);
        console.log('Translations applied for language:', data.language);
    } catch (error) {
        console.error('Failed to fetch translations:', error);
    }
}

function applyTranslations(t) {
    // Update tab buttons
    const tabButtons = {
        'main': document.querySelector('[data-tab="main"]'),
        'inventory': document.querySelector('[data-tab="inventory"]'),
        'settings': document.querySelector('[data-tab="settings"]'),
        'help': document.querySelector('[data-tab="help"]')
    };

    if (tabButtons.main && t.tabs) tabButtons.main.textContent = t.tabs.main;
    if (tabButtons.inventory && t.tabs) tabButtons.inventory.textContent = t.tabs.inventory;
    if (tabButtons.settings && t.tabs) tabButtons.settings.textContent = t.tabs.settings;
    if (tabButtons.help && t.tabs) tabButtons.help.textContent = t.tabs.help;

    // Update Main tab - Login section
    const mainTab = document.getElementById('main-tab');
    if (mainTab && t.login) {
        const loginHeader = mainTab.querySelector('.login-panel h2');
        if (loginHeader) loginHeader.textContent = t.login.title;

        const loginStatus = document.getElementById('login-status');
        if (loginStatus?.hasAttribute('translation-key')) loginStatus.textContent = t.login.status[loginStatus.getAttribute('translation-key')];

        // Update login form placeholders
        const usernameInput = document.getElementById('username');
        if (usernameInput) usernameInput.placeholder = t.login.username;

        const passwordInput = document.getElementById('password');
        if (passwordInput) passwordInput.placeholder = t.login.password;

        const twofaInput = document.getElementById('2fa-token');
        if (twofaInput) twofaInput.placeholder = t.login.twofa_code;

        const loginButton = document.getElementById('login-button');
        if (loginButton) loginButton.textContent = t.login.button;

        // Update OAuth display text
        const oauthDisplay = document.getElementById('oauth-code-display');
        if (oauthDisplay) {
            const oauthP = oauthDisplay.querySelector('p');
            if (oauthP) {
                const link = oauthP.querySelector('a');
                if (link) {
                    oauthP.textContent = t.login.oauth_prompt + ' ';
                    link.textContent = t.login.oauth_activate;
                    oauthP.appendChild(link);
                }
            }

            const oauthConfirmBtn = document.getElementById('oauth-confirm');
            if (oauthConfirmBtn) oauthConfirmBtn.textContent = t.login.oauth_confirm;
        }
    }

    // Update Progress section
    if (mainTab && t.progress) {
        const progressHeader = mainTab.querySelector('.progress-panel h2');
        if (progressHeader) progressHeader.textContent = t.progress.title;

        const noDropMsg = document.getElementById('no-drop-message');
        if (noDropMsg) noDropMsg.textContent = t.progress.no_drop;

        const exitManualBtn = document.getElementById('exit-manual-btn');
        if (exitManualBtn) exitManualBtn.textContent = t.progress.return_to_auto;
    }

    // Update Console section
    if (mainTab && t.console) {
        const consoleHeader = mainTab.querySelector('.console-panel h2');
        if (consoleHeader) consoleHeader.textContent = t.console.title;
    }

    // Update Channels section
    if (mainTab && t.channels) {
        const channelsHeader = mainTab.querySelector('.channels-panel h2');
        if (channelsHeader) channelsHeader.textContent = t.channels.title;
        // Channel list will re-render with translated empty messages
        renderChannels();
    }

    // Update Inventory tab
    const inventoryTab = document.getElementById('inventory-tab');
    if (inventoryTab && t.inventory) {
        // Inventory will re-render with translated status and empty messages
        renderInventory();
    }

    // Update Settings tab
    const settingsTab = document.getElementById('settings-tab');
    if (settingsTab && t.settings) {
        const headers = settingsTab.querySelectorAll('h2');
        if (headers[0]) headers[0].textContent = t.settings.general;
        if (headers[1]) headers[1].textContent = t.settings.games_to_watch;
        if (headers[2]) headers[2].textContent = t.settings.actions;

        const darkModeLabel = settingsTab.querySelector('label:has(#dark-mode)');
        if (darkModeLabel) {
            const checkbox = darkModeLabel.querySelector('input');
            darkModeLabel.textContent = '';
            darkModeLabel.appendChild(checkbox);
            darkModeLabel.appendChild(document.createTextNode(' ' + t.settings.dark_mode));
        }

        const connQualityLabel = settingsTab.querySelector('label:has(#connection-quality)');
        if (connQualityLabel) {
            const input = connQualityLabel.querySelector('input');
            connQualityLabel.textContent = t.settings.connection_quality + ' ';
            connQualityLabel.appendChild(input);
        }

        const refreshLabel = settingsTab.querySelector('label:has(#minimum-refresh-interval)');
        if (refreshLabel) {
            const input = refreshLabel.querySelector('input');
            refreshLabel.textContent = t.settings.minimum_refresh + ' ';
            refreshLabel.appendChild(input);
        }

        const helpText = settingsTab.querySelector('.help-text');
        if (helpText) helpText.textContent = t.settings.games_help;

        const searchInput = document.getElementById('games-filter');
        if (searchInput) searchInput.placeholder = t.settings.search_games;

        const selectAllBtn = document.getElementById('select-all-btn');
        if (selectAllBtn) selectAllBtn.textContent = t.settings.select_all;

        const deselectAllBtn = document.getElementById('deselect-all-btn');
        if (deselectAllBtn) deselectAllBtn.textContent = t.settings.deselect_all;

        const selectedGamesHeader = settingsTab.querySelector('.selected-games h3');
        if (selectedGamesHeader) selectedGamesHeader.textContent = t.settings.selected_games;

        const availableGamesHeader = settingsTab.querySelector('.available-games h3');
        if (availableGamesHeader) availableGamesHeader.textContent = t.settings.available_games;

        const reloadBtn = document.getElementById('reload-btn');
        if (reloadBtn) reloadBtn.textContent = t.settings.reload_campaigns;

        // Re-render games to watch with translated empty messages
        renderGamesToWatch();
    }

    // Update Help tab
    const helpTab = document.getElementById('help-tab');
    if (helpTab && t.help) {
        const helpContent = helpTab.querySelector('.help-content');
        if (helpContent) {
            // Rebuild help content dynamically
            helpContent.innerHTML = `
                <h2>${t.help.about || 'About Twitch Drops Miner'}</h2>
                <p>${t.help.about_text || 'This application automatically mines timed Twitch drops without downloading stream data.'}</p>

                <h3>${t.help.how_to_use || 'How to Use'}</h3>
                <ol>
                    ${(t.help.how_to_use_items || [
                    'Login using your Twitch account (OAuth device code flow)',
                    'Link your accounts at <a href="https://www.twitch.tv/drops/campaigns" target="_blank">twitch.tv/drops/campaigns</a>',
                    'The miner will automatically discover campaigns and start mining',
                    'Configure priority games in Settings to focus on what you want',
                    'Monitor progress in the Main and Inventory tabs'
                ]).map(item => `<li>${item}</li>`).join('')}
                </ol>

                <h3>${t.help.features || 'Features'}</h3>
                <ul>
                    ${(t.help.features_items || [
                    'Stream-less drop mining - saves bandwidth',
                    'Game priority and exclusion lists',
                    'Tracks up to 199 channels simultaneously',
                    'Automatic channel switching',
                    'Real-time progress tracking'
                ]).map(item => `<li>${item}</li>`).join('')}
                </ul>

                <h3>${t.help.important_notes || 'Important Notes'}</h3>
                <ul>
                    ${(t.help.important_notes_items || [
                    'Do not watch streams on the same account while mining',
                    'Keep your cookies.jar file secure',
                    'Requires linked game accounts for drops'
                ]).map(item => `<li>${item}</li>`).join('')}
                </ul>

                <div class="help-links">
                    <a href="https://github.com/DevilXD/TwitchDropsMiner" target="_blank">${t.help.github_repo || 'GitHub Repository'}</a>
                </div>
            `;
        }
    }

    // Update header elements
    if (t.header) {
        const languageLabel = document.querySelector('.language-selector span');
        if (languageLabel) languageLabel.textContent = t.header.language;

        const statusText = document.getElementById('status-text');
        if (statusText && statusText.textContent === 'Initializing...') {
            statusText.textContent = t.header.initializing;
        }

        // Update connection indicator
        const connIndicator = document.getElementById('connection-indicator');
        if (connIndicator) {
            if (state.connected) {
                connIndicator.textContent = '● ' + (t.header.connected || 'Connected');
            } else {
                connIndicator.textContent = '● ' + (t.header.disconnected || 'Disconnected');
            }
        }
    }
}

async function reloadCampaigns() {
    try {
        await fetch('/api/reload', { method: 'POST' });
        // Status will update via Socket.IO when backend starts operation
    } catch (error) {
        console.error('Failed to reload:', error);
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
}

// ==================== Event Listeners ====================

document.addEventListener('DOMContentLoaded', () => {
    // Tab switching
    document.querySelectorAll('.tab-button').forEach(button => {
        button.addEventListener('click', () => {
            switchTab(button.dataset.tab);
        });
    });

    // Login form
    document.getElementById('login-button').addEventListener('click', submitLogin);
    document.getElementById('oauth-confirm').addEventListener('click', confirmOAuth);

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
    document.getElementById('language').addEventListener('change', saveSettings);
    document.getElementById('connection-quality').addEventListener('change', saveSettings);
    document.getElementById('minimum-refresh-interval').addEventListener('change', saveSettings);
    document.getElementById('reload-btn').addEventListener('click', reloadCampaigns);

    // Games to watch management
    document.getElementById('select-all-btn').addEventListener('click', selectAllGames);
    document.getElementById('deselect-all-btn').addEventListener('click', deselectAllGames);
    document.getElementById('games-filter').addEventListener('input', renderGamesToWatch);

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
