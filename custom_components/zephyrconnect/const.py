"""Constants for Zephyr Connect integration."""

DOMAIN = "zephyrconnect"

# ── Cognito app credentials ───────────────────────────────────────────────────
# These were extracted from Zephyr Connect APK v1.1.16 (awsconfiguration.json).
# Zephyr/Gemteks could rotate these in a future app update.
# If AUTH_ERROR_REASON == "stale_client_credentials", these need updating.
# To get new values: download latest APK, run:
#   apktool d ZephyrConnect.apk && grep -r "AppClientId\|AppClientSecret" out/
# Or use jadx and look in res/raw/awsconfiguration.json
COGNITO_APP_CLIENT_VERSION = "1.1.16"   # APK version these were extracted from
COGNITO_REGION        = "us-west-2"
USER_POOL_ID          = "us-west-2_McuoKpkna"
APP_CLIENT_ID         = "5a2qiskdvvu7gre1jvbjnunu20"
APP_CLIENT_SECRET     = "3b085l2fkgph4kt734k5e26tirb9hjasgb4rn8sjpp4mheo5kga"
IDENTITY_POOL_ID      = "us-west-2:fb4c1b66-12c2-414b-83a1-a1902f7d98e3"

# ── AWS IoT ───────────────────────────────────────────────────────────────────
IOT_ENDPOINT          = "a1nqxu0hki9zw3-ats.iot.us-west-2.amazonaws.com"
IOT_POLICY_NAME       = "RangeHoodPolicy"

# ── REST API ──────────────────────────────────────────────────────────────────
API_BASE_URL          = "https://zephyr-prod-app.gemteks.com/prod"

# ── MQTT topic templates ──────────────────────────────────────────────────────
TOPIC_GET             = "$aws/things/{thing}/shadow/get"
TOPIC_GET_ACCEPTED    = "$aws/things/{thing}/shadow/get/accepted"
TOPIC_UPDATE          = "$aws/things/{thing}/shadow/update"
TOPIC_UPDATE_ACCEPTED = "$aws/things/{thing}/shadow/update/accepted"
TOPIC_UPDATE_REJECTED = "$aws/things/{thing}/shadow/update/rejected"

# ── Shadow state keys (from Reported.java + confirmed via discoverdevice) ─────
KEY_POWER             = "power"
KEY_FAN               = "fan"
KEY_LIGHT             = "light"
KEY_DELAY_TIMER       = "delaytimer"
KEY_IS_ONLINE         = "isOnline"
KEY_CLEAN_GREASE      = "cleangreasefilters"
KEY_CLEAN_CHARCOAL    = "cleancharcoalfilters"
KEY_FAULT_CODE        = "faultCode"
KEY_FAN_WARNING       = "fanwarning"
KEY_ALARM_FAN         = "alarmfan"
KEY_ALARM_GREASE      = "alarmgreasefilter"
KEY_USE_GREASE_TIME   = "usegreasefiltertime"
KEY_USE_CHARCOAL_TIME = "usecharcoalfiltertime"
KEY_USE_LIGHT_TIME    = "uselighttime"
KEY_USE_FAN_TIME      = "usefantime"

# ── Config entry data keys ────────────────────────────────────────────────────
CONF_THING_NAME       = "thing_name"
CONF_MAX_FAN_SPEED    = "max_fan_speed"
CONF_MAX_LIGHT_LEVEL  = "max_light_level"
CONF_MODEL_NAME       = "model_name"
CONF_SERIAL           = "serial"
CONF_MAC              = "mac"

# ── Defaults (fetched per-device from discoverdevice, stored in config entry) ─
DEFAULT_MAX_FAN_SPEED   = 6
DEFAULT_MAX_LIGHT_LEVEL = 3

# ── Auth error reason codes ───────────────────────────────────────────────────
# Stored in entry.data so the UI can show the right repair message.
AUTH_ERROR_WRONG_PASSWORD         = "wrong_password"
AUTH_ERROR_USER_NOT_FOUND         = "user_not_found"
AUTH_ERROR_STALE_CLIENT_CREDS     = "stale_client_credentials"
AUTH_ERROR_NETWORK                = "network_error"
AUTH_ERROR_UNKNOWN                = "unknown"

# Cognito error codes that indicate our hardcoded client credentials are stale
# (i.e., Zephyr rotated their app client in a new APK version)
STALE_CREDS_COGNITO_ERRORS = {
    "ResourceNotFoundException",   # client ID doesn't exist in the pool
    "InvalidClientTokenId",        # client ID rejected at API level
    "UnrecognizedClientException", # bad client secret
}

# ── Intervals ─────────────────────────────────────────────────────────────────
SCAN_INTERVAL = 60   # seconds — MQTT push is primary, this is fallback poll
TOKEN_REFRESH_BUFFER = 300  # refresh token 5 min before expiry
