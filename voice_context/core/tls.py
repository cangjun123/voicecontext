from __future__ import annotations

import logging
import ssl

LOG = logging.getLogger("voice_context")


def _build_ssl_context() -> ssl.SSLContext:
    """Build the process-wide TLS context while the CA bundle is still readable.

    PyInstaller one-file builds unpack certifi's cacert.pem into %TEMP%\\_MEI* at
    startup; temp-cleaning tools can delete it from under a long-lived tray app,
    which turns every later httpx call into a confusing FileNotFoundError. httpx
    rebuilds an SSL context per request by default, so capture one trusted
    context here, at import time, and reuse it for the process lifetime.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except (OSError, ImportError) as exc:
        # Bundle missing at startup too (rare): fall back to Windows/system trust
        # stores rather than failing every HTTPS request outright.
        LOG.warning("Could not load the certifi CA bundle (%s); using system trust store", exc)
        return ssl.create_default_context()


# Shared per process: imported once at startup, before any background thread runs.
SSL_CONTEXT = _build_ssl_context()
