"""The shell's only Python<->JS surface: the updater bridge + page bootstrap.

`UpdateBridge` is the pywebview `js_api` object. pywebview exposes every
public (non-underscore) member to the page — attributes included, walked
recursively — so all mutable state lives on underscore-prefixed attributes
and only the `update_*` methods are meaningfully page-callable. Methods never
raise (js_api exceptions become page-side promise rejections; the page wants
plain `{ok, message}` answers).

Rendering is one-directional push: Python state changes -> `push_state` ->
`window.__dshUpdate.applyState(...)`. `BOOTSTRAP_JS` is re-injected on every
`loaded` event (pywebview rebuilds `window.pywebview` on each navigation), is
idempotent, and re-renders from whatever state is pushed next — a push lost
to a navigation race heals when app.py re-pushes after re-injection.
"""

from __future__ import annotations

import json
import webbrowser
from typing import Any, Callable

from .log import log
from .updater import Updater, release_page_url


class UpdateBridge:
    """Page-callable updater API (`window.pywebview.api.update_*`)."""

    def __init__(self) -> None:
        self._window: Any = None
        self._updater: Updater | None = None
        self._request_exit: Callable[[str], None] | None = None
        self._show_window: Callable[[], None] | None = None

    def bind(
        self,
        window: Any,
        updater: Updater,
        request_exit: Callable[[str], None],
        show_window: Callable[[], None],
    ) -> None:
        """Wire the bridge to its collaborators (app.run owns the lifecycle)."""
        self._window = window
        self._updater = updater
        self._request_exit = request_exit
        self._show_window = show_window

    def update_check(self, manual: bool = True) -> dict:
        """Manual check (tray menu / settings row). Auto checks call Updater directly.

        Raises the window first if it's parked in the tray, so the toast/modal
        is actually visible.
        """
        try:
            if manual and self._show_window is not None:
                try:
                    self._show_window()
                except Exception:  # noqa: BLE001 — visibility is best-effort
                    pass
            if self._updater is None:
                return {"ok": False, "message": "updater not ready"}
            self._updater.check(bool(manual))
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}

    def update_choose(self, action: str) -> dict:
        """`install` / `later` / `never` from the modal's buttons."""
        try:
            if self._updater is None:
                return {"ok": False, "message": "updater not ready"}
            self._updater.choose(str(action))
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}

    def update_open_page(self) -> dict:
        """Open the release page in the user's browser (escape hatch)."""
        try:
            state = self._updater.state() if self._updater is not None else {}
            webbrowser.open(release_page_url(state.get("tag")))
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}


def push_state(window: Any, state: dict) -> None:
    """Fire-and-forget state push into the page. Never raises.

    evaluate_js races a navigation by blocking ~20s then raising; the next
    `loaded` re-injects BOOTSTRAP_JS and re-pushes current state, so a
    dropped push is self-healing. Pushes come from updater worker threads,
    so even the slow-failure path never blocks the UI thread.
    """
    payload = json.dumps(state, ensure_ascii=False)
    try:
        window.evaluate_js(f"window.__dshUpdate && window.__dshUpdate.applyState({payload})")
    except Exception as exc:  # noqa: BLE001
        log(f"==> updater: state push deferred (page navigating): {exc}")


# Injected on every `loaded` event. Palette mirrors _SPLASH_TMPL in app.py so
# the prompt reads native to the shell. Dynamic values (versions, sizes,
# error text) enter the DOM via textContent only — never innerHTML.
BOOTSTRAP_JS = r"""
(function () {
  'use strict';
  if (window.__dshUpdate) return;
  if (!document.body) return;

  var C = {
    card: '#161c28', border: '#2a3448', text: '#c8d2e4', muted: '#a9b4c8',
    accent: '#8fb7ff', warm: '#f0b37e', errBg: '#3a2320', errBorder: '#8f5a52',
    okBg: '#1d2a22', okBorder: '#4f7d63'
  };
  var FONT = '"Segoe UI",system-ui,sans-serif';
  var S = null, root = null, card = null, toast = null, hideTimer = null;

  function h(tag, css, text) {
    var e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (text != null) e.textContent = text;
    return e;
  }

  function ensureRoot() {
    if (root && root.isConnected) return;
    root = h('div',
      'position:fixed;left:0;right:0;top:0;bottom:0;display:none;' +
      'align-items:center;justify-content:center;z-index:2147483000;' +
      'background:rgba(6,9,14,0.55);font-family:' + FONT);
    card = h('div',
      'background:' + C.card + ';border:1px solid ' + C.border +
      ';border-radius:12px;padding:22px 24px;width:min(440px,88vw);' +
      'box-shadow:0 12px 40px rgba(0,0,0,0.45);color:' + C.text);
    root.appendChild(card);
    document.body.appendChild(root);
  }

  function ensureToast() {
    if (toast && toast.isConnected) return;
    toast = h('div',
      'position:fixed;right:18px;bottom:18px;z-index:2147483600;display:none;' +
      'max-width:340px;padding:10px 14px;border-radius:10px;' +
      'border:1px solid ' + C.border + ';background:' + C.card +
      ';color:' + C.text + ';font-size:13px;font-family:' + FONT +
      ';box-shadow:0 8px 28px rgba(0,0,0,0.4)');
    document.body.appendChild(toast);
  }

  function showToast(text, kind) {
    ensureToast();
    toast.textContent = text;
    toast.style.background = kind === 'err' ? C.errBg : (kind === 'ok' ? C.okBg : C.card);
    toast.style.borderColor = kind === 'err' ? C.errBorder : (kind === 'ok' ? C.okBorder : C.border);
    toast.style.display = 'block';
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(function () { toast.style.display = 'none'; }, 4500);
  }

  function titleEl(text) {
    return h('div',
      'font-size:15px;font-weight:600;color:' + C.accent + ';margin-bottom:10px;', text);
  }

  function button(label, primary, onClick) {
    return h('button',
      'font-family:inherit;font-size:13px;padding:7px 16px;border-radius:8px;' +
      'cursor:pointer;border:1px solid ' + (primary ? C.accent : C.border) + ';' +
      (primary
        ? 'background:' + C.accent + ';color:#0d1220;font-weight:600;'
        : 'background:transparent;color:' + C.muted + ';'),
      label);
  }

  function callApi(method, arg) {
    var api = window.pywebview && window.pywebview.api;
    if (!api || typeof api[method] !== 'function') return;
    try { api[method](arg).catch(function () {}); } catch (e) { /* bridge rebuilding */ }
  }

  function fmtMB(n) { return (n / 1048576).toFixed(1) + ' MB'; }

  function renderAvailable(st) {
    card.appendChild(titleEl('发现新版本 · Update available'));
    var line = h('div', 'font-size:13px;line-height:1.7;color:' + C.text);
    line.appendChild(h('span', null, '新版本 '));
    line.appendChild(h('span', 'color:' + C.accent + ';font-weight:600;', st.version || st.tag || ''));
    if (st.current) line.appendChild(h('span', 'color:' + C.muted, '（当前 ' + st.current + '）'));
    if (st.asset_size) line.appendChild(h('span', 'color:' + C.muted, ' · ' + fmtMB(st.asset_size)));
    card.appendChild(line);
    if (st.suppressed) {
      card.appendChild(h('div',
        'font-size:12px;color:' + C.warm + ';margin-top:8px;',
        '已对 ' + (st.tag || st.version || '') +
        ' 选择“不再提醒”，此为手动检查结果'));
    }
    var row = h('div', 'display:flex;gap:10px;margin-top:16px;align-items:center;flex-wrap:wrap;');
    row.appendChild(button('更新', true, function () { callApi('update_choose', 'install'); }));
    row.appendChild(button('暂不', false, function () { callApi('update_choose', 'later'); }));
    row.appendChild(button('不再提醒', false, function () { callApi('update_choose', 'never'); }));
    card.appendChild(row);
    var link = h('a',
      'display:inline-block;margin-top:14px;font-size:12px;color:' + C.muted +
      ';cursor:pointer;text-decoration:underline;',
      '查看发布页 · Release page');
    link.onclick = function () { callApi('update_open_page'); };
    card.appendChild(link);
  }

  function renderDownloading(st) {
    card.appendChild(titleEl('下载更新 · Downloading update'));
    var track = h('div',
      'height:8px;border-radius:6px;background:' + C.border +
      ';margin-top:6px;overflow:hidden;position:relative;');
    var fill = h('div', 'height:100%;width:0%;background:' + C.accent +
      ';border-radius:6px;transition:width 0.25s;');
    track.appendChild(fill);
    card.appendChild(track);
    var label = h('div', 'font-size:12px;color:' + C.muted + ';margin-top:8px;');
    card.appendChild(label);
    if (st.total) {
      var pct = Math.max(0, Math.min(100, (st.received / st.total) * 100));
      fill.style.width = pct.toFixed(1) + '%';
      label.textContent = fmtMB(st.received) + ' / ' + fmtMB(st.total) +
        '（' + Math.round(pct) + '%）';
    } else {
      fill.style.width = '35%';
      fill.style.position = 'absolute';
      fill.style.animation = 'dshIndet 1.2s linear infinite';
      label.textContent = '已下载 ' + fmtMB(st.received) + ' · 大小未知';
    }
    card.appendChild(h('div',
      'font-size:12px;color:' + C.muted + ';margin-top:12px;',
      '下载完成后将自动安装并重启'));
  }

  function renderApplying() {
    card.appendChild(titleEl('正在安装更新 · Installing update'));
    card.appendChild(h('div',
      'font-size:13px;line-height:1.7;color:' + C.text + ';margin-top:4px;',
      '更新包已就绪，应用即将退出并自动重启…'));
  }

  function applyState(st) {
    S = st;
    var p = st.phase || 'idle';
    if (p === 'available' || p === 'downloading' || p === 'applying') {
      ensureRoot();
      root.style.display = 'flex';
      card.textContent = '';
      if (p === 'available') renderAvailable(st);
      else if (p === 'downloading') renderDownloading(st);
      else renderApplying(st);
    } else if (root) {
      root.style.display = 'none';
    }
    if (p === 'uptodate') {
      showToast('已是最新版本 · You are up to date', 'ok');
    } else if (p === 'error') {
      showToast('检查/下载失败 · ' + (st.message || 'unknown error'), 'err');
    } else if (p === 'checking' && st.manual) {
      showToast('正在检查更新 · Checking…', 'info');
    }
  }

  window.__dshUpdate = { applyState: applyState, getState: function () { return S; } };

  // ---- settings-row injection (best-effort, Vue-tolerant) -----------------
  // Locate the settings dialog by the '通用设置' button, append a plain
  // "软件更新 · 检查更新" row. Vue may remove our unmanaged node on re-render;
  // the observer re-inserts. Silent no-op when no settings dialog is open.

  function callManualCheck(ev) {
    if (ev) { ev.preventDefault(); ev.stopPropagation(); }
    callApi('update_check', true);
  }

  function buildRow() {
    var row = h('div',
      'display:flex;align-items:center;justify-content:space-between;gap:12px;' +
      'padding:10px 12px;margin-top:8px;border:1px solid ' + C.border +
      ';border-radius:8px;background:rgba(143,183,255,0.04);font-family:' + FONT +
      ';font-size:13px;color:' + C.text);
    row.setAttribute('data-dsh-update-row', '1');
    row.appendChild(h('span', null, '软件更新 · Updates'));
    var btn = button('检查更新', false, callManualCheck);
    row.appendChild(btn);
    return row;
  }

  function ensureSettingsRow() {
    var dialogs = document.querySelectorAll('[role="dialog"]');
    for (var i = 0; i < dialogs.length; i++) {
      var dlg = dialogs[i];
      var buttons = dlg.querySelectorAll('button');
      var isSettings = false;
      for (var j = 0; j < buttons.length; j++) {
        var t = buttons[j].textContent;
        if (t && t.trim() === '通用设置') { isSettings = true; break; }
      }
      if (!isSettings) continue;
      if (dlg.querySelector('[data-dsh-update-row]')) return true;
      dlg.appendChild(buildRow());
      return true;
    }
    return false;
  }

  if (!document.getElementById('dsh-update-style')) {
    var styleEl = document.createElement('style');
    styleEl.id = 'dsh-update-style';
    styleEl.textContent = '@keyframes dshIndet{0%{left:-35%}100%{left:105%}}';
    (document.head || document.documentElement).appendChild(styleEl);
  }

  var mo = new MutationObserver(function () {
    if (document.querySelector('[role="dialog"]')) ensureSettingsRow();
  });
  mo.observe(document.body, { childList: true, subtree: true });
  setTimeout(ensureSettingsRow, 800);
})();
"""
