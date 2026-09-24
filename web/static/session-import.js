/* Private session files are sent once; never stored in browser storage or logs. */
class SessionImportPanel {
    constructor(doc = document, fetcher = fetch, translations = () => state.translations.gui?.session_import || {}) {
        this.doc = doc;
        this.fetcher = fetcher;
        this.translations = translations;
        this.data = null;
        this.busy = false;
        this.error = '';
        this.doc.getElementById('session-import-button').addEventListener('click', () => this.submit());
    }

    render() {
        const t = this.translations();
        const element = id => this.doc.getElementById(id);
        element('session-import-panel').hidden = this.data?.enabled === false;
        for (const [id, key] of [['title', 'title'], ['prompt', 'prompt'], ['file-label', 'file'], ['button', 'button']]) {
            element(`session-import-${id}`).textContent = t[key] || '';
        }
        const disabled = this.busy || !this.data?.enabled || this.data?.authentication_required;
        element('session-import-button').disabled = Boolean(disabled);
        element('session-import-file').disabled = Boolean(disabled);
        let text = t.waiting || '';
        const session = this.data?.session;
        if (this.error) text = (t.error || '').replace('{code}', this.error);
        else if (this.busy) text = t.checking || '';
        else if (this.data?.authentication_required) text = t.auth_required || '';
        else if (session?.state === 'ready' && Number.isFinite(session.expires_at)) {
            text = (t.ready || '').replace('{expiry}', new Date(session.expires_at * 1000).toLocaleString());
        } else if (session?.state === 'expired') text = t.expired || '';
        element('session-import-status').textContent = text;
    }

    async load() {
        if (this.busy) return;
        try {
            const response = await this.fetcher('/api/session', {cache: 'no-store'});
            if (!response.ok) throw new Error('status');
            const data = await response.json();
            if (typeof data.enabled !== 'boolean') throw new Error('status');
            this.data = data;
            if (this.error === 'status_unavailable') this.error = '';
        } catch (_) {
            this.error = 'status_unavailable';
        }
        this.render();
    }

    async submit() {
        if (this.busy || !this.data?.enabled || this.data.authentication_required) return;
        const input = this.doc.getElementById('session-import-file');
        const file = input.files[0];
        this.error = '';
        if (!file || file.size > 65536 || file.size === 0) {
            this.error = 'invalid_file';
            input.value = '';
            this.render();
            return;
        }
        this.busy = true;
        this.render();
        try {
            const response = await this.fetcher('/api/session/import', {
                method: 'POST', headers: {'Content-Type': 'application/json', 'X-TDM-Request': '1'},
                body: await file.text(),
            });
            const result = await response.json();
            if (!response.ok || result.success !== true) {
                this.error = /^session_[a-z_]{1,32}$/.test(result.detail) ? result.detail : 'upload_failed';
            } else {
                this.data.session = result.session;
            }
        } catch (_) {
            this.error = 'upload_failed';
        } finally {
            input.value = '';
            this.busy = false;
            this.render();
        }
    }
}

window.SessionImportPanel = SessionImportPanel;
document.addEventListener('DOMContentLoaded', () => {
    window.sessionImportPanel = new SessionImportPanel();
    window.sessionImportPanel.load();
    setInterval(() => window.sessionImportPanel.load(), 15000);
});
