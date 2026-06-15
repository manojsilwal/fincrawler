"""Browser stealth injection — masks headless automation signals."""

from __future__ import annotations

from app.services.crawler.fingerprints import (
    fingerprint_stealth_overrides_js,
    fingerprint_to_context_kwargs,
    pick_fingerprint,
)

STEALTH_LAUNCH_ARGS: dict = {
    "headless": True,
    "args": [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1920,1080",
        "--disable-infobars",
        "--disable-extensions",
        "--no-default-browser-check",
        "--no-first-run",
        "--ignore-certificate-errors",
        "--disable-breakpad",
    ],
}

_STEALTH_JS = r"""
(function () {
    try { Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true }); } catch(_) {}
    try {
        const fakePlugins = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
        ];
        Object.defineProperty(navigator, 'plugins', {
            get: () => Object.assign(fakePlugins, {
                item: i => fakePlugins[i],
                namedItem: n => fakePlugins.find(p => p.name === n) || null,
                refresh: () => {},
                length: fakePlugins.length,
            }),
            configurable: true,
        });
    } catch(_) {}
    try { Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'], configurable: true }); } catch(_) {}
    try {
        if (!window.chrome) {
            window.chrome = { app: { isInstalled: false }, runtime: {}, loadTimes: () => ({}), csi: () => ({}) };
        }
    } catch(_) {}
    try {
        const orig = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = p =>
            p && p.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission, onchange: null })
                : orig(p);
    } catch(_) {}
    try {
        window.outerHeight = window.innerHeight + 100;
        window.outerWidth = window.innerWidth;
    } catch(_) {}
})();
"""


def get_stealth_context_kwargs(retailer_key: str = "") -> dict:
    fp = pick_fingerprint(retailer_key)
    kwargs = fingerprint_to_context_kwargs(fp)
    kwargs.update(
        {
            "java_script_enabled": True,
            "bypass_csp": True,
            "ignore_https_errors": True,
        }
    )
    return kwargs


async def apply_stealth(page, retailer_key: str = "") -> None:
    fp = pick_fingerprint(retailer_key)
    await page.add_init_script(_STEALTH_JS + fingerprint_stealth_overrides_js(fp))
