/* Twitch credentials stay in the login browser; the dashboard controls helper access. */
class BrowserLoginPanel {
    constructor(doc = document, fetcher = fetch,
        translations = () => state.translations.gui?.browser_login || {},
        viewerFactory = async () => (await import('/static/novnc/core/rfb.js')).default,
        clock = () => Date.now() / 1000) {
        this.doc = doc;
        this.fetcher = fetcher;
        this.translations = translations;
        this.viewerFactory = viewerFactory;
        this.clock = clock;
        this.data = null;
        this.revision = 0;
        this.request = 0;
        this.busy = false;
        this.actionError = '';
        this.statusError = false;
        this.viewerError = false;
        this.viewer = null;
        this.viewerAttempt = null;
        this.viewerRevision = 0;
        this.actionRevision = 0;
        this.element('twitch-login-finish').addEventListener('click', () => this.action('finish'));
        this.element('twitch-login-retry').addEventListener('click', () => this.retry());
        this.element('twitch-logout').addEventListener('click', () => this.logout());
        this.element('twitch-helper-enable').addEventListener('click', () => this.action('helper/enable'));
        this.element('twitch-helper-cancel').addEventListener('click', () => this.action('helper/cancel'));
        this.render();
    }

    element(id) { return this.doc.getElementById(id); }

    helperActive() {
        return Boolean(this.data?.helper && !['disabled', 'complete'].includes(this.data.helper.state));
    }

    render() {
        const t = this.translations();
        for (const [id, key] of Object.entries({
            'twitch-login-title': 'title', 'twitch-login-instructions': 'instructions',
            'twitch-login-finish': 'finish', 'twitch-login-retry': 'retry',
            'twitch-logout': 'logout', 'twitch-logout-help': 'logout_help',
            'twitch-helper-title': 'helper_title', 'twitch-helper-help': 'helper_help',
            'twitch-helper-downloads': 'helper_downloads', 'twitch-helper-builds-note': 'helper_builds_note',
            'twitch-helper-enable': 'helper_title', 'twitch-helper-cancel': 'helper_cancel',
            'twitch-helper-instructions': 'helper_instructions', 'twitch-helper-url-label': 'helper_url',
            'twitch-helper-access-help': 'helper_access_help',
            'twitch-helper-windows': 'helper_windows', 'twitch-helper-linux': 'helper_linux',
            'twitch-helper-macos-arm': 'helper_macos_arm', 'twitch-helper-macos-intel': 'helper_macos_intel',
        })) this.element(id).textContent = t[key] || '';
        const browser = this.data?.browser;
        const helper = this.data?.helper;
        const helperActive = this.helperActive();
        const expired = helper?.expires_at != null && helper.expires_at <= this.clock();
        // Verification includes private-profile cleanup before returning to mining.
        const normal = this.data?.logged_in === true && !helperActive
            && !['starting', 'sign_in', 'verifying'].includes(browser?.state);
        this.element('dashboard').hidden = !normal;
        this.element('twitch-login-screen').hidden = normal;
        const access = this.element('dashboard-access-controls');
        const accessSlot = this.element(normal ? 'dashboard-access-slot' : 'twitch-login-access');
        if (access.parentElement !== accessSlot) accessSlot.appendChild(access);
        let message = t[browser?.state] || t.starting || '';
        if (browser?.state === 'error') {
            message = browser.error === 'BROWSER_UNAVAILABLE' ? t.unavailable : t.error;
            if (typeof browser.error === 'string') message = (message || '') + ' (' + browser.error + ')';
        }
        let helperMessage = helperActive ? (t['helper_' + helper.state] || '') : '';
        if (helper?.state === 'waiting' && expired) helperMessage = t.helper_expired || '';
        if (helper?.state === 'error' && helper.error) helperMessage += ' (' + helper.error + ')';
        if (helperActive) message = helperMessage;
        if (this.statusError) message = t.action_failed || '';
        else if (this.viewerError && !helperActive) message = t.viewer_closed || '';
        this.element('twitch-login-status').textContent = message;
        this.element('twitch-login-error').textContent = t[this.actionError] || '';
        this.element('twitch-logout-result').textContent = t[this.actionError] || '';
        this.element('twitch-login-instructions').hidden = Boolean(helperActive);
        this.element('twitch-login-finish').hidden = helperActive || browser?.state !== 'sign_in';
        this.element('twitch-login-finish').disabled = this.busy || this.statusError;
        this.element('twitch-login-retry').hidden = helperActive
            || !(this.viewerError || this.statusError || browser?.state === 'error');
        this.element('twitch-login-retry').disabled = this.busy;
        this.element('twitch-logout').disabled = this.busy || !normal;
        this.element('twitch-renewal-status').textContent = browser?.state === 'error' ? (t.error || '')
            : this.data?.renewal_error ? (t.renewal_retry || '')
            : this.data?.renewal_available ? (t.renewal_ready || '') : '';
        this.element('twitch-renewal-status').title = this.element('twitch-renewal-status').textContent;
        this.element('twitch-vnc').hidden = helperActive || browser?.state !== 'sign_in';
        this.element('twitch-helper-panel').hidden = normal;
        if (helperActive) this.element('twitch-helper-panel').open = true;
        this.element('twitch-helper-enable').hidden = Boolean(helperActive);
        this.element('twitch-helper-enable').disabled = this.busy || this.statusError || !helper || this.data?.logged_in;
        this.element('twitch-helper-cancel').hidden = !helperActive;
        this.element('twitch-helper-cancel').disabled = this.busy;
        this.element('twitch-helper-connection').hidden = helper?.state !== 'waiting' || expired;
        this.element('twitch-helper-url').value = new URL(window.location.href).origin;
        this.element('twitch-helper-status').textContent = helperMessage;
        this.syncViewer();
    }

    updateStatus(data) {
        if (typeof data?.logged_in !== 'boolean' || !data.session || !data.browser
            || !['idle', 'starting', 'sign_in', 'verifying', 'error'].includes(data.browser.state)
            || !Number.isInteger(data.browser.attempt) || data.browser.attempt < 0
            || (data.browser.error != null && !/^[A-Z_]{1,64}$/.test(data.browser.error))) {
            this.statusError = true;
            this.render();
            return false;
        }
        if (data.helper && (!['disabled', 'waiting', 'connected', 'verifying', 'complete', 'error'].includes(data.helper.state)
            || !Number.isInteger(data.helper.attempt) || data.helper.attempt < 0
            || (data.helper.expires_at !== null && !Number.isFinite(data.helper.expires_at))
            || (data.helper.state === 'waiting' && data.helper.expires_at === null)
            || (data.helper.error != null && !/^[A-Z_]{1,64}$/.test(data.helper.error)))) {
            this.statusError = true;
            this.render();
            return false;
        }
        this.data = {
            logged_in: data.logged_in, session: data.session, browser: data.browser, helper: data.helper,
            renewal_error: data.renewal_error, renewal_available: data.renewal_available,
            renewal_requires_login: data.renewal_requires_login,
        };
        this.revision++;
        this.statusError = false;
        this.render();
        return true;
    }

    updateLogin() { this.load(); }

    async load() {
        if (this.busy) return;
        const revision = this.revision;
        const request = ++this.request;
        try {
            const response = await this.fetcher('/api/session', {cache: 'no-store'});
            if (!response.ok) throw new Error('status');
            const data = await response.json();
            if (revision === this.revision && request === this.request) this.updateStatus(data);
        } catch (_) {
            if (revision === this.revision && request === this.request) {
                this.statusError = true;
                this.render();
            }
        }
    }

    disconnectViewer() {
        this.viewerRevision++;
        this.viewerAttempt = null;
        const viewer = this.viewer;
        this.viewer = null;
        if (viewer) viewer.disconnect();
        this.element('twitch-vnc').replaceChildren();
    }

    async syncViewer() {
        if (this.helperActive() || this.data?.browser.state !== 'sign_in') {
            if (this.viewerAttempt !== null) this.disconnectViewer();
            return;
        }
        const attempt = this.data.browser.attempt;
        if (this.viewerAttempt === attempt) return;
        this.disconnectViewer();
        this.viewerAttempt = attempt;
        this.viewerError = false;
        const revision = this.viewerRevision;
        try {
            const RFB = await this.viewerFactory();
            if (revision !== this.viewerRevision) return;
            const url = new URL('/api/session/vnc', window.location.href);
            url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
            const viewer = new RFB(this.element('twitch-vnc'), url.href);
            this.viewer = viewer;
            viewer.scaleViewport = true;
            viewer.resizeSession = false;
            viewer.addEventListener('disconnect', () => {
                if (revision !== this.viewerRevision) return;
                this.viewerError = true;
                this.render();
            });
            viewer.addEventListener('securityfailure', () => {
                if (revision !== this.viewerRevision) return;
                this.viewerError = true;
                this.render();
            });
        } catch (_) {
            if (revision === this.viewerRevision) {
                this.viewerError = true;
                this.render();
            }
        }
    }

    async action(name) {
        if (this.busy) return;
        const actionRevision = ++this.actionRevision;
        const revision = this.revision;
        ++this.request; // An earlier poll cannot overwrite an action's resulting state.
        this.busy = true;
        this.actionError = '';
        this.render();
        try {
            const response = await this.fetcher('/api/session/' + name, {
                method: 'POST', headers: {'X-TDM-Request': '1'},
            });
            if (!response.ok) throw new Error('action');
            const data = await response.json();
            if (actionRevision !== this.actionRevision) return;
            if (name === 'helper/enable') {
                // A later connection/cancel/broadcast takes precedence over this response.
                const sameWaitingAttempt = this.data?.helper?.state === 'waiting'
                    && this.data.helper.attempt === data.helper?.attempt;
                if (revision === this.revision || sameWaitingAttempt) {
                    this.updateStatus(data);
                }
            } else if (revision === this.revision
                || (data.helper && data.helper.attempt > (this.data?.helper?.attempt ?? -1))) {
                this.updateStatus(data);
            }
        } catch (_) {
            this.actionError = name === 'logout' ? 'logout_failed' : 'action_failed';
        } finally {
            this.busy = false;
            this.render();
            await this.load();
        }
    }

    async retry() {
        if (this.data?.browser.state === 'error') await this.action('retry');
        else {
            this.disconnectViewer();
            this.viewerError = false;
            await this.load();
        }
    }

    async logout() {
        if (!this.busy && this.data?.logged_in && window.confirm(this.translations().logout_confirm || '')) {
            await this.action('logout');
        }
    }
}

window.BrowserLoginPanel = BrowserLoginPanel;
document.addEventListener('DOMContentLoaded', () => {
    window.browserLoginPanel = new BrowserLoginPanel();
    window.browserLoginPanel.load();
    setInterval(() => window.browserLoginPanel.load(), 2000);
});
