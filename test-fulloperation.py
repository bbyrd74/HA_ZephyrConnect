#!/usr/bin/env python

"""
Zephyr Connect MQTT Test Script
Tests AWS IoT MQTT connection and device shadow control.

Usage:
    pip install pycognito boto3 awsiotsdk
    export ZEPHYREMAIL=EMAILADDR
    export ZEPHYRPASS=PASSWORD
    python3 zephyr_mqtt_test.py
"""

import json
import logging
import os
import sys
import time
import threading

from pycognito import Cognito
import boto3

# AWS IoT SDK v2
from awscrt import mqtt
from awsiot import mqtt_connection_builder

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
log = logging.getLogger("zephyr_mqtt")

# ── Config ───────────────────────────────────────────────────────────────────
USER_POOL_ID     = "us-west-2_McuoKpkna"
CLIENT_ID        = "5a2qiskdvvu7gre1jvbjnunu20"
CLIENT_SECRET    = "3b085l2fkgph4kt734k5e26tirb9hjasgb4rn8sjpp4mheo5kga"
IDENTITY_POOL_ID = "us-west-2:fb4c1b66-12c2-414b-83a1-a1902f7d98e3"
COGNITO_REGION   = "us-west-2"
IOT_ENDPOINT     = "a1nqxu0hki9zw3-ats.iot.us-west-2.amazonaws.com"
IOT_POLICY_NAME  = "RangeHoodPolicy"
THING_NAME       = "46fb1f32cfb7349885aaffe29e2d1e0dd2db84e6"

USERNAME = os.environ.get("ZEPHYREMAIL", "EMAILADDRESS")
PASSWORD = os.environ.get("ZEPHYRPASS", "PASSWORD")

# ── MQTT topics ───────────────────────────────────────────────────────────────
TOPIC_GET          = f"$aws/things/{THING_NAME}/shadow/get"
TOPIC_GET_ACCEPTED = f"$aws/things/{THING_NAME}/shadow/get/accepted"
TOPIC_GET_REJECTED = f"$aws/things/{THING_NAME}/shadow/get/rejected"
TOPIC_UPDATE       = f"$aws/things/{THING_NAME}/shadow/update"
TOPIC_UPDATE_ACCPT = f"$aws/things/{THING_NAME}/shadow/update/accepted"
TOPIC_UPDATE_REJCT = f"$aws/things/{THING_NAME}/shadow/update/rejected"


# ── Step 1: Cognito auth ──────────────────────────────────────────────────────

def get_cognito_tokens() -> tuple[str, str]:
    log.info("═══ Step 1: Cognito SRP Authentication ═══")
    u = Cognito(USER_POOL_ID, CLIENT_ID, username=USERNAME, client_secret=CLIENT_SECRET)
    u.authenticate(PASSWORD)
    log.info("✅ Cognito auth successful")
    log.info("IdToken (first 60): %s...", u.id_token[:60])
    log.info("AccessToken (first 60): %s...", u.access_token[:60])
    return u.id_token, u.access_token


# ── Step 2: Get AWS credentials via Identity Pool ─────────────────────────────

def get_aws_credentials(id_token: str) -> dict:
    log.info("═══ Step 2: Get AWS Identity Credentials ═══")
    provider_key = f"cognito-idp.{COGNITO_REGION}.amazonaws.com/{USER_POOL_ID}"

    identity_client = boto3.client("cognito-identity", region_name=COGNITO_REGION)

    # Get identity ID
    resp = identity_client.get_id(
        IdentityPoolId=IDENTITY_POOL_ID,
        Logins={provider_key: id_token},
    )
    identity_id = resp["IdentityId"]
    log.info("Identity ID: %s", identity_id)

    # Get credentials for identity
    resp = identity_client.get_credentials_for_identity(
        IdentityId=identity_id,
        Logins={provider_key: id_token},
    )
    creds = resp["Credentials"]
    log.info("AccessKeyId: %s", creds["AccessKeyId"])
    log.info("SecretKey: %s", creds["SecretKey"])
    log.info("SessionToken: %s", creds["SessionToken"])
    log.info("Expiration: %s", creds["Expiration"])

    # Print full credentials as shell exports for easy aws cli testing
    print("\n" + "="*70)
    print("COPY-PASTE TO TEST WITH AWS CLI:")
    print("="*70)
    print(f'export AWS_ACCESS_KEY_ID="{creds["AccessKeyId"]}"')
    print(f'export AWS_SECRET_ACCESS_KEY="{creds["SecretKey"]}"')
    print(f'export AWS_SESSION_TOKEN="{creds["SessionToken"]}"')
    print("="*70)
    print("Then run:")
    print("  aws iot describe-endpoint --endpoint-type iot:Data-ATS --region us-west-2")
    print("="*70 + "\n")

    # Also attach the IoT policy so this identity can use MQTT
    try:
        iot_client = boto3.client(
            "iot",
            region_name=COGNITO_REGION,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretKey"],
            aws_session_token=creds["SessionToken"],
        )
        iot_client.attach_policy(
            policyName=IOT_POLICY_NAME,
            target=identity_id,
        )
        log.info("✅ IoT policy '%s' attached to identity", IOT_POLICY_NAME)
    except Exception as e:
        log.warning("Policy attach result (may already be attached): %s", e)

    return creds


# ── Step 3: MQTT connection + shadow test ─────────────────────────────────────

class ZephyrMQTTTest:
    def __init__(self, creds: dict):
        self._creds = creds
        self._connection = None
        self._received_events = []
        self._event = threading.Event()

    def _on_message(self, topic, payload, dup, qos, retain, **kwargs):
        try:
            msg = json.loads(payload.decode())
            log.info("📨 Message received on topic: %s", topic)
            log.info("   Payload: %s", json.dumps(msg, indent=2))
            self._received_events.append({"topic": topic, "payload": msg})
            self._event.set()
        except Exception as e:
            log.error("Error decoding message: %s", e)
            log.error("Raw payload: %s", payload)

    def connect(self) -> bool:
        log.info("═══ Step 3: Connect to AWS IoT MQTT ═══")
        log.info("Endpoint: %s", IOT_ENDPOINT)
        log.info("Thing name: %s", THING_NAME)

        from awscrt import auth, io
        log.info("Building static credentials provider from Cognito creds...")
        log.info("  AccessKeyId: %s", self._creds["AccessKeyId"])
        log.info("  SecretKey (first 10): %s...", self._creds["SecretKey"][:10])
        log.info("  SessionToken: %s", self._creds["SessionToken"])

        credentials_provider = auth.AwsCredentialsProvider.new_static(
            access_key_id=self._creds["AccessKeyId"],
            secret_access_key=self._creds["SecretKey"],
            session_token=self._creds["SessionToken"],
        )
        log.info("Credentials provider created OK")

        client_id = f"hatest-{int(time.time())}"
        log.info("Client ID: %s", client_id)

        self._connection = mqtt_connection_builder.websockets_with_default_aws_signing(
            endpoint=IOT_ENDPOINT,
            region=COGNITO_REGION,
            credentials_provider=credentials_provider,
            client_id=client_id,
            clean_session=True,
            keep_alive_secs=30,
            on_connection_interrupted=self._on_interrupted,
            on_connection_resumed=self._on_resumed,
        )
        log.info("MQTT connection object built, attempting connect...")

        connect_future = self._connection.connect()
        connect_future.result(timeout=15)
        log.info("✅ MQTT connected!")
        return True

    def _on_interrupted(self, connection, error, **kwargs):
        log.warning("MQTT connection interrupted: %s", error)

    def _on_resumed(self, connection, return_code, session_present, **kwargs):
        log.info("MQTT connection resumed, return_code=%s", return_code)

    def subscribe_all(self):
        log.info("═══ Subscribing to shadow topics ═══")
        topics = [
            TOPIC_GET_ACCEPTED,
            TOPIC_GET_REJECTED,
            TOPIC_UPDATE_ACCPT,
            TOPIC_UPDATE_REJCT,
        ]
        for topic in topics:
            sub_future, _ = self._connection.subscribe(
                topic=topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_message,
            )
            sub_future.result(timeout=5)
            log.info("✅ Subscribed to: %s", topic)

    def get_shadow(self):
        log.info("═══ Requesting current shadow state ═══")
        self._event.clear()
        pub_future, _ = self._connection.publish(
            topic=TOPIC_GET,
            payload="{}",
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )
        pub_future.result(timeout=5)
        log.info("Published GET request to: %s", TOPIC_GET)
        # Wait for response
        if self._event.wait(timeout=10):
            log.info("✅ Shadow state received")
        else:
            log.warning("⚠️  No shadow response within 10 seconds")

    def send_command(self, key: str, value: int):
        log.info("═══ Sending command: %s = %d ═══", key, value)
        payload = json.dumps({"state": {"reported": {key: value}}})
        log.info("Publishing to: %s", TOPIC_UPDATE)
        log.info("Payload: %s", payload)
        self._event.clear()
        pub_future, _ = self._connection.publish(
            topic=TOPIC_UPDATE,
            payload=payload,
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )
        pub_future.result(timeout=5)
        log.info("Published command")
        if self._event.wait(timeout=10):
            log.info("✅ Command acknowledged")
        else:
            log.warning("⚠️  No acknowledgement within 10 seconds")

    def disconnect(self):
        if self._connection:
            disconnect_future = self._connection.disconnect()
            disconnect_future.result(timeout=5)
            log.info("MQTT disconnected cleanly")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("═══════════════════════════════════════════════════════════════")
    log.info("  Zephyr Connect MQTT Test")
    log.info("═══════════════════════════════════════════════════════════════")

    # Auth
    id_token, access_token = get_cognito_tokens()
    creds = get_aws_credentials(id_token)

    # MQTT
    client = ZephyrMQTTTest(creds)
    client.connect()
    client.subscribe_all()

    # Get current state
    client.get_shadow()
    time.sleep(2)

    # Test fan control — turn fan on speed 1
    log.info("═══════════════════════════════════════════════════════════════")
    log.info("  Testing fan speed control")
    log.info("  Sending fan=1 (speed 1)...")
    log.info("  WATCH YOUR HOOD — fan should turn on!")
    log.info("═══════════════════════════════════════════════════════════════")
    input("Press ENTER when ready to send fan=1 command...")
    client.send_command("fan", 1)
    time.sleep(3)

    input("Press ENTER to turn fan OFF (fan=0)...")
    client.send_command("fan", 0)
    time.sleep(3)

    # Test light
    log.info("═══════════════════════════════════════════════════════════════")
    log.info("  Testing light control")
    log.info("═══════════════════════════════════════════════════════════════")
    input("Press ENTER to turn light on (light=1)...")
    client.send_command("light", 1)
    time.sleep(3)

    input("Press ENTER to turn light OFF (light=0)...")
    client.send_command("light", 0)
    time.sleep(2)

    client.disconnect()

    log.info("═══════════════════════════════════════════════════════════════")
    log.info("  All events received during test:")
    for evt in client._received_events:
        log.info("  Topic: %s", evt["topic"])
        log.info("  Payload: %s", json.dumps(evt["payload"], indent=4))
    log.info("═══════════════════════════════════════════════════════════════")


if __name__ == "__main__":
    main()