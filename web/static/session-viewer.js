import UI from '/static/novnc-login-v2/app/ui.js';

// Override clipboard handlers before noVNC registers listeners. Never log clipboard data.
const field = () => document.getElementById('noVNC_clipboard_text');
const clear = () => { field().value = ''; };
const close = UI.closeClipboardPanel;
UI.closeClipboardPanel = () => { clear(); close(); };
UI.clipboardReceive = event => {
    if (!document.getElementById('noVNC_clipboard').classList.contains('noVNC_open')) return;
    const text = event.detail?.text;
    if (typeof text === 'string' && text.length <= 65536) field().value = text;
};
UI.clipboardSend = () => {
    if (!UI.connected) return;
    const text = field().value;
    clear();
    if (text && text.length <= 65536) UI.rfb.clipboardPasteFrom(text);
};
for (const [method, event] of Object.entries({
    connectFinished: 'connect', disconnectFinished: 'disconnect', securityFailed: 'securityfailure',
})) {
    const original = UI[method];
    UI[method] = (...args) => {
        if (event !== 'connect') clear();
        original(...args);
        if (event === 'connect') UI.openControlbar();
        parent.postMessage({type: 'tdm-viewer', event}, location.origin);
    };
}
window.addEventListener('pagehide', clear);
await UI.start({settings: {mandatory: {
    host: location.hostname, port: location.port || (location.protocol === 'https:' ? '443' : '80'),
    encrypt: location.protocol === 'https:', path: 'api/session/vnc',
    autoconnect: true, reconnect: false, resize: 'scale', logging: 'none',
}}});
field().maxLength = 65536;
UI.openControlbar();
