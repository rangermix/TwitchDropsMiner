// Shared dashboard login and settings controls. No credentials enter app state or storage.
const dashboardAuth = {
    enabled: false,
    busy: false,
    translations: {},

    async refresh(reconnect = false) {
        if (this.busy) return;
        const response = await fetch('/api/auth/status', { cache: 'no-store' });
        if (!response.ok) throw new Error('request_failed');
        const data = await response.json();
        this.enabled = data.enabled;
        this.translations = data.translations;
        const loginPage = Boolean(document.getElementById('web-auth-login'));
        if (!data.authenticated && !loginPage) {
            window.location.replace('/login');
            return;
        }
        if (data.authenticated && loginPage) {
            window.location.replace('/');
            return;
        }
        document.querySelectorAll('[data-auth-text]').forEach(element => {
            element.textContent = this.translations[element.dataset.authText] || element.textContent;
        });
        const logout = document.getElementById('web-auth-logout');
        if (logout) logout.hidden = !data.enabled;
        const current = document.getElementById('web-auth-current-row');
        if (current) {
            current.hidden = !data.enabled;
            document.getElementById('web-auth-current').required = data.enabled;
            document.getElementById('web-auth-disable').hidden = !data.enabled;
            document.getElementById('web-auth-save').textContent =
                this.translations[data.enabled ? 'change' : 'enable'];
            document.getElementById('web-auth-state').textContent =
                this.translations[data.enabled ? 'enabled' : 'disabled'];
        }
        document.querySelectorAll('.auth-form button').forEach(button => { button.disabled = false; });
        if (reconnect && typeof socket !== 'undefined') socket.connect();
    },

    async submit(action) {
        if (this.busy) return;
        const result = document.getElementById(action === 'logout' ? 'web-auth-logout-result' : 'web-auth-result');
        const value = id => document.getElementById(id)?.value || '';
        const payload = action === 'login'
            ? { password: value('web-auth-password'), remember: document.getElementById('web-auth-remember').checked }
            : action === 'logout' ? {} : {
                action, current_password: value('web-auth-current'),
                password: action === 'disable' ? '' : value('web-auth-new'),
                confirm_password: action === 'disable' ? '' : value('web-auth-confirm')
            };
        this.busy = true;
        if (result) result.textContent = '';
        document.querySelectorAll('.auth-form button, #web-auth-logout').forEach(button => { button.disabled = true; });
        try {
            const endpoint = ['login', 'logout'].includes(action) ? action : 'settings';
            const response = await fetch(`/api/auth/${endpoint}`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.detail || 'request_failed');
            document.querySelectorAll('.auth-form input[type="password"]').forEach(input => { input.value = ''; });
            window.location.replace(action === 'logout' ? '/login' : '/');
        } catch (error) {
            if (result) result.textContent = this.translations[error.message] || this.translations.request_failed || 'Request failed. Try again.';
            if (error.message === 'authentication_required') window.location.replace('/login');
        } finally {
            this.busy = false;
            document.querySelectorAll('.auth-form button, #web-auth-logout').forEach(button => { button.disabled = false; });
        }
    }
};

// Same-origin custom header prevents cross-site form submissions, including initial setup.
const dashboardFetch = window.fetch.bind(window);
window.fetch = async function(input, options = {}) {
    const url = new URL(input instanceof Request ? input.url : input, window.location.href);
    const sameOrigin = url.origin === window.location.origin;
    const method = (options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (sameOrigin && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
        const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
        headers.set('X-TDM-Request', '1');
        options = { ...options, headers };
    }
    const response = await dashboardFetch(input, options);
    if (sameOrigin && response.status === 401 && !url.pathname.startsWith('/api/auth/')) {
        window.location.replace('/login');
    }
    return response;
};

document.addEventListener('DOMContentLoaded', () => {
    const login = document.getElementById('web-auth-login');
    if (login) login.addEventListener('submit', event => { event.preventDefault(); dashboardAuth.submit('login'); });
    const settings = document.getElementById('web-auth-settings');
    if (settings) settings.addEventListener('submit', event => {
        event.preventDefault(); dashboardAuth.submit(dashboardAuth.enabled ? 'change' : 'enable');
    });
    document.getElementById('web-auth-disable')?.addEventListener('click', () => dashboardAuth.submit('disable'));
    document.getElementById('web-auth-logout')?.addEventListener('click', () => dashboardAuth.submit('logout'));
    const refresh = reconnect => dashboardAuth.refresh(reconnect).catch(() => {
        const result = document.getElementById('web-auth-result');
        if (result) result.textContent = dashboardAuth.translations.request_failed || 'Request failed. Try again.';
        if (login && !dashboardAuth.busy) {
            login.querySelectorAll('button').forEach(button => { button.disabled = false; });
        }
    });
    refresh(false);
    if (typeof socket !== 'undefined') {
        socket.on('disconnect', reason => { if (reason === 'io server disconnect') refresh(true); });
        socket.on('connect_error', () => refresh(false));
        socket.on('language_changed', () => refresh(false));
    }
});

window.addEventListener('pageshow', event => {
    if (event.persisted) window.location.reload();
});
