"""Typed exceptions for Zephyr Connect.

Having distinct exception types lets the config flow, __init__, and repair
flow each respond appropriately rather than pattern-matching on strings.
"""


class ZephyrAuthError(Exception):
    """Base class for all authentication errors."""
    reason: str = "unknown"


class ZephyrWrongPasswordError(ZephyrAuthError):
    """User exists but password is wrong. User should change password in app."""
    reason = "wrong_password"


class ZephyrUserNotFoundError(ZephyrAuthError):
    """No Cognito account found for this email address."""
    reason = "user_not_found"


class ZephyrStaleClientCredsError(ZephyrAuthError):
    """The hardcoded APP_CLIENT_ID/SECRET are no longer valid.

    This means Zephyr released a new APK version that rotates the Cognito
    app client. The integration maintainer needs to:
      1. Download the latest Zephyr Connect APK
      2. Extract res/raw/awsconfiguration.json (or decompile with jadx)
      3. Update APP_CLIENT_ID, APP_CLIENT_SECRET, and COGNITO_APP_CLIENT_VERSION
         in const.py
    """
    reason = "stale_client_credentials"


class ZephyrNetworkError(ZephyrAuthError):
    """Network/connectivity error during auth."""
    reason = "network_error"


class ZephyrMQTTError(Exception):
    """MQTT connection or publish failure."""


class ZephyrAPIError(Exception):
    """REST API call failed."""
    def __init__(self, message: str, endpoint: str = ""):
        super().__init__(message)
        self.endpoint = endpoint
