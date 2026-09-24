/* Private session files are sent once; never stored in browser storage or logs. */
class SessionImportPanel {
    constructor(doc = document, fetcher = fetch, translations = () => state.translations.gui?.session_import || {}) {
        this.doc = doc;
        this.fetcher = fetcher;
        this.translations = translations;
        this.data = null;
        this.download = data => {
            const url = URL.createObjectURL(new Blob([JSON.stringify(data)], {type: 'application/json'}));
            const link = this.doc.createElement('a');
            link.href = url;
            link.download = 'tdm-connection.json';
            this.doc.body.appendChild(link);
            try { link.click(); } finally { link.remove(); URL.revokeObjectURL(url); }
        };
        this.busy = false;
        this.error = '';
        this.doc.getElementById('session-import-button').addEventListener('click', () => this.submit());
        this.doc.getElementById('session-import-pair').addEventListener('click', () => this.manage('pair'));
        this.doc.getElementById('session-import-revoke').addEventListener('click', () => this.manage('revoke'));
    }

    render() {
        const t = this.translations();
        const element = id => this.doc.getElementById(id);
        element('session-import-panel').hidden = this.data?.enabled === false;
        for (const [id, key] of [['title', 'title'], ['prompt', 'prompt'], ['file-label', 'file'], ['button', 'button'], ['pair', 'pair'], ['revoke', 'revoke'], ['pair-hint', 'pair_hint']]) {
            element(`session-import-${id}`).textContent = t[key] || '';
        }
        const disabled = this.busy || !this.data?.enabled || this.data?.authentication_required;
        element('session-import-button').disabled = Boolean(disabled);
        element('session-import-file').disabled = Boolean(disabled);
        let text = t.waiting || '';
        const session = this.data?.session;
        element('session-import-pair').disabled = Boolean(disabled || session?.state !== 'ready');
        element('session-import-revoke').disabled = Boolean(disabled || !session?.paired);
        element('session-import-renewal-status').textContent = session?.paired ? (t.paired || '') : (t.unpaired || '');
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

    async manage(action) {
        if (this.busy || !this.data?.enabled || this.data.authentication_required) return;
        if (action === 'pair' && this.data.session?.state !== 'ready') return;
        if (action === 'revoke' && !this.data.session?.paired) return;
        if (!['pair', 'revoke'].includes(action)) return;
        this.busy = true;
        this.error = '';
        this.render();
        try {
            const response = await this.fetcher(`/api/session/${action}`, {
                method: 'POST', headers: {'X-TDM-Request': '1'},
            });
            const result = await response.json();
            if (!response.ok) throw new Error('connection_failed');
            if (action === 'pair') {
                this.download(result);
                this.data.session.paired = true;
            } else {
                this.data.session = result.session;
            }
        } catch (_) {
            this.error = 'connection_failed';
        } finally {
            this.busy = false;
            this.render();
        }
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
