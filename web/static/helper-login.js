/* The native helper sends credentials directly; the dashboard only handles public status. */
class HelperLoginPanel {
    constructor(doc = document, fetcher = fetch,
        translations = () => state.translations.gui?.helper_login || {},
        instanceUrl = window.location.origin, onLogin = data => updateLoginStatus(data)) {
        this.doc = doc;
        this.fetcher = fetcher;
        this.translations = translations;
        this.instanceUrl = instanceUrl;
        this.onLogin = onLogin;
        this.data = null;
        this.login = null;
        this.allowed = null;
        this.saving = false;
        this.pendingChoice = null;
        this.saveError = false;
        this.statusError = false;
        this.copyResult = '';
        this.revision = 0;
        this.gateRevision = 0;
        this.request = 0;
        this.element('allow-helper-connection').addEventListener('change', () => this.saveAllowed());
        this.element('helper-copy-instance').addEventListener('click', () => this.copyInstance());
        this.element('helper-status-retry').addEventListener('click', () => this.load());
        this.render();
    }

    element(id) {
        return this.doc.getElementById(id);
    }

    render() {
        const t = this.translations();
        const labels = {
            'helper-step-download': 'step_download', 'helper-step-instance': 'step_instance',
            'helper-step-chrome': 'step_chrome', 'helper-step-finish': 'step_finish',
            'helper-download': 'builds', 'helper-builds-note': 'builds_note',
            'helper-instance-label': 'instance', 'helper-copy-instance': 'copy',
            'helper-status-retry': 'retry', 'helper-settings-title': 'settings_title',
            'allow-helper-connection-label': 'allow', 'helper-setting-help': 'setting_help',
        };
        for (const [id, key] of Object.entries(labels)) {
            if (typeof t[key] === 'string') this.element(id).textContent = t[key];
        }
        this.element('helper-instance-url').value = this.instanceUrl;
        this.element('helper-copy-result').textContent = t[this.copyResult] || '';
        const checkbox = this.element('allow-helper-connection');
        checkbox.checked = this.saving && this.pendingChoice?.revision === this.gateRevision
            ? this.pendingChoice.allowed : this.allowed !== false;
        checkbox.disabled = this.allowed === null || this.saving || this.statusError;
        this.element('helper-setting-result').textContent = this.saving
            ? (t.saving || '') : this.saveError ? (t.save_error || '') : '';
        this.element('helper-gate-status').textContent = this.allowed === null
            ? '' : this.allowed ? (t.open || '') : (t.closed || '');
        this.element('helper-status-retry').hidden = !this.statusError;

        const session = this.data?.session;
        const ready = session?.state === 'ready';
        const needsRenewalLogin = ready && session.generation > 0 && this.data.renewal_available === false;
        const renewalRequiresLogin = this.data?.renewal_error && this.data.renewal_requires_login === true;
        this.element('helper-login-instructions').hidden = this.allowed === false
            && ready && !renewalRequiresLogin && !needsRenewalLogin;
        let message = t.waiting || '';
        if (this.statusError) message = t.status_error || '';
        else if (!this.data) message = t.checking || '';
        else if (ready && Number.isFinite(session.expires_at)) {
            message = (t.ready || '').replace('{expiry}', new Date(session.expires_at * 1000).toLocaleString());
        } else if (session?.state === 'expired') message = t.expired || '';
        else if (session?.error || session?.state === 'error') message = t.session_error || '';
        else if (this.login?.user_id && !session?.user_id) message = t.existing || '';
        this.element('helper-session-status').textContent = message;
        let renewal = '';
        if (this.data?.renewal_error) renewal = (renewalRequiresLogin ? t.renewal_error : t.renewal_retrying) || '';
        else if (needsRenewalLogin) renewal = t.renewal_unavailable || '';
        else if (ready && this.data?.renewal_available === true) renewal = t.renewal || '';
        this.element('helper-renewal-status').textContent = renewal;
    }

    updateLogin(data) {
        this.login = data;
        if (data.import_pending && !data.user_id) {
            this.revision++;
            if (this.data) {
                this.data = {...this.data, session: {...this.data.session, state: 'waiting', user_id: null}};
            }
        }
        this.render();
    }

    updateSettings(settings) {
        if (typeof settings.allow_helper_connection !== 'boolean') return;
        if (this.allowed !== settings.allow_helper_connection) this.saveError = false;
        this.allowed = settings.allow_helper_connection;
        this.gateRevision++;
        this.render();
    }

    updateStatus(data) {
        if (data?.enabled !== true || typeof data.allow_helper_connection !== 'boolean'
            || !data.session || typeof data.session.state !== 'string') {
            this.statusError = true;
            this.render();
            return;
        }
        if (this.allowed !== data.allow_helper_connection
            || this.data?.session.generation !== data.session.generation) this.saveError = false;
        this.data = data;
        this.allowed = data.allow_helper_connection;
        this.statusError = false;
        this.revision++;
        this.gateRevision++;
        if (data.session.state === 'ready' && Number.isInteger(data.session.user_id)
            && data.session.user_id > 0) {
            this.login = {user_id: data.session.user_id};
            this.onLogin(this.login);
        } else if (data.session.generation > 0 && data.session.state !== 'ready') {
            this.login = {user_id: null};
            this.onLogin(this.login);
        }
        this.render();
    }

    async load() {
        const revision = this.revision;
        const gateRevision = this.gateRevision;
        const request = ++this.request;
        try {
            const response = await this.fetcher('/api/session', {cache: 'no-store'});
            if (!response.ok) throw new Error('status');
            const data = await response.json();
            if (revision !== this.revision || request !== this.request) return;
            if (gateRevision !== this.gateRevision) data.allow_helper_connection = this.allowed;
            this.updateStatus(data);
        } catch (_) {
            if (revision !== this.revision || request !== this.request) return;
            this.statusError = true;
            this.render();
        }
    }

    async saveAllowed() {
        if (this.saving || this.allowed === null || this.statusError) return;
        const allowed = this.element('allow-helper-connection').checked;
        const gateRevision = this.gateRevision;
        this.saving = true;
        this.pendingChoice = {allowed, revision: gateRevision};
        this.saveError = false;
        this.render();
        try {
            const response = await this.fetcher('/api/settings', {
                method: 'POST', headers: {'Content-Type': 'application/json', 'X-TDM-Request': '1'},
                body: JSON.stringify({allow_helper_connection: allowed}),
            });
            const result = await response.json();
            if (!response.ok || result.success !== true
                || typeof result.settings?.allow_helper_connection !== 'boolean') {
                throw new Error('settings');
            }
            if (gateRevision === this.gateRevision) this.updateSettings(result.settings);
            await this.load();
        } catch (_) {
            this.saveError = true;
        } finally {
            this.saving = false;
            this.pendingChoice = null;
            this.render();
        }
    }

    async copyInstance() {
        try {
            if (!globalThis.navigator?.clipboard?.writeText) throw new Error('clipboard');
            await globalThis.navigator.clipboard.writeText(this.instanceUrl);
            this.copyResult = 'copied';
        } catch (_) {
            this.element('helper-instance-url').focus();
            this.element('helper-instance-url').select();
            this.copyResult = 'copy_manually';
        }
        this.render();
    }
}

window.HelperLoginPanel = HelperLoginPanel;
document.addEventListener('DOMContentLoaded', () => {
    window.helperLoginPanel = new HelperLoginPanel();
    if (state.login) window.helperLoginPanel.updateLogin(state.login);
    window.helperLoginPanel.updateSettings(state.settings);
    window.helperLoginPanel.load();
    setInterval(() => window.helperLoginPanel.load(), 15000);
});
