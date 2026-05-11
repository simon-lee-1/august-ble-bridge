#!/usr/bin/env python3
"""August/Yale BLE-to-MQTT bridge for Home Assistant.

Connects to an August smart lock via BLE using the offline key,
publishes state to MQTT with HA auto-discovery, and accepts
lock/unlock commands.
"""

import sdnotify
_sd = sdnotify.SystemdNotifier()

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv
from yalexs_ble import LockState, PushLock, ConnectionInfo, LockInfo
from yalexs_ble.const import LockStatus, DoorStatus
import paho.mqtt.client as mqtt

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOCK_NAME = os.environ["LOCK_NAME"]
LOCK_MAC = os.environ["LOCK_MAC"]
LOCK_KEY = os.environ["LOCK_KEY"]
LOCK_KEY_SLOT = int(os.environ["LOCK_KEY_SLOT"])
LOCK_ID = os.environ["LOCK_ID"]

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "august")
MQTT_PASS = os.environ.get("MQTT_PASS", "august2026")

TOPIC_PREFIX = f"august_lock/{LOCK_NAME.replace(' ', '_').lower()}"
DISCOVERY_PREFIX = "homeassistant"
AVAILABILITY_TOPIC = f"{TOPIC_PREFIX}/availability"

RECONNECT_DELAY_MIN = 10
RECONNECT_DELAY_MAX = 300

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("august-ble-bridge")

# ---------------------------------------------------------------------------
# MQTT Setup
# ---------------------------------------------------------------------------


def setup_mqtt() -> mqtt.Client:
    client = mqtt.Client(client_id=f"august_ble_{LOCK_NAME}")
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.will_set(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)
    client.reconnect_delay_set(5, 120)
    return client


def publish_discovery(client: mqtt.Client):
    """Publish HA MQTT auto-discovery configs."""
    device_info = {
        "identifiers": [f"august_{LOCK_ID}"],
        "name": f"August {LOCK_NAME}",
        "manufacturer": "August",
        "model": "Wi-Fi Smart Lock",
        "sw_version": "1.1.30",
    }
    availability = {
        "topic": AVAILABILITY_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
    }

    # Lock entity
    lock_config = {
        "name": LOCK_NAME.title(),
        "unique_id": f"august_{LOCK_ID}_lock",
        "command_topic": f"{TOPIC_PREFIX}/set",
        "state_topic": f"{TOPIC_PREFIX}/state",
        "payload_lock": "LOCK",
        "payload_unlock": "UNLOCK",
        "state_locked": "locked",
        "state_unlocked": "unlocked",
        "optimistic": False,
        "device": device_info,
        "availability": availability,
    }
    client.publish(
        f"{DISCOVERY_PREFIX}/lock/august_{LOCK_ID}/config",
        json.dumps(lock_config),
        qos=1,
        retain=True,
    )

    # Battery sensor
    battery_config = {
        "name": f"{LOCK_NAME.title()} Battery",
        "unique_id": f"august_{LOCK_ID}_battery",
        "state_topic": f"{TOPIC_PREFIX}/battery",
        "unit_of_measurement": "%",
        "device_class": "battery",
        "state_class": "measurement",
        "device": device_info,
        "availability": availability,
    }
    client.publish(
        f"{DISCOVERY_PREFIX}/sensor/august_{LOCK_ID}_battery/config",
        json.dumps(battery_config),
        qos=1,
        retain=True,
    )

    # Door sensor
    door_config = {
        "name": f"{LOCK_NAME.title()} Door",
        "unique_id": f"august_{LOCK_ID}_door",
        "state_topic": f"{TOPIC_PREFIX}/door",
        "payload_on": "open",
        "payload_off": "closed",
        "device_class": "door",
        "device": device_info,
        "availability": availability,
    }
    client.publish(
        f"{DISCOVERY_PREFIX}/binary_sensor/august_{LOCK_ID}_door/config",
        json.dumps(door_config),
        qos=1,
        retain=True,
    )

    log.info("MQTT discovery configs published")
    _sd.notify("READY=1")


# ---------------------------------------------------------------------------
# BLE Lock Control
# ---------------------------------------------------------------------------


class AugustBLEBridge:
    def __init__(self, mqtt_client: mqtt.Client):
        self.mqtt = mqtt_client
        self.lock: PushLock | None = None
        self._running = True
        self._command_queue: asyncio.Queue = asyncio.Queue()

    def on_mqtt_message(self, client, userdata, msg):
        """Handle MQTT command messages."""
        payload = msg.payload.decode().upper()
        if payload in ("LOCK", "UNLOCK"):
            log.info(f"MQTT command received: {payload}")
            asyncio.get_event_loop().call_soon_threadsafe(
                self._command_queue.put_nowait, payload
            )

    def _state_callback(self, state: LockState, lock_info: LockInfo, connection_info: ConnectionInfo):
        """Called when lock state changes."""
        log.info(f"Lock state update: lock={state.lock}, door={state.door}, battery={state.battery}")

        # Publish lock state
        if state.lock == LockStatus.LOCKED:
            self.mqtt.publish(f"{TOPIC_PREFIX}/state", "locked", qos=1, retain=True)
        elif state.lock == LockStatus.UNLOCKED:
            self.mqtt.publish(f"{TOPIC_PREFIX}/state", "unlocked", qos=1, retain=True)

        # Publish door state
        if state.door is not None:
            if state.door in (DoorStatus.OPENED, DoorStatus.AJAR):
                self.mqtt.publish(f"{TOPIC_PREFIX}/door", "open", qos=1, retain=True)
            elif state.door == DoorStatus.CLOSED:
                self.mqtt.publish(f"{TOPIC_PREFIX}/door", "closed", qos=1, retain=True)

        # Publish battery percentage
        if state.battery is not None:
            self.mqtt.publish(
                f"{TOPIC_PREFIX}/battery", str(state.battery.percentage), qos=1, retain=True
            )

    async def run(self):
        """Main BLE connection loop with passive scan."""
        from bleak import BleakScanner

        while self._running:
            try:
                # Passive scan: wait until we actually see the lock advertising
                log.info(f"Scanning for lock {LOCK_NAME} ({LOCK_MAC})...")
                device = None
                while self._running and device is None:
                    found = await BleakScanner.discover(timeout=10, return_adv=True)
                    for addr, (dev, adv) in found.items():
                        if addr.upper() == LOCK_MAC.upper():
                            device = dev
                            log.info(f"Lock found (RSSI: {adv.rssi})")
                            break
                    if device is None:
                        _sd.notify("WATCHDOG=1")
                        await asyncio.sleep(5)

                if not self._running:
                    break

                log.info(f"Connecting to lock {LOCK_NAME} at {LOCK_MAC}...")
                self.lock = PushLock(
                    address=LOCK_MAC,
                    key=LOCK_KEY,
                    key_index=LOCK_KEY_SLOT,
                    always_connected=True,
                )

                self._cancel_lock = await self.lock.start()
                await self.lock.wait_for_first_update(timeout=30.0)
                self.lock.register_callback(self._state_callback)
                log.info("BLE connected to lock")
                if self.lock.lock_state:
                    self._state_callback(
                        self.lock.lock_state,
                        self.lock.lock_info,
                        self.lock.connection_info,
                    )
                self.mqtt.publish(AVAILABILITY_TOPIC, "online", qos=1, retain=True)

                # Process commands while connected
                while self._running and self.lock.is_connected:
                    try:
                        cmd = await asyncio.wait_for(
                            self._command_queue.get(), timeout=30
                        )
                        if cmd == "LOCK":
                            log.info("Locking...")
                            await self.lock.lock()
                            log.info("Lock command sent")
                        elif cmd == "UNLOCK":
                            log.info("Unlocking...")
                            await self.lock.unlock()
                            log.info("Unlock command sent")
                    except asyncio.TimeoutError:
                        _sd.notify("WATCHDOG=1")
                        pass
                    except Exception as e:
                        log.error(f"Command error: {e}")

                log.warning("BLE connection lost")
                self.mqtt.publish(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)

            except Exception as e:
                log.error(f"BLE error: {e}")
                self.mqtt.publish(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)

            await asyncio.sleep(10)

    def stop(self):
        self._running = False
        if self.lock:
            log.info("Disconnecting from lock...")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    log.info(f"August BLE Bridge starting — lock={LOCK_NAME}, mac={LOCK_MAC}")

    # Setup MQTT
    mqtt_client = setup_mqtt()
    mqtt_client.loop_start()

    try:
        mqtt_client.connect(MQTT_HOST, MQTT_PORT)
    except Exception as e:
        log.warning(f"Initial MQTT connect failed: {e} (will retry)")

    # Wait for MQTT connection
    await asyncio.sleep(2)

    publish_discovery(mqtt_client)
    mqtt_client.subscribe(f"{TOPIC_PREFIX}/set", qos=1)

    # Setup bridge
    bridge = AugustBLEBridge(mqtt_client)
    mqtt_client.on_message = bridge.on_mqtt_message

    # Handle shutdown
    loop = asyncio.get_event_loop()

    def shutdown_handler():
        log.info("Shutdown signal received")
        bridge.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_handler)

    # Run
    await bridge.run()

    # Cleanup
    mqtt_client.publish(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    log.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
