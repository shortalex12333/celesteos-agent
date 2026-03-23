from __future__ import annotations

"""
Configuration loader for the CelesteOS agent.

Priority: embedded manifest (DMG) > environment variables > ~/.celesteos/.env.local > Keychain
"""

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("agent.config")

ENV_DIR = Path.home() / ".celesteos"
ENV_FILE = ENV_DIR / ".env.local"
MANIFEST_DIR = ENV_DIR
KEYCHAIN_SERVICE = "ai.celeste7.filesync"


@dataclass
class SyncConfig:
    yacht_id: str = ""
    nas_root: str = ""
    supabase_url: str = ""
    supabase_key: str = ""  # service-role key
    poll_interval_s: int = 300
    max_upload_bytes_per_cycle: int = 0  # 0 = unlimited
    max_satellite_upload_mb: int = 0     # 0 = use default thresholds
    manifest_path: str = ""
    source_type: str = "nas"  # 'nas', 'onedrive', 'local'
    registration_api_endpoint: str = "https://registration.celeste7.ai"

    @property
    def is_configured(self) -> bool:
        return bool(self.yacht_id and self.nas_root and self.supabase_url and self.supabase_key)


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict. Ignores comments and blank lines."""
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Strip optional quotes
        value = value.strip().strip("'\"")
        env[key.strip()] = value
    return env


def _get_keychain_password(service: str, account: str) -> str:
    """Retrieve a password from macOS Keychain with recovery key fallback. Returns empty string on failure."""
    try:
        result = subprocess.run(
            [
                "security", "find-generic-password",
                "-s", service,
                "-a", account,
                "-w",  # output password only
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            pw = result.stdout.strip()
            if pw:
                return pw
        logger.debug("Keychain lookup failed for %s/%s: rc=%d", service, account, result.returncode)
    except Exception as exc:
        logger.warning("Keychain retrieval error: %s", exc)

    # Fallback: recovery key file — ONLY for the HMAC shared_secret,
    # not for Supabase keys or other credentials
    if service == "com.celeste7.celesteos":
        recovery_path = Path.home() / ".celesteos" / ".recovery_key"
        if recovery_path.exists():
            try:
                secret = recovery_path.read_text().strip()
                if secret:
                    logger.warning("Using recovery key — Keychain may need repair (%s/%s)", service, account)
                    return secret
            except OSError as exc:
                logger.warning("Failed to read recovery key: %s", exc)

    return ""


def load_from_manifest() -> SyncConfig | None:
    """
    Load config from embedded install_manifest.json (DMG installs).
    Returns None if manifest not found.
    """
    # Check for manifest in the app bundle or ~/.celesteos
    manifest_paths = [
        Path(__file__).parent.parent / "install_manifest.json",
        ENV_DIR / "install_manifest.json",
    ]

    for manifest_path in manifest_paths:
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text())
                logger.info("Loaded config from manifest: %s", manifest_path)

                supabase_key = data.get("supabase_service_key", "")
                if not supabase_key:
                    supabase_key = _get_keychain_password(KEYCHAIN_SERVICE, "SUPABASE_SERVICE_KEY")

                return SyncConfig(
                    yacht_id=data.get("yacht_id", ""),
                    nas_root=data.get("nas_root", ""),
                    supabase_url=data.get("supabase_url", "").rstrip("/"),
                    supabase_key=supabase_key,
                    poll_interval_s=int(data.get("poll_interval_s", 300)),
                    manifest_path=str(MANIFEST_DIR / "filesync_manifest.db"),
                    source_type=data.get("source_type", "nas"),
                )
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Failed to parse manifest %s: %s", manifest_path, exc)

    return None


def load_from_env() -> SyncConfig:
    """Load configuration from .env.local + Keychain + environment overrides."""
    env = _read_env_file(ENV_FILE)

    # Environment variables override .env.local
    yacht_id = os.environ.get("YACHT_ID", env.get("YACHT_ID", ""))
    nas_root = os.environ.get("NAS_ROOT", env.get("NAS_ROOT", ""))
    supabase_url = os.environ.get("SUPABASE_URL", env.get("SUPABASE_URL", ""))
    source_type = os.environ.get("SOURCE_TYPE", env.get("SOURCE_TYPE", "nas"))

    # Service key: env var → Keychain → .env.local (least preferred, plaintext)
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not supabase_key:
        supabase_key = _get_keychain_password(KEYCHAIN_SERVICE, "SUPABASE_SERVICE_KEY")
    if not supabase_key:
        supabase_key = env.get("SUPABASE_SERVICE_KEY", "")
        if supabase_key:
            logger.warning("Using SUPABASE_SERVICE_KEY from .env.local — migrate to Keychain")

    poll_interval = int(os.environ.get(
        "POLL_INTERVAL_S", env.get("POLL_INTERVAL_S", "300")
    ))
    max_upload = int(os.environ.get(
        "MAX_UPLOAD_BYTES_PER_CYCLE", env.get("MAX_UPLOAD_BYTES_PER_CYCLE", "0")
    ))
    max_sat = int(os.environ.get(
        "MAX_SATELLITE_UPLOAD_MB", env.get("MAX_SATELLITE_UPLOAD_MB", "0")
    ))

    manifest_path = str(MANIFEST_DIR / "filesync_manifest.db")

    return SyncConfig(
        yacht_id=yacht_id,
        nas_root=nas_root,
        supabase_url=supabase_url.rstrip("/"),
        supabase_key=supabase_key,
        poll_interval_s=poll_interval,
        max_upload_bytes_per_cycle=max_upload,
        max_satellite_upload_mb=max_sat,
        manifest_path=manifest_path,
        source_type=source_type,
    )


def load_config() -> SyncConfig:
    """
    Load configuration with priority: manifest > env > .env.local > Keychain.
    """
    # Try manifest first (DMG install)
    cfg = load_from_manifest()
    if cfg and cfg.is_configured:
        return cfg

    # Fall back to env-based config
    cfg = load_from_env()

    if not cfg.is_configured:
        missing = []
        if not cfg.yacht_id:
            missing.append("YACHT_ID")
        if not cfg.nas_root:
            missing.append("NAS_ROOT")
        if not cfg.supabase_url:
            missing.append("SUPABASE_URL")
        if not cfg.supabase_key:
            missing.append("SUPABASE_SERVICE_KEY")
        logger.error("Missing required config: %s", ", ".join(missing))

    return cfg
