/* Credentials stay in the container browser; this panel handles public state and VNC. */
class BrowserLoginPanel {
    constructor(doc = document, fetcher = fetch,
        translations = () => state.translations.gui?.browser_login || {},
        viewerFactory = async () => (await import('/static/novnc/core/rfb.js')).default) {
        this.doc = doc;
        this.fetcher = fetcher;
        this.translations = translations;
        this.viewerFactory = viewerFactory;
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
        this.element('twitch-login-finish').addEventListener('click', () => this.action('finish'));
        this.element('twitch-login-retry').addEventListener('click', () => this.retry());
        this.element('twitch-logout').addEventListener('click', () => this.logout());
        this.render();
    }

    element(id) { return this.doc.getElementById(id); }

    render() {
        const t = this.translations();
        for (const [id, key] of Object.entries({
            'twitch-login-title': 'title', 'twitch-login-instructions': 'instructions',
            'twitch-login-finish': 'finish', 'twitch-login-retry': 'retry',
            'twitch-logout': 'logout', 'twitch-logout-help': 'logout_help',
        })) this.element(id).textContent = t[key] || '';
        const browser = this.data?.browser;
        // Verification includes private-profile cleanup before returning to mining.
        const normal = this.data?.logged_in === true && !['starting', 'sign_in', 'verifying'].includes(browser?.state);
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
        if (this.statusError) message = t.action_failed || '';
        else if (this.viewerError) message = t.viewer_closed || '';
        this.element('twitch-login-status').textContent = message;
        this.element('twitch-login-error').textContent = t[this.actionError] || '';
        this.element('twitch-logout-result').textContent = t[this.actionError] || '';
        this.element('twitch-login-finish').hidden = browser?.state !== 'sign_in';
        this.element('twitch-login-finish').disabled = this.busy || this.statusError;
        this.element('twitch-login-retry').hidden = !(this.viewerError || this.statusError || browser?.state === 'error');
        this.element('twitch-login-retry').disabled = this.busy;
        this.element('twitch-logout').disabled = this.busy || !normal;
        this.element('twitch-renewal-status').textContent = browser?.state === 'error' ? (t.error || '')
            : this.data?.renewal_error ? (t.renewal_retry || '')
            : this.data?.renewal_available ? (t.renewal_ready || '') : '';
        this.element('twitch-vnc').hidden = browser?.state !== 'sign_in';
        this.syncViewer();
    }

    updateStatus(data) {
        if (typeof data?.logged_in !== 'boolean' || !data.session || !data.browser
            || !['idle', 'starting', 'sign_in', 'verifying', 'error'].includes(data.browser.state)
            || !Number.isInteger(data.browser.attempt) || data.browser.attempt < 0
            || (data.browser.error != null && !/^[A-Z_]{1,64}$/.test(data.browser.error))) return;
        this.data = data;
        this.revision++;
        this.statusError = false;
        this.render();
    }

    updateLogin() { this.load(); }

    async load() {
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
        if (this.data?.browser.state !== 'sign_in') {
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
        this.busy = true;
        this.actionError = '';
        this.render();
        try {
            const response = await this.fetcher('/api/session/' + name, {
                method: 'POST', headers: {'X-TDM-Request': '1'},
            });
            if (!response.ok) throw new Error('action');
            this.updateStatus(await response.json());
        } catch (_) {
            this.actionError = name === 'logout' ? 'logout_failed' : 'action_failed';
            await this.load();
        } finally {
            this.busy = false;
            this.render();
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
