"""Doppler integration for Wizard Launcher.

Replaces the old Azure Key Vault module. Doppler's normal mode of operation
is to inject secrets straight into the process environment (``doppler run --
...``), so that is checked first and costs nothing. DOPPLER_TOKEN is only
needed as a fallback, for a launch that was not started through Doppler.

Usage:
    from launcher_core.doppler_secrets import initialize_doppler, get_doppler_manager

    # Initialize (typically in launcher.py startup)
    initialize_doppler(project="wizard-launcher", config="prd")

    # Use anywhere
    mgr = get_doppler_manager()
    api_key = mgr.get_secret("API_KEY")
"""

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.doppler.com/v3"


class DopplerSecretsManager:
    """Manages secret lookup against a Doppler project/config."""

    def __init__(self, project: Optional[str] = None, config: Optional[str] = None,
                 token: Optional[str] = None):
        self.project = project or os.environ.get("DOPPLER_PROJECT")
        self.config = config or os.environ.get("DOPPLER_CONFIG")
        self.token = token or os.environ.get("DOPPLER_TOKEN")
        self._cache: Optional[dict] = None

    def _auth(self):
        if not self.token:
            raise RuntimeError(
                "No DOPPLER_TOKEN set; cannot reach the Doppler API for a secret "
                "that isn't already in the environment."
            )
        # Doppler's API takes the service token as the basic-auth username
        # with an empty password - same convention as `doppler run`'s token.
        return (self.token, "")

    def _fetch_all(self) -> dict:
        """Lazy-load every secret in this project/config, once.

        Uses the same "download" endpoint the Doppler CLI uses to populate a
        process environment, so a value read here matches what ``doppler
        run`` would have injected directly.
        """
        if self._cache is None:
            params = {"format": "json"}
            if self.project:
                params["project"] = self.project
            if self.config:
                params["config"] = self.config
            resp = requests.get(
                f"{API_BASE}/configs/config/secrets/download",
                params=params, auth=self._auth(), timeout=10,
            )
            resp.raise_for_status()
            self._cache = resp.json()
        return self._cache

    def get_secret(self, secret_name: str) -> str:
        """Retrieve a secret's value.

        Checks the process environment first, then falls back to Doppler's
        API.

        Raises:
            ValueError: If the secret doesn't exist
        """
        from_env = os.environ.get(secret_name)
        if from_env is not None:
            return from_env

        try:
            value = self._fetch_all().get(secret_name)
        except Exception as e:
            logger.error(f"Failed to retrieve secret '{secret_name}': {e}")
            raise
        if value is None:
            raise ValueError(f"Secret '{secret_name}' not found in Doppler.")
        logger.info(f"Retrieved secret: {secret_name}")
        return value

    def _update_secrets(self, mapping: dict) -> None:
        payload = {"secrets": mapping}
        if self.project:
            payload["project"] = self.project
        if self.config:
            payload["config"] = self.config
        resp = requests.post(
            f"{API_BASE}/configs/config/secrets",
            json=payload, auth=self._auth(), timeout=10,
        )
        resp.raise_for_status()

    def set_secret(self, secret_name: str, secret_value: str) -> None:
        """Store a secret in Doppler.

        Raises:
            Exception: If authentication fails or write permission is denied
        """
        self._update_secrets({secret_name: secret_value})
        if self._cache is not None:
            self._cache[secret_name] = secret_value

    def delete_secret(self, secret_name: str) -> None:
        """Delete a secret from Doppler (a null value removes it).

        Raises:
            Exception: If the secret doesn't exist or delete permission is denied
        """
        self._update_secrets({secret_name: None})
        if self._cache is not None:
            self._cache.pop(secret_name, None)

    def secret_exists(self, secret_name: str) -> bool:
        if secret_name in os.environ:
            return True
        try:
            return secret_name in self._fetch_all()
        except Exception:
            return False


# Global instance - configure this with your Doppler project/config.
_doppler_manager: Optional[DopplerSecretsManager] = None


def initialize_doppler(project: Optional[str] = None, config: Optional[str] = None,
                        token: Optional[str] = None) -> None:
    """Initialize the global Doppler secrets manager.

    Args:
        project: Doppler project name (falls back to DOPPLER_PROJECT)
        config: Doppler config/environment name (falls back to DOPPLER_CONFIG)
        token: Service token (falls back to DOPPLER_TOKEN); not needed at all
            if every secret is already present in the environment
    """
    global _doppler_manager
    _doppler_manager = DopplerSecretsManager(project=project, config=config, token=token)


def get_doppler_manager() -> DopplerSecretsManager:
    """Get the global Doppler secrets manager instance."""
    if _doppler_manager is None:
        raise RuntimeError(
            "Doppler not initialized. Call initialize_doppler() first."
        )
    return _doppler_manager
