/* Only loaded in TDM's temporary interactive profile. Never export cookie values. */
// Non-Secure cookie events use HTTP permission URLs; cookie lookups still use HTTPS.
let finishing = false;
function complete(cookie) {
    if (finishing || !cookie || cookie.name !== 'auth-token'
        || !['.twitch.tv', 'twitch.tv', 'www.twitch.tv'].includes(cookie.domain)
        || !cookie.value) return;
    finishing = true;
    // Let the sign-in response finish before normal window closure flushes storage.
    setTimeout(async () => {
        try {
            const current = await chrome.cookies.get({url: 'https://www.twitch.tv/', name: 'auth-token'});
            if (current?.value) {
                for (const window of await chrome.windows.getAll()) {
                    await chrome.windows.remove(window.id);
                }
            }
        } catch (_) {
            // Manual Finish remains available; never log browser or cookie details.
        } finally {
            finishing = false;
        }
    }, 2000);
}
chrome.cookies.onChanged.addListener(change => {
    if (!change.removed) complete(change.cookie);
});
chrome.cookies.get({url: 'https://www.twitch.tv/', name: 'auth-token'}).then(complete).catch(() => {});
