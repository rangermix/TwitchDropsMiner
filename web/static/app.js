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

                // Log to console
                console.log(`Update available: ${data.latest_version} (current: ${data.current_version})`);
            }
        }
    } catch (error) {
        console.warn('Could not fetch version information:', error);
        // Set placeholder text if fetch fails
        const versionElement = document.getElementById('current-version');
        if (versionElement && versionElement.textContent === 'Loading...') {
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

    if (data.settings) updateSettingsUI(data.settings);
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

function renderChannels() {
    const container = document.getElementById('channels-list');
    container.innerHTML = '';

    const t = state.translations;
    const channels = Object.values(state.channels);
    if (channels.length === 0) {
        const emptyMsg = t.gui?.channels?.no_channels || 'No channels tracked yet...';
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
        const emptyMsg = t.gui?.channels?.no_channels_for_games || 'No channels found for selected games...';
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
            ? (t.gui?.channels?.channel_count || 'channel')
            : (t.gui?.channels?.channel_count_plural || 'channels');
        const viewersText = t.gui?.channels?.viewers || 'viewers';

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

// ==================== Inventory Filtering ====================

function getInventoryFilters() {
    // Get filter state from UI checkboxes and selected games array
    return {
        show_active: document.getElementById('filter-active')?.checked || false,
        show_not_linked: document.getElementById('filter-not-linked')?.checked || false,
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
    // Calculate "finished" status: all drops claimed
    const isFinished = campaign.total_drops > 0 && campaign.claimed_drops === campaign.total_drops;

    // Check if any filter is enabled
    const hasGameFilter = filters.game_name_search && filters.game_name_search.length > 0;
    const anyFilterEnabled = filters.show_active || filters.show_not_linked ||
        filters.show_upcoming || filters.show_expired ||
        filters.show_finished || hasGameFilter;

    // If no filters enabled, show all campaigns
    if (!anyFilterEnabled) {
        return true;
    }

    // Check status filters (OR logic - campaign matches if ANY checked filter applies)
    let statusMatch = false;

    if (filters.show_active && campaign.active) statusMatch = true;
    if (filters.show_not_linked && !campaign.linked) statusMatch = true;
    if (filters.show_upcoming && campaign.upcoming) statusMatch = true;
    if (filters.show_expired && campaign.expired) statusMatch = true;
    if (filters.show_finished && isFinished) statusMatch = true;

    // If status filters are enabled but campaign doesn't match any, filter it out
    const hasStatusFilters = filters.show_active || filters.show_not_linked ||
        filters.show_upcoming || filters.show_expired ||
        filters.show_finished;
    if (hasStatusFilters && !statusMatch) {
        return false;
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
    document.getElementById('filter-not-linked').checked = false;
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
        dropdown.innerHTML = '<div class="dropdown-item no-results">No games found</div>';
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
        removeBtn.innerHTML = '×';
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

function renderInventory() {
    const container = document.getElementById('inventory-grid');
    container.innerHTML = '';

    const t = state.translations;
    const allCampaigns = Object.values(state.campaigns);

    // Apply filters
    const filters = getInventoryFilters();
    const campaigns = allCampaigns.filter(campaign => campaignMatchesFilters(campaign, filters));

    if (allCampaigns.length === 0) {
        const emptyMsg = t.gui?.inventory?.no_campaigns || 'No campaigns loaded yet...';
        container.innerHTML = `<p class="empty-message">${emptyMsg}</p>`;
        return;
    }

    if (campaigns.length === 0) {
        container.innerHTML = `<p class="empty-message">No campaigns match the current filters.</p>`;
        return;
    }

    campaigns.forEach(campaign => {
        const card = document.createElement('div');
        card.className = 'campaign-card';

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
        const dropsHtml = campaign.drops.map(drop => {
            // Generate HTML for each benefit as its own line
            let benefitsHtml = '';
            if (drop.benefits && drop.benefits.length > 0) {
                benefitsHtml = drop.benefits.map(benefit =>
                    `<div class="benefit-item">
                        <img src="${benefit.image_url}" alt="${benefit.name}" class="benefit-icon" onerror="this.style.display='none'">
                        <div class="benefit-info">
                            <span class="benefit-name">${benefit.name}</span>
                            <span class="benefit-type">(${benefit.type})</span>
                        </div>
                    </div>`
                ).join('');
            }

            return `
                <div class="drop-item ${drop.is_claimed ? 'claimed' : ''} ${drop.can_claim ? 'active' : ''}">
                    <div class="drop-item-header">
                        <div class="drop-item-info">
                            <div><strong>${drop.name}</strong></div>
                        </div>
                    </div>
                    <div class="benefits-list">
                        ${benefitsHtml}
                    </div>
                    <div>${drop.current_minutes} / ${drop.required_minutes} minutes (${Math.round(drop.progress * 100)}%)</div>
                    ${drop.is_claimed ? `<div>✓ ${claimedText}</div>` : ''}
                </div>
            `;
        }).join('');

        const campaignNameHtml = `<a href="${campaign.campaign_url}" target="_blank" rel="noopener noreferrer" class="campaign-name-link">${campaign.name} <span class="external-link-icon">🔗</span></a>`

        // Add LINKED or NOT LINKED badge
        const linkStatusBadgeHtml = campaign.linked
            ? `<span class="campaign-badge linked" title="Account is linked">LINKED</span>`
            : `<span class="campaign-badge not-linked" onclick="window.open('${campaign.link_url}', '_blank')" title="Click to link your account">NOT LINKED</span>`;

        const linkAccountButtonHtml = !campaign.linked && campaign.link_url
            ? `<button class="link-account-btn" onclick="window.open('${campaign.link_url}', '_blank')">Link Account</button>`
            : '';

        // Add game icon if available
        let gameIconHtml = '';
        if (campaign.game_box_art_url) {
            const iconUrl = campaign.game_box_art_url.replace('{width}', '52').replace('{height}', '70');
            gameIconHtml = `<img src="${iconUrl}" alt="${campaign.game_name}" class="game-icon" onerror="this.style.display='none'">`;
        }

        // Format campaign timing based on status
        let timingHtml = '';
        if (campaign.active && campaign.ends_at) {
            const endDate = new Date(campaign.ends_at);
            const formattedDate = endDate.toLocaleString();
            const endsLabel = t.gui?.inventory?.ends || 'Ends: {time}';
            timingHtml = `<div class="campaign-timing">${endsLabel.replace('{time}', formattedDate)}</div>`;
        } else if (campaign.upcoming && campaign.starts_at) {
            const startDate = new Date(campaign.starts_at);
            const formattedDate = startDate.toLocaleString();
            const startsLabel = t.gui?.inventory?.starts || 'Starts: {time}';
            timingHtml = `<div class="campaign-timing">${startsLabel.replace('{time}', formattedDate)}</div>`;
        } else if (campaign.expired && campaign.ends_at) {
            const endDate = new Date(campaign.ends_at);
            const formattedDate = endDate.toLocaleString();
            const endsLabel = t.gui?.inventory?.ends || 'Ends: {time}';
            timingHtml = `<div class="campaign-timing">${endsLabel.replace('{time}', formattedDate)}</div>`;
        }

        const claimedCountText = t.gui?.inventory?.claimed_drops || 'claimed';
        card.innerHTML = `
            <div class="campaign-header">
                <div class="campaign-game">
                    ${gameIconHtml}
                    <span class="campaign-game-name">${campaign.game_name}</span>
                    ${linkStatusBadgeHtml}
                </div>
                ${campaignNameHtml}
                ${linkAccountButtonHtml}
            </div>
            <div class="campaign-status">
                <span>${statusText}</span>
                <span>${campaign.claimed_drops} / ${campaign.total_drops} ${claimedCountText}</span>
            </div>
            ${timingHtml}
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

    // Update proxy settings and indicator
    const proxyUrl = settings.proxy || '';
    const proxyInput = document.getElementById('proxy-url');
    if (proxyInput) proxyInput.value = proxyUrl;

    const proxyIndicator = document.getElementById('proxy-indicator');
    if (proxyIndicator) {
        proxyIndicator.style.display = proxyUrl ? 'inline-flex' : 'none';
        proxyIndicator.title = proxyUrl ? `Proxy active: ${proxyUrl}` : 'Proxy disabled';
    }

    // Populate Telegram fields if present in settings
    const botTokenInput = document.getElementById('telegram-bot-token');
    const chatIdInput = document.getElementById('telegram-chat-id');
    if (botTokenInput) botTokenInput.value = settings.telegram_bot_token || '';
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
        document.getElementById('filter-not-linked').checked = settings.inventory_filters.show_not_linked || false;
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

    // Restore mining benefit filters
    if (settings.mining_benefits) {
        if (document.getElementById('mining-benefit-item')) document.getElementById('mining-benefit-item').checked = settings.mining_benefits.DIRECT_ENTITLEMENT;
        if (document.getElementById('mining-benefit-badge')) document.getElementById('mining-benefit-badge').checked = settings.mining_benefits.BADGE;
        if (document.getElementById('mining-benefit-emote')) document.getElementById('mining-benefit-emote').checked = settings.mining_benefits.EMOTE;
        if (document.getElementById('mining-benefit-unknown')) document.getElementById('mining-benefit-unknown').checked = settings.mining_benefits.UNKNOWN;
    }


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
        const emptyMsg = t.gui?.settings?.no_games_selected || 'No games selected. Check games below to add them.';
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
            <button class="remove-btn">✕</button>
        `;

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

function renderAvailableGames(games, filterText) {
    const container = document.getElementById('available-games-list');
    if (!container) return;

    const t = state.translations;
    container.innerHTML = '';

    if (games.length === 0) {
        if (filterText) {
            const emptyMsg = t.gui?.settings?.no_games_match || 'No games match your search.';
            container.innerHTML = `<p class="empty-message">${emptyMsg}</p>`;
        } else {
            const emptyMsg = t.gui?.settings?.all_games_selected || 'All games are selected or no games available.';
            container.innerHTML = `<p class="empty-message">${emptyMsg}</p>`;
        }
        return;
    }

    games.forEach(game => {
        const label = document.createElement('label');
        label.className = 'game-checkbox';
        label.innerHTML = `
            <input type="checkbox" value="${game}">
            <span>${game}</span>
        `;

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

async function testTelegramConnection() {
    const botTokenInput = document.getElementById('telegram-bot-token');
    const chatIdInput = document.getElementById('telegram-chat-id');
    const resultDiv = document.getElementById('telegram-test-result');

    if (!resultDiv) return;

    const botToken = botTokenInput ? botTokenInput.value.trim() : '';
    const chatId = chatIdInput ? chatIdInput.value.trim() : '';

    // Reset display
    resultDiv.style.display = 'block';
    resultDiv.className = 'verify-result loading';
    resultDiv.textContent = 'Testing Telegram connection...';

    if (!botToken || !chatId) {
        resultDiv.className = 'verify-result error';
        resultDiv.textContent = 'Please fill in both Bot Token and Chat ID. See setup instructions in the Help tab.';
        return;
    }

    try {
        const response = await fetch('/api/settings/test-telegram', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ telegram_bot_token: botToken, telegram_chat_id: chatId })
        });

        const data = await response.json();

        if (data.success) {
            resultDiv.className = 'verify-result success';
            resultDiv.textContent = `✓ ${data.message || 'Telegram connection successful!'}`;
            // Save settings if test was successful
            saveTelegramSettings(botToken, chatId);
        } else {
            resultDiv.className = 'verify-result error';
            resultDiv.textContent = `✗ ${data.message || 'Telegram connection failed.'}`;
        }
    } catch (error) {
        resultDiv.className = 'verify-result error';
        resultDiv.textContent = `Error: ${error.message}`;
    }
}

async function saveTelegramSettings(botToken, chatId) {
    const settings = {
        telegram_bot_token: botToken,
        telegram_chat_id: chatId
    };

    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        console.log('Telegram settings saved');
    } catch (error) {
        console.error('Failed to save Telegram settings:', error);
    }
}

async function handleSaveTelegramClick() {
    const botTokenInput = document.getElementById('telegram-bot-token');
    const chatIdInput = document.getElementById('telegram-chat-id');
    const resultDiv = document.getElementById('telegram-test-result');

    if (!resultDiv) return;

    const botToken = botTokenInput ? botTokenInput.value.trim() : '';
    const chatId = chatIdInput ? chatIdInput.value.trim() : '';

    // Reset display
    resultDiv.style.display = 'block';
    resultDiv.className = 'verify-result loading';
    resultDiv.textContent = 'Saving settings...';

    if (!botToken || !chatId) {
        resultDiv.className = 'verify-result error';
        resultDiv.textContent = 'Please fill in both Bot Token and Chat ID. See setup instructions in the Help tab.';
        return;
    }

    try {
        await saveTelegramSettings(botToken, chatId);
        resultDiv.className = 'verify-result success';
        resultDiv.textContent = '✓ Settings saved successfully!';
    } catch (error) {
        resultDiv.className = 'verify-result error';
        resultDiv.textContent = `Error: ${error.message}`;
    }
}


async function saveSettings() {
    const settings = {
        dark_mode: document.getElementById('dark-mode').checked,
        language: document.getElementById('language').value,
        connection_quality: parseInt(document.getElementById('connection-quality').value),
        minimum_refresh_interval_minutes: parseInt(document.getElementById('minimum-refresh-interval').value),
        proxy: state.settings.proxy || '',
        games_to_watch: state.settings.games_to_watch || [],
        inventory_filters: getInventoryFilters(),
        mining_benefits: {
            "DIRECT_ENTITLEMENT": document.getElementById('mining-benefit-item')?.checked,
            "BADGE": document.getElementById('mining-benefit-badge')?.checked,
            "EMOTE": document.getElementById('mining-benefit-emote')?.checked,
            "UNKNOWN": document.getElementById('mining-benefit-unknown')?.checked
        }
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

        state.translations = data;
        applyTranslations(data);
        console.log('Translations applied for language:', data.language_name);
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

    if (tabButtons.main && t.gui?.tabs) tabButtons.main.textContent = t.gui.tabs.main;
    if (tabButtons.inventory && t.gui?.tabs) tabButtons.inventory.textContent = t.gui.tabs.inventory;
    if (tabButtons.settings && t.gui?.tabs) tabButtons.settings.textContent = t.gui.tabs.settings;
    if (tabButtons.help && t.gui?.tabs) tabButtons.help.textContent = t.gui.tabs.help;

    // Update Main tab - Login section
    const mainTab = document.getElementById('main-tab');
    if (mainTab && t.gui?.login) {
        const loginHeader = mainTab.querySelector('.login-panel h2');
        if (loginHeader) loginHeader.textContent = t.gui.login.name;

        const loginStatus = document.getElementById('login-status');
        if (loginStatus?.hasAttribute('translation-key')) loginStatus.textContent = t.login?.status?.[loginStatus.getAttribute('translation-key')];

        // Update login form placeholders
        const usernameInput = document.getElementById('username');
        if (usernameInput) usernameInput.placeholder = t.gui.login.username;

        const passwordInput = document.getElementById('password');
        if (passwordInput) passwordInput.placeholder = t.gui.login.password;

        const twofaInput = document.getElementById('2fa-token');
        if (twofaInput) twofaInput.placeholder = t.gui.login.twofa_code;

        const loginButton = document.getElementById('login-button');
        if (loginButton) loginButton.textContent = t.gui.login.button;

        // Update OAuth display text
        const oauthDisplay = document.getElementById('oauth-code-display');
        if (oauthDisplay) {
            const oauthP = oauthDisplay.querySelector('p');
            if (oauthP) {
                const link = oauthP.querySelector('a');
                if (link) {
                    oauthP.textContent = t.gui.login.oauth_prompt + ' ';
                    link.textContent = t.gui.login.oauth_activate;
                    oauthP.appendChild(link);
                }
            }

            const oauthConfirmBtn = document.getElementById('oauth-confirm');
            if (oauthConfirmBtn) oauthConfirmBtn.textContent = t.gui.login.oauth_confirm;
        }
    }

    // Update Progress section
    if (mainTab && t.gui?.progress) {
        const progressHeader = mainTab.querySelector('.progress-panel h2');
        if (progressHeader) progressHeader.textContent = t.gui.progress.name;

        const noDropMsg = document.getElementById('no-drop-message');
        if (noDropMsg) noDropMsg.textContent = t.gui.progress.no_drop;

        const exitManualBtn = document.getElementById('exit-manual-btn');
        if (exitManualBtn) exitManualBtn.textContent = t.gui.progress.return_to_auto;
    }

    // Update Console section
    if (mainTab && t.gui) {
        const consoleHeader = mainTab.querySelector('.console-panel h2');
        if (consoleHeader) consoleHeader.textContent = t.gui.output;
    }

    // Update Channels section
    if (mainTab && t.gui?.channels) {
        const channelsHeader = mainTab.querySelector('.channels-panel h2');
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
        const headers = settingsTab.querySelectorAll('h2');
        if (headers[0]) headers[0].textContent = t.gui.settings.general.name;
        // headers[1] is the Telegram section header in DOM order
        if (headers[1]) {
            const telegramName = (t.settings && t.settings.telegram && t.settings.telegram.name) || (t.gui && t.gui.settings && t.gui.settings.telegram && t.gui.settings.telegram.name) || null;
            headers[1].textContent = telegramName || headers[1].textContent;
        }
        // games to watch is the next header
        if (headers[2]) headers[2].textContent = t.gui.settings.games_to_watch;
        if (headers[3]) headers[3].textContent = t.gui.settings.actions;

        const darkModeLabel = settingsTab.querySelector('label:has(#dark-mode)');
        if (darkModeLabel) {
            const checkbox = darkModeLabel.querySelector('input');
            darkModeLabel.textContent = '';
            darkModeLabel.appendChild(checkbox);
            darkModeLabel.appendChild(document.createTextNode(' ' + t.gui.settings.general.dark_mode));
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

        const helpText = settingsTab.querySelector('.help-text');
        if (helpText) helpText.textContent = t.gui.settings.games_help;

        const searchInput = document.getElementById('games-filter');
        if (searchInput) searchInput.placeholder = t.gui.settings.search_games;

        const selectAllBtn = document.getElementById('select-all-btn');
        if (selectAllBtn) selectAllBtn.textContent = t.gui.settings.select_all;

        const deselectAllBtn = document.getElementById('deselect-all-btn');
        if (deselectAllBtn) deselectAllBtn.textContent = t.gui.settings.deselect_all;

        const selectedGamesHeader = settingsTab.querySelector('.selected-games h3');
        if (selectedGamesHeader) selectedGamesHeader.textContent = t.gui.settings.selected_games;

        const availableGamesHeader = settingsTab.querySelector('.available-games h3');
        if (availableGamesHeader) availableGamesHeader.textContent = t.gui.settings.available_games;

        const reloadBtn = document.getElementById('reload-btn');
        if (reloadBtn) reloadBtn.textContent = t.gui.settings.reload_campaigns;

        // Update Telegram Notifications section (use either t.settings.telegram or t.gui.settings.telegram)
        const tgTrans = (t.settings && t.settings.telegram) || (t.gui && t.gui.settings && t.gui.settings.telegram) || null;
        if (tgTrans) {
            const telegramSection = settingsTab.querySelector('.settings-section:has(#telegram-bot-token)');
            if (telegramSection) {
                const heading = telegramSection.querySelector('h2');
                if (heading) heading.textContent = tgTrans.name || heading.textContent;

                const description = telegramSection.querySelector('.help-text');
                if (description) description.textContent = tgTrans.description || description.textContent;

                const labels = telegramSection.querySelectorAll('label');
                if (labels[0]) {
                    const span = labels[0].querySelector('span');
                    if (span) span.textContent = (tgTrans.bot_token ? tgTrans.bot_token + ':' : span.textContent);
                }
                if (labels[1]) {
                    const span = labels[1].querySelector('span');
                    if (span) span.textContent = (tgTrans.chat_id ? tgTrans.chat_id + ':' : span.textContent);
                    const small = labels[1].querySelector('small');
                    if (small) small.textContent = tgTrans.your_user_id || small.textContent;
                }

                const saveTelegramBtn = document.getElementById('save-telegram-btn');
                if (saveTelegramBtn) saveTelegramBtn.textContent = tgTrans.save_settings || saveTelegramBtn.textContent;

                const testTelegramBtn = document.getElementById('test-telegram-btn');
                if (testTelegramBtn) testTelegramBtn.textContent = tgTrans.test_connection || testTelegramBtn.textContent;
            }
        }

        // Re-render games to watch with translated empty messages
        renderGamesToWatch();
    }

    // Update Help tab
    const helpTab = document.getElementById('help-tab');
    if (helpTab && t.gui?.help) {
        const helpContent = helpTab.querySelector('.help-content');
        if (helpContent) {
            // Rebuild help content dynamically
            helpContent.innerHTML = `
                <h2>${t.gui.help.about || 'About Twitch Drops Miner'}</h2>
                <p>${t.gui.help.about_text || 'This application automatically mines timed Twitch drops without downloading stream data.'}</p>

                <h3>${t.gui.help.how_to_use || 'How to Use'}</h3>
                <ol>
                    ${(t.gui.help.how_to_use_items || [
                    'Login using your Twitch account (OAuth device code flow)',
                    'Link your accounts at <a href="https://www.twitch.tv/drops/campaigns" target="_blank">twitch.tv/drops/campaigns</a>',
                    'The miner will automatically discover campaigns and start mining',
                    'Configure priority games in Settings to focus on what you want',
                    'Monitor progress in the Main and Inventory tabs'
                ]).map(item => `<li>${item}</li>`).join('')}
                </ol>

                <h3>${t.gui.help.features || 'Features'}</h3>
                <ul>
                    ${(t.gui.help.features_items || [
                    'Stream-less drop mining - saves bandwidth',
                    'Game priority and exclusion lists',
                    'Tracks up to 199 channels simultaneously',
                    'Automatic channel switching',
                    'Real-time progress tracking'
                ]).map(item => `<li>${item}</li>`).join('')}
                </ul>

                <h3>${t.gui.help.important_notes || 'Important Notes'}</h3>
                <ul>
                    ${(t.gui.help.important_notes_items || [
                    'Do not watch streams on the same account while mining',
                    'Keep your cookies.jar file secure',
                    'Requires linked game accounts for drops'
                ]).map(item => `<li>${item}</li>`).join('')}
                </ul>

                <!-- Telegram setup (translated) -->
                ${(() => {
                    const tg = (t.settings && t.settings.telegram) || (t.gui && t.gui.settings && t.gui.settings.telegram) || null;
                    const title = tg && tg.name ? tg.name : (t.gui && t.gui.settings && t.gui.settings.telegram && t.gui.settings.telegram.name) || 'Telegram Notifications';
                    const desc = tg && tg.description ? tg.description : (t.gui && t.gui.settings && t.gui.settings.telegram && t.gui.settings.telegram.description) || 'Receive instant notifications on Telegram when you claim drops. Setup instructions:';
                    const steps = (tg && tg.setup_steps) || (t.gui && t.gui.settings && t.gui.settings.telegram && t.gui.settings.telegram.setup_steps) || [
                        'Go to @BotFather on Telegram',
                        'Create a new bot with /newbot command',
                        'Save the bot token you receive',
                        'Start your new bot by searching for it and clicking /start (or send any message)',
                        'Get your Chat ID by opening this URL in browser (replace TOKEN): https://api.telegram.org/botTOKEN/getUpdates',
                        'Find your user ID in the response - it is the number in "from": {"id": YOUR_ID}',
                        'Enter the token and Chat ID in Settings and click Test Connection'
                    ];

                    return `
                        <h3>${title}</h3>
                        <p>${desc}</p>
                        <ol>
                            ${steps.map(s => `<li>${s}</li>`).join('')}
                        </ol>
                    `;
                })()}

                <div class="help-links">
                    <a href="https://github.com/rangermix/TwitchDropsMiner" target="_blank">${t.gui.help.github_repo || 'GitHub Repository'}</a>
                </div>
            `;
        }
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
    // Fetch and display version information
    fetchAndDisplayVersion();

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
    // Proxy uses a manual "Set Proxy" button instead of auto-save
    document.getElementById('set-proxy-btn').addEventListener('click', () => {
        const proxyInput = document.getElementById('proxy-url');
        const newValue = proxyInput ? proxyInput.value : '';

        // Only save if changed
        if (newValue !== (state.settings.proxy || '')) {
            state.settings.proxy = newValue;
            saveSettings();
        }
    });
    document.getElementById('verify-proxy-btn').addEventListener('click', verifyProxy);
    document.getElementById('test-telegram-btn').addEventListener('click', testTelegramConnection);
    document.getElementById('save-telegram-btn').addEventListener('click', handleSaveTelegramClick);
    document.getElementById('reload-btn').addEventListener('click', reloadCampaigns);


    // Games to watch management
    document.getElementById('select-all-btn').addEventListener('click', selectAllGames);
    document.getElementById('deselect-all-btn').addEventListener('click', deselectAllGames);
    document.getElementById('games-filter').addEventListener('input', renderGamesToWatch);

    // Inventory filters
    document.getElementById('filter-active').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-not-linked').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-upcoming').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-expired').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-finished').addEventListener('change', onInventoryFilterChange);
    // Benefit type filters
    document.getElementById('filter-benefit-item').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-benefit-badge').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-benefit-emote').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-benefit-other').addEventListener('change', onInventoryFilterChange);
    document.getElementById('clear-filters-btn').addEventListener('click', clearInventoryFilters);

    // Mining benefit settings
    document.getElementById('mining-benefit-item').addEventListener('change', saveSettings);
    document.getElementById('mining-benefit-badge').addEventListener('change', saveSettings);
    document.getElementById('mining-benefit-emote').addEventListener('change', saveSettings);
    document.getElementById('mining-benefit-unknown').addEventListener('change', saveSettings);


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
        container.innerHTML = '<p class="empty-message-small">No wanted drops queued...</p>';
        return;
    }

    tree.forEach((gameGroup, index) => {
        const groupEl = document.createElement('div');
        groupEl.className = 'wanted-game-group';

        const headerEl = document.createElement('div');
        headerEl.className = 'wanted-game-header';

        // Game Icon
        let iconUrl = gameGroup.game_icon;
        if (iconUrl) {
            iconUrl = iconUrl.replace('{width}', '40').replace('{height}', '53'); // 3:4 aspect ratio approx
        }

        const iconHtml = iconUrl
            ? `<img src="${iconUrl}" alt="${gameGroup.game_name}" class="wanted-game-icon" onerror="this.style.display='none'">`
            : '';

        headerEl.innerHTML = `
            <span class="wanted-game-index">#${index + 1}</span>
            ${iconHtml}
            <span class="wanted-game-title">${gameGroup.game_name}</span>
        `;
        groupEl.appendChild(headerEl);

        const campaignListEl = document.createElement('div');
        campaignListEl.className = 'wanted-campaign-list';

        gameGroup.campaigns.forEach(campaign => {
            const cardEl = document.createElement('div');
            cardEl.className = 'wanted-card';

            cardEl.innerHTML = `
                <div class="wanted-card-header">
                     <a href="${campaign.url}" target="_blank" rel="noopener noreferrer" class="wanted-card-campaign-link" title="${campaign.name}">
                        ${campaign.name}
                    </a>
                </div>
                <div class="wanted-card-body">
                    <div id="wanted-drops-${campaign.id}"></div>
                </div>
            `;

            const dropContainer = cardEl.querySelector(`#wanted-drops-${campaign.id}`);

            campaign.drops.forEach(drop => {
                const dropEl = document.createElement('div');
                dropEl.className = 'wanted-drop-item';

                let html = `<span class="wanted-drop-name">${drop.name}</span>`;

                drop.benefits.forEach(benefit => {
                    html += `<span class="wanted-benefit-pill">${benefit}</span>`;
                });

                dropEl.innerHTML = html;
                dropContainer.appendChild(dropEl);
            });

            campaignListEl.appendChild(cardEl);
        });

        groupEl.appendChild(campaignListEl);
        container.appendChild(groupEl);
    });
}
