"""
CelesteOS Branded Status Window
================================
A single branded window (like Dropbox / Time Machine) that shows sync progress,
file activity, errors, and actions. Opens when the tray icon is clicked.

Uses pywebview + embedded HTML (same pattern as installer_ui.py).

Architecture:
    Menu bar icon (rumps) → click → pywebview status window (HTML/CSS/JS)
                                        → StatusAPI bridge → SyncStatus (shared)
"""

import json
import logging
import os
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger("agent.status_window")

# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

STATUS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CelesteOS</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0E1117;
    --surface: #161B22;
    --border: rgba(255,255,255,0.08);
    --txt: rgba(255,255,255,0.92);
    --txt2: rgba(255,255,255,0.55);
    --txt3: rgba(255,255,255,0.40);
    --teal: #5AABCC;
    --red: #C0503A;
    --green: #3A9D5C;
    --warn: #D4A843;
    --mono: 'SF Mono', 'Fira Code', 'Menlo', monospace;
  }

  body {
    font-family: -apple-system, 'Inter', BlinkMacSystemFont, sans-serif;
    background: var(--bg);
    color: var(--txt);
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
    overflow: hidden;
    user-select: none;
  }

  /* Header */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px 12px;
    border-bottom: 1px solid var(--border);
  }
  .header-left h1 {
    font-size: 16px;
    font-weight: 700;
    letter-spacing: -0.3px;
  }
  .header-left .yacht-name {
    font-size: 12px;
    color: var(--txt2);
    margin-top: 2px;
  }
  .state-badge {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 12px;
    background: var(--surface);
    border: 1px solid var(--border);
  }
  .state-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--txt3);
  }
  .state-dot.idle { background: var(--green); }
  .state-dot.syncing { background: var(--teal); animation: pulse 1.2s ease-in-out infinite; }
  .state-dot.error { background: var(--red); }
  .state-dot.paused { background: var(--warn); }
  .state-dot.starting { background: var(--txt3); animation: pulse 1.2s ease-in-out infinite; }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  /* Stats grid */
  .stats {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 1px;
    background: var(--border);
    border-bottom: 1px solid var(--border);
  }
  .stat {
    background: var(--bg);
    padding: 14px 20px;
    text-align: center;
  }
  .stat-value {
    font-size: 22px;
    font-weight: 700;
    font-family: var(--mono);
    line-height: 1;
  }
  .stat-value.error { color: var(--red); }
  .stat-label {
    font-size: 10px;
    color: var(--txt3);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-top: 4px;
  }

  /* Meta row */
  .meta {
    padding: 10px 20px;
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--txt3);
    font-family: var(--mono);
    border-bottom: 1px solid var(--border);
  }

  /* Activity list */
  .activity-header {
    padding: 12px 20px 8px;
    font-size: 11px;
    font-weight: 600;
    color: var(--txt2);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .activity-list {
    height: 200px;
    overflow-y: auto;
    padding: 0 12px;
  }
  .activity-list::-webkit-scrollbar { width: 4px; }
  .activity-list::-webkit-scrollbar-track { background: transparent; }
  .activity-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  .activity-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    min-height: 36px;
    border-radius: 6px;
    font-size: 12px;
    transition: background 0.1s;
  }
  .activity-row:hover { background: var(--surface); }
  .activity-time {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--txt3);
    flex-shrink: 0;
    width: 50px;
  }
  .activity-icon {
    flex-shrink: 0;
    width: 16px;
    text-align: center;
    font-size: 12px;
  }
  .activity-icon.synced { color: var(--green); }
  .activity-icon.failed { color: var(--red); }
  .activity-icon.pending { color: var(--warn); }
  .activity-file {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--txt2);
  }
  .activity-retry {
    font-size: 10px;
    color: var(--red);
    cursor: pointer;
    flex-shrink: 0;
    padding: 2px 6px;
    border-radius: 4px;
    border: 1px solid var(--red);
    background: transparent;
    transition: background 0.15s;
  }
  .activity-retry:hover { background: rgba(192,80,58,0.15); }

  .empty-state {
    text-align: center;
    padding: 40px 20px;
    color: var(--txt3);
    font-size: 12px;
  }

  /* Syncing current file */
  .current-file {
    padding: 8px 20px;
    font-size: 11px;
    font-family: var(--mono);
    color: var(--teal);
    border-bottom: 1px solid var(--border);
    display: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .current-file.active { display: block; }
  .current-file::before {
    content: '\\2191 ';
  }

  /* Action buttons */
  .actions {
    display: flex;
    gap: 8px;
    padding: 12px 20px 16px;
    border-top: 1px solid var(--border);
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    background: var(--bg);
  }
  .action-btn {
    flex: 1;
    padding: 9px 0;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--txt2);
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s;
    font-family: -apple-system, 'Inter', sans-serif;
  }
  .action-btn:hover {
    border-color: var(--teal);
    color: var(--txt);
  }
  .action-btn.primary {
    background: var(--teal);
    border-color: var(--teal);
    color: #fff;
  }
  .action-btn.primary:hover { opacity: 0.9; }
  .action-btn.warn {
    border-color: var(--warn);
    color: var(--warn);
  }
  .action-btn.warn:hover { background: rgba(212,168,67,0.1); }
</style>
</head>
<body>

  <!-- Header -->
  <div class="header">
    <div class="header-left">
      <h1>CelesteOS</h1>
      <div class="yacht-name" id="yacht-name">—</div>
    </div>
    <div class="state-badge">
      <div class="state-dot" id="state-dot"></div>
      <span id="state-label">Starting</span>
    </div>
  </div>

  <!-- Stats grid -->
  <div class="stats">
    <div class="stat">
      <div class="stat-value" id="stat-synced">0</div>
      <div class="stat-label">Synced</div>
    </div>
    <div class="stat">
      <div class="stat-value" id="stat-pending">0</div>
      <div class="stat-label">Pending</div>
    </div>
    <div class="stat">
      <div class="stat-value" id="stat-failed">0</div>
      <div class="stat-label">Failed</div>
    </div>
  </div>

  <!-- Meta -->
  <div class="meta">
    <span>Last sync: <span id="meta-last-sync">Never</span></span>
    <span>NAS: <span id="meta-nas">—</span></span>
  </div>

  <!-- Current file indicator -->
  <div class="current-file" id="current-file"></div>

  <!-- Recent activity -->
  <div class="activity-header">Recent Activity</div>
  <div class="activity-list" id="activity-list">
    <div class="empty-state">No file activity yet</div>
  </div>

  <!-- Action buttons -->
  <div class="actions">
    <button class="action-btn" onclick="doOpenNAS()">Open NAS</button>
    <button class="action-btn" onclick="doOpenLogs()">View Logs</button>
    <button class="action-btn warn" id="btn-pause" onclick="doTogglePause()">Pause Sync</button>
  </div>

<script>
  const ICONS = { synced: '\\u2713', failed: '\\u2717', pending: '\\u2026' };
  const STATE_LABELS = {
    starting: 'Starting',
    idle: 'Idle',
    syncing: 'Syncing',
    error: 'Error',
    paused: 'Paused',
  };

  function pyCall(method, ...args) {
    return window.pywebview.api[method](...args);
  }

  async function refresh() {
    try {
      const raw = await pyCall('get_status');
      const s = JSON.parse(raw);

      // State badge
      const dot = document.getElementById('state-dot');
      dot.className = 'state-dot ' + s.state;
      document.getElementById('state-label').textContent = STATE_LABELS[s.state] || s.state;

      // Yacht name
      document.getElementById('yacht-name').textContent = s.yacht_name || s.yacht_id || '—';

      // Stats
      document.getElementById('stat-synced').textContent = s.files_synced;
      document.getElementById('stat-pending').textContent = s.files_pending;
      const failedEl = document.getElementById('stat-failed');
      failedEl.textContent = s.files_failed;
      failedEl.className = 'stat-value' + (s.files_failed > 0 ? ' error' : '');

      // Meta
      document.getElementById('meta-last-sync').textContent = s.last_sync;
      const nasPath = s.nas_root || '—';
      document.getElementById('meta-nas').textContent =
        nasPath.length > 28 ? '...' + nasPath.slice(-25) : nasPath;

      // Current file
      const cfEl = document.getElementById('current-file');
      if (s.state === 'syncing' && s.current_file) {
        cfEl.textContent = s.current_file;
        cfEl.classList.add('active');
      } else {
        cfEl.classList.remove('active');
      }

      // Pause button
      const pauseBtn = document.getElementById('btn-pause');
      if (s.is_paused) {
        pauseBtn.textContent = 'Resume';
        pauseBtn.className = 'action-btn primary';
      } else {
        pauseBtn.textContent = 'Pause Sync';
        pauseBtn.className = 'action-btn warn';
      }

      // Activity list
      renderActivity(s.recent_activity || []);

    } catch (e) {
      // pywebview not ready yet — retry
    }
  }

  function renderActivity(items) {
    const list = document.getElementById('activity-list');
    if (!items.length) {
      list.innerHTML = '<div class="empty-state">No file activity yet</div>';
      return;
    }

    // Reverse so newest is on top
    const reversed = items.slice().reverse();
    let html = '';
    for (const item of reversed) {
      const icon = ICONS[item.status] || '?';
      const shortFile = item.filename.length > 45
        ? '...' + item.filename.slice(-42)
        : item.filename;
      const retryBtn = item.status === 'failed'
        ? '<button class="activity-retry" onclick="doRetry()">retry</button>'
        : '';
      html += '<div class="activity-row">'
        + '<span class="activity-time">' + item.time + '</span>'
        + '<span class="activity-icon ' + item.status + '">' + icon + '</span>'
        + '<span class="activity-file" title="' + item.filename + '">' + shortFile + '</span>'
        + retryBtn
        + '</div>';
    }
    list.innerHTML = html;
  }

  async function doOpenNAS() { await pyCall('open_nas'); }
  async function doOpenLogs() { await pyCall('open_logs'); }
  async function doTogglePause() { await pyCall('toggle_pause'); refresh(); }
  async function doRetry() { await pyCall('retry_failed'); }

  // Auto-refresh every 2 seconds
  setInterval(refresh, 2000);

  // Initial load (wait for pywebview bridge)
  window.addEventListener('pywebviewready', refresh);
  setTimeout(refresh, 500);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Python API bridge — exposed to JavaScript via pywebview
# ---------------------------------------------------------------------------

class StatusAPI:
    """
    Bridge between the HTML status window and the Python daemon.
    Methods are called from JavaScript via window.pywebview.api.
    """

    def get_status(self) -> str:
        """Return a JSON snapshot of the current sync status."""
        from .status_tray import sync_status
        return json.dumps(sync_status.snapshot())

    def open_nas(self) -> str:
        """Open the NAS root folder in Finder."""
        from .status_tray import sync_status
        snap = sync_status.snapshot()
        nas = snap["nas_root"]
        if nas and os.path.isdir(nas):
            subprocess.run(["open", nas], capture_output=True)
            return json.dumps({"ok": True})
        return json.dumps({"ok": False, "error": "NAS folder not found"})

    def open_logs(self) -> str:
        """Open the log directory in Finder."""
        log_dir = Path.home() / ".celesteos" / "logs"
        if log_dir.is_dir():
            subprocess.run(["open", str(log_dir)], capture_output=True)
        else:
            # Create the dir so user sees it
            log_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(["open", str(log_dir)], capture_output=True)
        return json.dumps({"ok": True})

    def toggle_pause(self) -> str:
        """Toggle the pause state."""
        from .status_tray import sync_status
        with sync_status._lock:
            sync_status.is_paused = not sync_status.is_paused
            if sync_status.is_paused:
                sync_status.state = "paused"
            else:
                sync_status.state = "idle"
        logger.info("Sync %s", "paused" if sync_status.is_paused else "resumed")
        return json.dumps({"paused": sync_status.is_paused})

    def retry_failed(self) -> str:
        """Clear errors and trigger a retry on next cycle."""
        from .status_tray import sync_status
        sync_status.clear_errors()
        logger.info("Retry requested — errors cleared, will retry on next cycle")
        return json.dumps({"ok": True})


# ---------------------------------------------------------------------------
# Window management
# ---------------------------------------------------------------------------

_window = None
_window_lock = threading.Lock()


def toggle_status_window():
    """Open the status window, or focus it if already open."""
    global _window

    with _window_lock:
        if _window is not None:
            try:
                # Window exists — try to bring to front
                _window.show()
                _window.restore()
                return
            except Exception:
                # Window was destroyed
                _window = None

    # Open in a new thread so we don't block rumps
    thread = threading.Thread(target=_open_window, daemon=True, name="status-window")
    thread.start()


def _open_window():
    """Create and show the status window (blocking until closed)."""
    global _window
    try:
        import webview
    except ImportError:
        logger.warning("pywebview not installed — cannot open status window")
        return

    api = StatusAPI()
    _window = webview.create_window(
        "CelesteOS",
        html=STATUS_HTML,
        js_api=api,
        width=420,
        height=560,
        resizable=False,
        background_color="#0E1117",
        on_top=False,
    )

    webview.start(debug=False)

    # Window was closed
    with _window_lock:
        _window = None


def close_status_window():
    """Destroy the status window if open."""
    global _window
    with _window_lock:
        if _window is not None:
            try:
                _window.destroy()
            except Exception:
                pass
            _window = None


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time
    from datetime import datetime
    logging.basicConfig(level=logging.INFO)

    # Populate mock data for testing
    from .status_tray import sync_status

    sync_status.yacht_name = "M/Y Freedom"
    sync_status.yacht_id = "test-123"
    sync_status.nas_root = "/Volumes/YachtNAS"
    sync_status.state = "idle"
    sync_status.files_synced = 142
    sync_status.files_pending = 3
    sync_status.files_failed = 0
    sync_status.last_sync = datetime.now()

    # Add some mock activity
    sync_status.add_activity("Engine/CAT_C32_Manual.pdf", "synced")
    sync_status.add_activity("Safety/Fire_System.pdf", "synced")
    sync_status.add_activity("Deck/Gangway_Manual.pdf", "synced")
    sync_status.add_activity("Certs/SOLAS_2024.pdf", "failed")
    sync_status.add_activity("Nav/Radar_Config.pdf", "synced")

    sync_status.add_error("Certs/SOLAS_2024.pdf: upload timeout after 30s")

    print("Opening status window with mock data...")
    _open_window()
