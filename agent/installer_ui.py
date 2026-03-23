"""
CelesteOS Installer UI
=======================
Native macOS window wrapping a local HTML UI for the first-launch experience.
Uses pywebview to render a branded setup wizard.

Flow:
    Step 1: Welcome + Register → sends registration request, triggers 2FA email
    Step 2: Enter 2FA code → verifies code, receives shared_secret
    Step 3: Select NAS folder → saves to ~/.celesteos/.env.local
    Step 4: Success → shows confirmation, starts sync

Design:
    - Dark theme matching Celeste design tokens
    - Teal (#5AABCC) for interactive elements
    - Inter font for human text
    - Functional MVP — correctness over polish
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agent.installer_ui")


# ---------------------------------------------------------------------------
# HTML Template — complete single-page app
# ---------------------------------------------------------------------------

INSTALLER_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CelesteOS Setup</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --mark: #5AABCC;
    --teal-bg: rgba(58,124,157,0.13);
    --bg: #0E1117;
    --surface: #161B22;
    --border: rgba(255,255,255,0.08);
    --txt: rgba(255,255,255,0.92);
    --txt2: rgba(255,255,255,0.55);
    --txt3: rgba(255,255,255,0.40);
    --red: #C0503A;
    --green: #3A9D5C;
  }

  body {
    font-family: -apple-system, 'Inter', BlinkMacSystemFont, sans-serif;
    background: var(--bg);
    color: var(--txt);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 32px;
    -webkit-font-smoothing: antialiased;
  }

  .container { width: 100%; max-width: 420px; }

  .logo { font-size: 22px; font-weight: 700; margin-bottom: 4px; letter-spacing: -0.3px; }
  .subtitle { color: var(--txt2); font-size: 13px; margin-bottom: 28px; }

  .step { display: none; }
  .step.active { display: block; }

  .card {
    background: var(--surface);
    border-radius: 10px;
    padding: 28px;
    border: 1px solid var(--border);
  }

  .card h2 { font-size: 16px; font-weight: 600; margin-bottom: 6px; }
  .card p { color: var(--txt2); font-size: 13px; line-height: 1.5; margin-bottom: 16px; }

  .yacht-name { color: var(--mark); font-weight: 600; }
  .email-masked { color: var(--mark); }

  label { display: block; font-size: 12px; color: var(--txt3); margin-bottom: 5px; text-transform: uppercase; letter-spacing: 0.5px; }

  input[type="text"] {
    width: 100%; padding: 10px 12px; border-radius: 6px;
    border: 1px solid var(--border); background: var(--bg);
    color: var(--txt); font-size: 15px; outline: none;
    transition: border-color 0.15s;
  }
  input:focus { border-color: var(--mark); }

  .code-input {
    text-align: center; font-size: 26px; letter-spacing: 8px;
    font-weight: 600; font-family: 'SF Mono', 'Fira Code', monospace;
  }

  .btn {
    width: 100%; padding: 11px; border-radius: 6px; border: none;
    background: var(--mark); color: #fff; font-size: 14px; font-weight: 600;
    cursor: pointer; transition: opacity 0.15s; margin-top: 12px;
  }
  .btn:hover { opacity: 0.9; }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }

  .btn-folder {
    background: var(--surface); border: 1px solid var(--border);
    color: var(--txt); text-align: left; padding: 14px;
    font-size: 13px; margin-bottom: 8px; cursor: pointer;
    border-radius: 6px; width: 100%;
  }
  .btn-folder:hover { border-color: var(--mark); background: var(--teal-bg); }
  .btn-folder .path { display: block; color: var(--mark); font-family: monospace; font-size: 12px; margin-top: 4px; }

  .btn-browse { background: transparent; border: 1px dashed var(--border); color: var(--txt2); }
  .btn-browse:hover { border-color: var(--mark); color: var(--txt); }

  .msg { font-size: 12px; min-height: 18px; margin-top: 8px; }
  .msg.error { color: var(--red); }
  .msg.success { color: var(--green); }

  .progress {
    display: flex; align-items: center; gap: 8px; margin-bottom: 20px;
  }
  .progress .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--border);
  }
  .progress .dot.done { background: var(--green); }
  .progress .dot.current { background: var(--mark); }
  .progress .line { flex: 1; height: 1px; background: var(--border); }

  .success-icon {
    font-size: 48px; text-align: center; margin: 16px 0;
  }

  .spinner {
    display: inline-block; width: 14px; height: 14px;
    border: 2px solid rgba(255,255,255,0.2); border-top-color: #fff;
    border-radius: 50%; animation: spin 0.5s linear infinite;
    vertical-align: middle; margin-right: 6px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="container">
  <div class="logo">CelesteOS</div>
  <div class="subtitle">Setup</div>

  <!-- Step 1: Welcome + Register -->
  <div class="step active" id="step-welcome">
    <div class="progress">
      <div class="dot current"></div><div class="line"></div>
      <div class="dot"></div><div class="line"></div>
      <div class="dot"></div><div class="line"></div>
      <div class="dot"></div>
    </div>
    <div class="card">
      <h2>Welcome</h2>
      <p>This installer will activate CelesteOS for your yacht and connect it to your document storage.</p>
      <p>Yacht: <span class="yacht-name" id="yacht-name-display">Loading...</span></p>
      <button class="btn" id="btn-register" onclick="doRegister()">Begin setup</button>
      <div class="msg" id="msg-register"></div>
    </div>
  </div>

  <!-- Step 2: Enter 2FA Code -->
  <div class="step" id="step-2fa">
    <div class="progress">
      <div class="dot done"></div><div class="line"></div>
      <div class="dot current"></div><div class="line"></div>
      <div class="dot"></div><div class="line"></div>
      <div class="dot"></div>
    </div>
    <div class="card">
      <h2>Verify your identity</h2>
      <p>A 6-digit code has been sent to <span class="email-masked" id="email-display"></span></p>
      <label for="code-input">Verification code</label>
      <input type="text" id="code-input" class="code-input" maxlength="6"
             placeholder="000000" inputmode="numeric" pattern="[0-9]*"
             autocomplete="one-time-code">
      <button class="btn" id="btn-verify" onclick="doVerify()">Verify</button>
      <div class="msg" id="msg-verify"></div>
    </div>
  </div>

  <!-- Step 3: Select Folder -->
  <div class="step" id="step-folder">
    <div class="progress">
      <div class="dot done"></div><div class="line"></div>
      <div class="dot done"></div><div class="line"></div>
      <div class="dot current"></div><div class="line"></div>
      <div class="dot"></div>
    </div>
    <div class="card">
      <h2>Select document folder</h2>
      <p>Choose the root folder of your yacht's NAS or document storage.</p>
      <div id="folder-candidates"></div>
      <button class="btn-folder btn-browse" onclick="doBrowse()">Browse for folder...</button>
      <div class="msg" id="msg-folder"></div>
    </div>
  </div>

  <!-- Step 4: Success -->
  <div class="step" id="step-success">
    <div class="progress">
      <div class="dot done"></div><div class="line"></div>
      <div class="dot done"></div><div class="line"></div>
      <div class="dot done"></div><div class="line"></div>
      <div class="dot done"></div>
    </div>
    <div class="card" style="text-align:center;">
      <div class="success-icon">&#10003;</div>
      <h2>CelesteOS is ready</h2>
      <p>Your documents will begin syncing automatically. CelesteOS will start on login and run in the background.</p>
      <p style="color:var(--txt3);font-size:12px;margin-top:16px;">You can close this window.</p>
      <button class="btn" onclick="doFinish()" style="margin-top:16px;">Done</button>
    </div>
  </div>
</div>

<script>
  // Bridge to Python via pywebview
  function pyCall(method, ...args) {
    return window.pywebview.api[method](...args);
  }

  function showStep(id) {
    document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
    document.getElementById(id).classList.add('active');
  }

  function setMsg(id, text, isError) {
    const el = document.getElementById(id);
    el.textContent = text;
    el.className = 'msg ' + (isError ? 'error' : 'success');
  }

  function setLoading(btnId, loading) {
    const btn = document.getElementById(btnId);
    if (loading) {
      btn.disabled = true;
      btn.dataset.orig = btn.textContent;
      btn.innerHTML = '<span class="spinner"></span>Please wait...';
    } else {
      btn.disabled = false;
      btn.textContent = btn.dataset.orig || btn.textContent;
    }
  }

  // Step 1: Register
  async function doRegister() {
    setLoading('btn-register', true);
    setMsg('msg-register', '', false);
    try {
      const result = await pyCall('register');
      const data = JSON.parse(result);
      if (data.success) {
        document.getElementById('email-display').textContent = data.email_sent_to || 'your email';
        showStep('step-2fa');
        document.getElementById('code-input').focus();
      } else {
        setMsg('msg-register', data.error || 'Registration failed', true);
      }
    } catch (e) {
      setMsg('msg-register', 'Connection error: ' + e.message, true);
    } finally {
      setLoading('btn-register', false);
    }
  }

  // Step 2: Verify 2FA
  async function doVerify() {
    const code = document.getElementById('code-input').value.trim();
    if (code.length !== 6) {
      setMsg('msg-verify', 'Enter the full 6-digit code', true);
      return;
    }
    setLoading('btn-verify', true);
    setMsg('msg-verify', '', false);
    try {
      const result = await pyCall('verify_2fa', code);
      const data = JSON.parse(result);
      if (data.success) {
        // Load folder candidates
        const foldersJson = await pyCall('get_folder_candidates');
        const folders = JSON.parse(foldersJson);
        renderFolders(folders);
        showStep('step-folder');
      } else {
        setMsg('msg-verify', data.error || 'Invalid code', true);
      }
    } catch (e) {
      setMsg('msg-verify', 'Connection error: ' + e.message, true);
    } finally {
      setLoading('btn-verify', false);
    }
  }

  // Step 3: Folder selection
  function renderFolders(folders) {
    const container = document.getElementById('folder-candidates');
    container.innerHTML = '';
    folders.forEach(path => {
      const btn = document.createElement('button');
      btn.className = 'btn-folder';
      const name = path.split('/').pop();
      btn.innerHTML = name + '<span class="path">' + path + '</span>';
      btn.onclick = () => selectFolder(path);
      container.appendChild(btn);
    });
  }

  async function selectFolder(path) {
    setMsg('msg-folder', '', false);
    try {
      const result = await pyCall('select_folder', path);
      const data = JSON.parse(result);
      if (data.success) {
        showStep('step-success');
      } else {
        setMsg('msg-folder', data.error || 'Invalid folder', true);
      }
    } catch (e) {
      setMsg('msg-folder', 'Error: ' + e.message, true);
    }
  }

  async function doBrowse() {
    try {
      const result = await pyCall('browse_folder');
      const data = JSON.parse(result);
      if (data.path) {
        await selectFolder(data.path);
      }
    } catch (e) {
      setMsg('msg-folder', 'Browse failed: ' + e.message, true);
    }
  }

  // Step 4: Done
  async function doFinish() {
    await pyCall('finish');
  }

  // Auto-advance on 6 digits
  document.getElementById('code-input').addEventListener('input', e => {
    e.target.value = e.target.value.replace(/\\D/g, '');
    if (e.target.value.length === 6) doVerify();
  });

  // Init: load yacht info
  (async () => {
    try {
      const info = await pyCall('get_yacht_info');
      const data = JSON.parse(info);
      document.getElementById('yacht-name-display').textContent = data.yacht_name || data.yacht_id;
    } catch (e) {
      document.getElementById('yacht-name-display').textContent = 'Unknown';
    }
  })();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Python API exposed to JavaScript via pywebview
# ---------------------------------------------------------------------------

class InstallerAPI:
    """
    Bridge between the HTML UI and the Python installer logic.
    Methods are called from JavaScript via window.pywebview.api.
    """

    def __init__(self, config):
        """
        Args:
            config: InstallConfig instance from lib.installer
        """
        from lib.installer import InstallationOrchestrator, KeychainStore
        self.config = config
        self.orchestrator = InstallationOrchestrator(config)
        self.orchestrator.initialize()
        self._selected_folder: Optional[str] = None
        self._window = None  # set after window creation

    def get_yacht_info(self) -> str:
        """Return yacht info for the welcome screen."""
        return json.dumps({
            "yacht_id": self.config.yacht_id,
            "yacht_name": getattr(self.config, 'yacht_name', self.config.yacht_id),
            "version": self.config.version,
        })

    def register(self) -> str:
        """Trigger registration and 2FA email."""
        success, message = self.orchestrator.register()
        if success:
            email_sent_to = message.split("to ")[-1] if "to " in message else ""
            return json.dumps({"success": True, "email_sent_to": email_sent_to})
        return json.dumps({"success": False, "error": message})

    def _show_simulated_email(self) -> None:
        """Open a second window showing the 2FA code (simulates email delivery)."""
        try:
            import httpx
            import hashlib

            # Fetch the latest unverified code from the database
            sb_url = os.getenv("MASTER_SUPABASE_URL", "https://qvzmkaamzaqxpzbewjxe.supabase.co")
            sb_key = os.getenv("MASTER_SUPABASE_SERVICE_KEY", "")
            if not sb_key:
                return  # Can't fetch without key

            headers = {
                "apikey": sb_key,
                "Authorization": f"Bearer {sb_key}",
            }
            resp = httpx.get(
                f"{sb_url}/rest/v1/installation_2fa_codes",
                params={
                    "yacht_id": f"eq.{self.config.yacht_id}",
                    "purpose": "eq.installation",
                    "verified": "eq.false",
                    "order": "created_at.desc",
                    "limit": "1",
                    "select": "code_hash",
                },
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200 or not resp.json():
                return

            code_hash = resp.json()[0]["code_hash"]
            # Brute-force the 6-digit code from hash (instant for 6 digits)
            code = None
            for i in range(1000000):
                candidate = f"{i:06d}"
                if hashlib.sha256(candidate.encode()).hexdigest() == code_hash:
                    code = candidate
                    break

            if not code:
                return

            yacht_name = getattr(self.config, 'yacht_name', self.config.yacht_id) or self.config.yacht_id

            # Open a second window showing the simulated email
            import webview
            email_html = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Simulated Email</title>
<style>
body {{ margin:0; padding:24px; background:#1a1a2e; color:#e2e8f0;
  font-family:-apple-system,sans-serif; }}
.banner {{ background:#f59e0b; color:#000; padding:8px 16px; border-radius:6px;
  font-size:12px; font-weight:600; margin-bottom:20px; text-align:center; }}
.from {{ color:#94a3b8; font-size:12px; margin-bottom:4px; }}
.subject {{ font-size:16px; font-weight:600; margin-bottom:20px; }}
.body {{ background:#0f172a; border-radius:8px; padding:24px; border:1px solid rgba(255,255,255,0.08); }}
.code {{ font-size:36px; letter-spacing:10px; font-weight:700; color:#5AABCC;
  text-align:center; margin:20px 0; font-family:monospace; }}
.hint {{ color:#94a3b8; font-size:13px; }}
.yacht {{ color:#5AABCC; font-weight:600; }}
</style></head><body>
<div class="banner">SIMULATED EMAIL — In production this arrives in the buyer's inbox</div>
<div class="from">From: noreply@celeste7.ai</div>
<div class="subject">CelesteOS — Your verification code</div>
<div class="body">
  <p>Your verification code for <span class="yacht">{yacht_name}</span>:</p>
  <div class="code">{code}</div>
  <p class="hint">Enter this code in the CelesteOS installer to complete activation.<br>
  This code expires in 10 minutes.</p>
</div>
</body></html>'''

            webview.create_window(
                "Simulated Email",
                html=email_html,
                width=420,
                height=340,
                x=600,
                y=100,
                resizable=False,
                on_top=True,
                background_color="#1a1a2e",
            )

        except Exception as exc:
            logger.warning("Could not show simulated email: %s", exc)

    def verify_2fa(self, code: str) -> str:
        """Verify the 2FA code."""
        success, message = self.orchestrator.verify_2fa(code)
        return json.dumps({"success": success, "error": "" if success else message})

    def get_folder_candidates(self) -> str:
        """Return list of detected NAS folder paths."""
        from .folder_selector import _find_nas_candidates
        candidates = _find_nas_candidates()
        return json.dumps(candidates)

    def browse_folder(self) -> str:
        """Open a native folder picker dialog."""
        if self._window:
            result = self._window.create_file_dialog(
                dialog_type=20,  # FOLDER_DIALOG
                allow_multiple=False,
            )
            if result and len(result) > 0:
                path = result[0] if isinstance(result, (list, tuple)) else str(result)
                return json.dumps({"path": path})
        return json.dumps({"path": None})

    def select_folder(self, path: str) -> str:
        """Validate and save the selected folder."""
        if not os.path.isdir(path):
            return json.dumps({"success": False, "error": "Folder does not exist"})

        self._selected_folder = path

        # Save to ~/.celesteos/.env.local
        env_dir = Path.home() / ".celesteos"
        env_dir.mkdir(parents=True, exist_ok=True)
        env_file = env_dir / ".env.local"

        lines = []
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if not line.strip().startswith("NAS_ROOT="):
                    lines.append(line)
        lines.append(f"NAS_ROOT={path}")
        env_file.write_text("\n".join(lines) + "\n")

        # Install launchd
        try:
            from .launchd import install_launchd
            install_launchd()
        except Exception as exc:
            logger.warning("Launchd install failed: %s", exc)

        return json.dumps({"success": True})

    def finish(self) -> str:
        """Close the window."""
        if self._window:
            self._window.destroy()
        return json.dumps({"success": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_installer_ui(config) -> Optional[str]:
    """
    Launch the installer UI window.

    Args:
        config: InstallConfig instance

    Returns:
        Selected NAS folder path, or None if cancelled
    """
    import webview

    api = InstallerAPI(config)
    window = webview.create_window(
        "CelesteOS Setup",
        html=INSTALLER_HTML,
        js_api=api,
        width=500,
        height=620,
        resizable=False,
        background_color="#0E1117",
    )
    api._window = window

    webview.start(debug=False)

    return api._selected_folder


if __name__ == "__main__":
    # Test mode: run with a mock config
    from lib.installer import InstallConfig
    try:
        config = InstallConfig.load_embedded()
    except FileNotFoundError:
        print("No manifest found. Create ~/.celesteos/install_manifest.json for testing.")
        exit(1)

    result = run_installer_ui(config)
    print(f"Selected folder: {result}")
