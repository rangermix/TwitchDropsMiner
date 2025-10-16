// Twitch Drops Miner Web Client
// Socket.IO and API communication

// Global state
const state = {
    connected: false,
    channels: {},
    campaigns: {},
    settings: {},
    currentDrop: null
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
    document.getElementById('connection-indicator').textContent = '● Connected';
    document.getElementById('connection-indicator').className = 'connected';
});

socket.on('disconnect', () => {
    console.log('Disconnected from server');
    state.connected = false;
    document.getElementById('connection-indicator').textContent = '● Disconnected';
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
        audio.play().catch(() => {});
    }
    // Flash title
    flashTitle();
});

// ==================== UI Update Functions ====================

function updateStatus(status) {
    document.getElementById('status-text').textContent = status;
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

    const channels = Object.values(state.channels);
    if (channels.length === 0) {
        container.innerHTML = '<p class="empty-message">No channels tracked yet...</p>';
        return;
    }

    // Sort: watching first, then online, then by viewers
    channels.sort((a, b) => {
        if (a.watching !== b.watching) return b.watching ? 1 : -1;
        if (a.online !== b.online) return b.online ? 1 : -1;
        return (b.viewers || 0) - (a.viewers || 0);
    });

    channels.forEach(channel => {
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
                ${channel.game || 'No game'} •
                ${channel.viewers !== null ? channel.viewers.toLocaleString() + ' viewers' : 'Offline'}
                ${channel.watching ? ' • <strong>WATCHING</strong>' : ''}
            </div>
        `;

        div.onclick = () => selectChannel(channel.id);
        container.appendChild(div);
    });
}

function updateDropProgress(data) {
    state.currentDrop = data;
    document.getElementById('no-drop-message').style.display = 'none';
    document.getElementById('drop-info').style.display = 'block';

    document.getElementById('drop-name').textContent = data.drop_name;
    document.getElementById('drop-game').textContent = `${data.campaign_name} (${data.game_name})`;

    const progress = data.progress * 100;
    const fill = document.getElementById('progress-fill');
    fill.style.width = `${progress}%`;
    fill.textContent = `${Math.round(progress)}%`;

    document.getElementById('progress-text').textContent =
        `${data.current_minutes} / ${data.required_minutes} minutes`;

    // Update remaining time
    updateRemainingTime(data.remaining_seconds);
}

function updateRemainingTime(seconds) {
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    document.getElementById('progress-time').textContent =
        `Time remaining: ${minutes}:${secs.toString().padStart(2, '0')}`;

    if (seconds > 0) {
        setTimeout(() => updateRemainingTime(seconds - 1), 1000);
    }
}

function clearDropProgress() {
    state.currentDrop = null;
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

    const campaigns = Object.values(state.campaigns);
    if (campaigns.length === 0) {
        container.innerHTML = '<p class="empty-message">No campaigns loaded yet...</p>';
        return;
    }

    campaigns.forEach(campaign => {
        const card = document.createElement('div');
        card.className = 'campaign-card';

        let statusClass = '';
        let statusText = '';
        if (campaign.active) {
            statusClass = 'active';
            statusText = 'Active';
        } else if (campaign.upcoming) {
            statusClass = 'upcoming';
            statusText = 'Upcoming';
        } else if (campaign.expired) {
            statusClass = 'expired';
            statusText = 'Expired';
        }

        const dropsHtml = campaign.drops.map(drop => `
            <div class="drop-item ${drop.is_claimed ? 'claimed' : ''} ${drop.can_claim ? 'active' : ''}">
                <div><strong>${drop.name}</strong></div>
                <div>${drop.rewards}</div>
                <div>${drop.current_minutes} / ${drop.required_minutes} minutes (${Math.round(drop.progress * 100)}%)</div>
                ${drop.is_claimed ? '<div>✓ Claimed</div>' : ''}
            </div>
        `).join('');

        card.innerHTML = `
            <div class="campaign-header">
                <div class="campaign-game">${campaign.game_name}</div>
                <div class="campaign-name">${campaign.name}</div>
            </div>
            <div class="campaign-status">
                <span>${statusText}</span>
                <span>${campaign.claimed_drops} / ${campaign.total_drops} claimed</span>
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
    if (data.user_id) {
        statusEl.textContent = `Logged in (User ID: ${data.user_id})`;
        statusEl.style.color = 'var(--success-color)';
        document.getElementById('login-form').style.display = 'none';
        document.getElementById('oauth-code-display').style.display = 'none';
    } else {
        statusEl.textContent = data.status || 'Not logged in';
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

    container.innerHTML = '';

    if (games.length === 0) {
        container.innerHTML = '<p class="empty-message">No games selected. Check games below to add them.</p>';
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

    container.innerHTML = '';

    if (games.length === 0) {
        if (filterText) {
            container.innerHTML = '<p class="empty-message">No games match your search.</p>';
        } else {
            container.innerHTML = '<p class="empty-message">All games are selected or no games available.</p>';
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
    saveSettings();
}

function removeGameFromWatch(gameName) {
    const games = state.settings.games_to_watch || [];
    const index = games.indexOf(gameName);
    if (index > -1) {
        games.splice(index, 1);
        state.settings.games_to_watch = games;
        renderGamesToWatch();
        saveSettings();
    }
}

function selectAllGames() {
    state.settings.games_to_watch = Array.from(availableGames).sort();
    renderGamesToWatch();
    saveSettings();
}

function deselectAllGames() {
    state.settings.games_to_watch = [];
    renderGamesToWatch();
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
        await fetch('/api/channels/select', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({channel_id: channelId})
        });
    } catch (error) {
        console.error('Failed to select channel:', error);
    }
}

async function submitLogin() {
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    const token = document.getElementById('2fa-token').value;

    try {
        await fetch('/api/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username, password, token})
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
        document.getElementById('login-status').textContent = 'Waiting for authentication...';
    } catch (error) {
        console.error('Failed to confirm OAuth:', error);
    }
}

async function saveSettings() {
    const settings = {
        dark_mode: document.getElementById('dark-mode').checked,
        connection_quality: parseInt(document.getElementById('connection-quality').value),
        games_to_watch: state.settings.games_to_watch || []
    };

    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(settings)
        });
        console.log('Settings saved automatically');
    } catch (error) {
        console.error('Failed to save settings:', error);
    }
}

async function reloadCampaigns() {
    try {
        await fetch('/api/reload', {method: 'POST'});
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
    document.getElementById('connection-quality').addEventListener('change', saveSettings);
    document.getElementById('reload-btn').addEventListener('click', reloadCampaigns);

    // Games to watch management
    document.getElementById('select-all-btn').addEventListener('click', selectAllGames);
    document.getElementById('deselect-all-btn').addEventListener('click', deselectAllGames);
    document.getElementById('games-filter').addEventListener('input', renderGamesToWatch);

    // Request notification permission
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }
});
