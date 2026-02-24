"""Zephyr Connect API — auth, REST discovery, and MQTT control.

Auth flow (proven working via test scripts):
  1. pycognito SRP → IdToken + RefreshToken
  2. boto3 Cognito Identity → temporary AWS credentials
  3. boto3 IoT attach_policy → allow identity to use MQTT
  4. awsiotsdk websockets_with_default_aws_signing → MQTT over WSS/SigV4

Device discovery flow:
  1. POST /getowndevices → list of {thingName, SN, modelName, MAC, location}
  2. POST /discoverdevice → full state + capabilities (maxFanSpeed, maxLightLevel, etc.)

Control flow:
  - Publish {"state":{"reported":{"fan":N}}} to $aws/things/{thing}/shadow/update
  - Shadow update/accepted fires back with confirmed new state
  - Shadow get/accepted fires on startup with full current state

Credential staleness detection:
  Cognito errors ResourceNotFoundException / UnrecognizedClientException /
  InvalidClientTokenId indicate the APP_CLIENT_ID or APP_CLIENT_SECRET hardcoded
  in const.py no longer match what Zephyr is using in their current APK.
  These are raised as ZephyrStaleClientCredsError so the UI can surface a
  specific repair action to the user.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Optional

import boto3
import requests
from awscrt import auth, mqtt
from awsiot import mqtt_connection_builder
from botocore.exceptions import ClientError
from pycognito import Cognito
from pycognito.exceptions import ForceChangePasswordException

from .const import (
    APP_CLIENT_ID,
    APP_CLIENT_SECRET,
    API_BASE_URL,
    COGNITO_REGION,
    IDENTITY_POOL_ID,
    IOT_ENDPOINT,
    IOT_POLICY_NAME,
    KEY_FAN,
    KEY_LIGHT,
    STALE_CREDS_COGNITO_ERRORS,
    TOKEN_REFRESH_BUFFER,
    TOPIC_GET,
    TOPIC_GET_ACCEPTED,
    TOPIC_UPDATE,
    TOPIC_UPDATE_ACCEPTED,
    TOPIC_UPDATE_REJECTED,
    USER_POOL_ID,
)
from .exceptions import (
    ZephyrAPIError,
    ZephyrMQTTError,
    ZephyrNetworkError,
    ZephyrStaleClientCredsError,
    ZephyrUserNotFoundError,
    ZephyrWrongPasswordError,
)

_LOGGER = logging.getLogger(__name__)


def _classify_cognito_error(exc: Exception) -> Exception:
    """Map a raw Cognito/boto3 exception to a typed ZephyrAuthError.

    This is the central place where we detect stale client credentials vs
    wrong password vs user not found, so callers don't have to parse strings.
    """
    error_code = ""
    error_msg = str(exc).lower()

    # boto3 ClientError carries a structured error code
    if hasattr(exc, "response"):
        error_code = exc.response.get("Error", {}).get("Code", "")

    _LOGGER.debug("Cognito error code=%r msg=%r", error_code, error_msg)

    # Stale client credentials — Zephyr rotated their APK's client ID/secret
    if error_code in STALE_CREDS_COGNITO_ERRORS:
        _LOGGER.error(
            "Cognito client credentials are stale (error_code=%s). "
            "Zephyr likely released a new APK. Update APP_CLIENT_ID, "
            "APP_CLIENT_SECRET, and COGNITO_APP_CLIENT_VERSION in const.py. "
            "Extract from: apktool d ZephyrConnect.apk -> res/raw/awsconfiguration.json",
            error_code,
        )
        return ZephyrStaleClientCredsError(
            f"App client credentials rejected by Cognito ({error_code}). "
            "The integration needs to be updated with credentials from the "
            "latest Zephyr Connect APK."
        )

    # Wrong password
    if error_code == "NotAuthorizedException" or "incorrect username or password" in error_msg:
        return ZephyrWrongPasswordError("Incorrect password")

    # User doesn't exist
    if error_code == "UserNotFoundException" or "user does not exist" in error_msg:
        return ZephyrUserNotFoundError("No account found for this email")

    # Network / connection issues
    if any(k in error_msg for k in ("connection", "timeout", "network", "endpoint")):
        return ZephyrNetworkError(f"Network error: {exc}")

    # Fall through — unknown, wrap with original
    from .exceptions import ZephyrAuthError
    err = ZephyrAuthError(str(exc))
    err.reason = "unknown"
    return err


class ZephyrAuth:
    """Cognito SRP authentication + AWS Identity credentials.

    Raises typed ZephyrAuthError subclasses so callers can respond
    specifically to stale credentials vs wrong password etc.
    """

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self.id_token: Optional[str] = None
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.aws_credentials: Optional[dict] = None
        self.identity_id: Optional[str] = None
        self._token_expiry: float = 0

    def authenticate(self) -> None:
        """Full auth: SRP → tokens → AWS credentials.  Raises on failure."""
        _LOGGER.debug("Authenticating %s via Cognito SRP", self._username)
        try:
            u = Cognito(
                USER_POOL_ID,
                APP_CLIENT_ID,
                username=self._username,
                client_secret=APP_CLIENT_SECRET,
            )
            u.authenticate(self._password)
        except Exception as exc:
            raise _classify_cognito_error(exc) from exc

        self.id_token = u.id_token
        self.access_token = u.access_token
        self.refresh_token = u.refresh_token
        self._token_expiry = time.time() + 3600 - TOKEN_REFRESH_BUFFER
        _LOGGER.debug("SRP auth OK, fetching AWS identity credentials")
        self._fetch_identity_credentials()

    def refresh(self) -> None:
        """Refresh tokens using stored refresh token.  Falls back to full auth."""
        _LOGGER.debug("Refreshing Cognito tokens for %s", self._username)
        try:
            u = Cognito(
                USER_POOL_ID,
                APP_CLIENT_ID,
                username=self._username,
                client_secret=APP_CLIENT_SECRET,
                refresh_token=self.refresh_token,
            )
            u.renew_access_token()
            self.id_token = u.id_token
            self.access_token = u.access_token
            self._token_expiry = time.time() + 3600 - TOKEN_REFRESH_BUFFER
            self._fetch_identity_credentials()
            _LOGGER.debug("Token refresh OK")
        except Exception as exc:
            typed = _classify_cognito_error(exc)
            # If it's a stale creds error, surface it immediately
            if isinstance(typed, ZephyrStaleClientCredsError):
                raise typed from exc
            # Otherwise try a full re-auth
            _LOGGER.warning("Token refresh failed (%s), attempting full re-auth", exc)
            self.authenticate()

    def ensure_valid(self) -> None:
        """Refresh tokens if they're about to expire."""
        if time.time() >= self._token_expiry:
            self.refresh()

    def _fetch_identity_credentials(self) -> None:
        """Exchange IdToken for temporary AWS credentials via Identity Pool."""
        provider_key = f"cognito-idp.{COGNITO_REGION}.amazonaws.com/{USER_POOL_ID}"
        try:
            id_client = boto3.client("cognito-identity", region_name=COGNITO_REGION)
            resp = id_client.get_id(
                IdentityPoolId=IDENTITY_POOL_ID,
                Logins={provider_key: self.id_token},
            )
            self.identity_id = resp["IdentityId"]

            resp = id_client.get_credentials_for_identity(
                IdentityId=self.identity_id,
                Logins={provider_key: self.id_token},
            )
            self.aws_credentials = resp["Credentials"]
            _LOGGER.debug("AWS credentials obtained for identity %s", self.identity_id)

            # Attach IoT policy to this identity (idempotent — safe to call repeatedly)
            self._attach_iot_policy()

        except ClientError as exc:
            raise _classify_cognito_error(exc) from exc
        except Exception as exc:
            if "connection" in str(exc).lower() or "timeout" in str(exc).lower():
                raise ZephyrNetworkError(str(exc)) from exc
            raise

    def _attach_iot_policy(self) -> None:
        """Attach IoT policy to identity so it can use MQTT.  Idempotent."""
        try:
            creds = self.aws_credentials
            iot = boto3.client(
                "iot",
                region_name=COGNITO_REGION,
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretKey"],
                aws_session_token=creds["SessionToken"],
            )
            iot.attach_policy(policyName=IOT_POLICY_NAME, target=self.identity_id)
            _LOGGER.debug("IoT policy '%s' attached to %s", IOT_POLICY_NAME, self.identity_id)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ResourceAlreadyExistsException":
                pass  # already attached, fine
            else:
                _LOGGER.warning("IoT policy attach failed (code=%s): %s", code, exc)

    def credentials_provider(self) -> auth.AwsCredentialsProvider:
        """Return an awscrt credentials provider for use with MQTT."""
        creds = self.aws_credentials
        return auth.AwsCredentialsProvider.new_static(
            access_key_id=creds["AccessKeyId"],
            secret_access_key=creds["SecretKey"],
            session_token=creds["SessionToken"],
        )


class ZephyrRestClient:
    """REST client for the Gemteks/Zephyr backend API."""

    def __init__(self, zauth: ZephyrAuth) -> None:
        self._auth = zauth
        self._session = requests.Session()

    def _headers(self) -> dict:
        return {"Authorization": self._auth.id_token, "Content-Type": "application/json"}

    def get_devices(self) -> list[dict]:
        """Return list of all devices owned by this account.

        Each device dict contains: thingName, SN, modelName, MAC, location.
        """
        self._auth.ensure_valid()
        try:
            resp = self._session.post(
                f"{API_BASE_URL}/getowndevices",
                headers=self._headers(),
                json={},
                timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ZephyrAPIError(str(exc), "getowndevices") from exc

        data = resp.json()
        _LOGGER.debug("getowndevices response: %s", data)

        msg = data.get("message", "")
        if "Success" not in msg:
            raise ZephyrAPIError(f"getowndevices returned: {msg}", "getowndevices")

        return data.get("devices", [])

    def discover_device(self, thing_name: str) -> dict:
        """Fetch full device state and capabilities for a specific thing.

        Returns all fields confirmed from real device (AK9434BS):
          thingName, SN, modelName, MAC, isOnline, power, fan, light,
          maxFanSpeed (6), maxLightLevel (3), cleangreasefilters,
          alarmgreasefilter, usegreasefiltertime, usefantime, uselighttime,
          warrantyDates, URLs, truHueSupport, etc.
        """
        self._auth.ensure_valid()
        try:
            resp = self._session.post(
                f"{API_BASE_URL}/discoverdevice",
                headers=self._headers(),
                json={"thingName": thing_name},
                timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ZephyrAPIError(str(exc), "discoverdevice") from exc

        data = resp.json()
        _LOGGER.debug("discoverdevice response: %s", data)
        return data


class ZephyrMQTTClient:
    """AWS IoT MQTT client for real-time device shadow control.

    Uses WebSocket + SigV4 signing with Cognito Identity credentials.

    Command format (from AwsCommand.java, proven via test script):
        {"state": {"reported": {"fan": 3}}}

    Key values are integers. 0 = off for fan and light.
    Fan range: 0–maxFanSpeed (device-specific, AK9434BS = 0–6)
    Light range: 0–maxLightLevel (device-specific, AK9434BS = 0–3)
    """

    def __init__(self, zauth: ZephyrAuth, thing_name: str) -> None:
        self._auth = zauth
        self._thing_name = thing_name
        self._connection = None
        self._state: dict[str, Any] = {}
        self._state_lock = threading.Lock()
        self._connected = False
        self._state_callbacks: list[Callable[[dict], None]] = []

        self._topic_get          = TOPIC_GET.format(thing=thing_name)
        self._topic_get_accepted = TOPIC_GET_ACCEPTED.format(thing=thing_name)
        self._topic_update       = TOPIC_UPDATE.format(thing=thing_name)
        self._topic_upd_accepted = TOPIC_UPDATE_ACCEPTED.format(thing=thing_name)
        self._topic_upd_rejected = TOPIC_UPDATE_REJECTED.format(thing=thing_name)

    # ── Public API ────────────────────────────────────────────────────────────

    def add_state_callback(self, cb: Callable[[dict], None]) -> None:
        self._state_callbacks.append(cb)

    def connect(self) -> None:
        """Connect to AWS IoT MQTT.  Raises ZephyrMQTTError on failure."""
        _LOGGER.debug("Connecting to AWS IoT: %s", IOT_ENDPOINT)
        try:
            self._connection = mqtt_connection_builder.websockets_with_default_aws_signing(
                endpoint=IOT_ENDPOINT,
                region=COGNITO_REGION,
                credentials_provider=self._auth.credentials_provider(),
                client_id=f"haos-zephyr-{int(time.time())}",
                clean_session=True,
                keep_alive_secs=30,
                on_connection_interrupted=self._on_interrupted,
                on_connection_resumed=self._on_resumed,
            )
            self._connection.connect().result(timeout=15)
            self._connected = True
            _LOGGER.debug("MQTT connected")
            self._subscribe_topics()
            self._request_state()
        except Exception as exc:
            raise ZephyrMQTTError(f"MQTT connection failed: {exc}") from exc

    def disconnect(self) -> None:
        if self._connection and self._connected:
            try:
                self._connection.disconnect().result(timeout=5)
            except Exception:
                pass
            self._connected = False

    def publish_command(self, key: str, value: int) -> None:
        """Send a shadow update command to the device.

        Refreshes AWS credentials first if they're about to expire
        (Cognito Identity credentials are only valid for ~1 hour).
        """
        # Refresh tokens if needed before publishing — stale creds = silent failure
        try:
            self._auth.ensure_valid()
        except ZephyrStaleClientCredsError:
            _LOGGER.error("Cannot send command — client credentials are stale")
            raise

        if not self._connected:
            _LOGGER.warning("MQTT not connected, attempting reconnect before command")
            self._reconnect()

        payload = json.dumps({"state": {"reported": {key: value}}})
        _LOGGER.debug("Publishing %s=%d to %s", key, value, self._topic_update)
        try:
            future, _ = self._connection.publish(
                topic=self._topic_update,
                payload=payload,
                qos=mqtt.QoS.AT_LEAST_ONCE,
            )
            future.result(timeout=5)
        except Exception as exc:
            raise ZephyrMQTTError(f"Publish failed: {exc}") from exc

    def get_state(self) -> dict:
        with self._state_lock:
            return dict(self._state)

    def seed_state(self, state: dict) -> None:
        """Seed initial state from REST discoverdevice response."""
        with self._state_lock:
            self._state.update(state)

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Internal ──────────────────────────────────────────────────────────────

    def _notify(self) -> None:
        state = self.get_state()
        for cb in self._state_callbacks:
            try:
                cb(state)
            except Exception as exc:
                _LOGGER.error("State callback error: %s", exc)

    def _on_message(self, topic, payload, dup, qos, retain, **kwargs):
        try:
            msg = json.loads(payload.decode())
            _LOGGER.debug("MQTT msg on %s: %s", topic, msg)
            reported = msg.get("state", {}).get("reported", {})
            if reported:
                with self._state_lock:
                    self._state.update(reported)
                self._notify()
            elif topic.endswith("/rejected"):
                _LOGGER.warning("Shadow operation rejected: %s", msg)
        except Exception as exc:
            _LOGGER.error("MQTT message parse error: %s — raw: %s", exc, payload)

    def _on_interrupted(self, connection, error, **kwargs):
        _LOGGER.warning("MQTT interrupted: %s", error)
        self._connected = False

    def _on_resumed(self, connection, return_code, session_present, **kwargs):
        _LOGGER.info("MQTT resumed (return_code=%s)", return_code)
        self._connected = True
        # Re-fetch state in case we missed updates while disconnected
        self._request_state()

    def _subscribe_topics(self) -> None:
        for topic in [self._topic_get_accepted, self._topic_upd_accepted, self._topic_upd_rejected]:
            future, _ = self._connection.subscribe(
                topic=topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_message,
            )
            future.result(timeout=5)
            _LOGGER.debug("Subscribed: %s", topic)

    def _request_state(self) -> None:
        """Ask the shadow for a full state snapshot."""
        try:
            self._connection.publish(
                topic=self._topic_get,
                payload="{}",
                qos=mqtt.QoS.AT_LEAST_ONCE,
            )
        except Exception as exc:
            _LOGGER.warning("Shadow GET request failed: %s", exc)

    def _reconnect(self) -> None:
        """Attempt reconnect with fresh credentials."""
        _LOGGER.info("Attempting MQTT reconnect with fresh credentials")
        try:
            self._auth.ensure_valid()
            # Rebuild connection with fresh credentials
            self._connection = mqtt_connection_builder.websockets_with_default_aws_signing(
                endpoint=IOT_ENDPOINT,
                region=COGNITO_REGION,
                credentials_provider=self._auth.credentials_provider(),
                client_id=f"haos-zephyr-{int(time.time())}",
                clean_session=True,
                keep_alive_secs=30,
                on_connection_interrupted=self._on_interrupted,
                on_connection_resumed=self._on_resumed,
            )
            self._connection.connect().result(timeout=15)
            self._connected = True
            self._subscribe_topics()
            self._request_state()
            _LOGGER.info("MQTT reconnected successfully")
        except Exception as exc:
            raise ZephyrMQTTError(f"Reconnect failed: {exc}") from exc
