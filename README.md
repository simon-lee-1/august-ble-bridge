# August BLE-to-MQTT Bridge

Connects to an August/Yale smart lock via BLE using an offline key, publishes state to MQTT with Home Assistant auto-discovery, and accepts lock/unlock commands.

## Why

August locks without a WiFi bridge (or with a dead one) can only be controlled via BLE. This bridge runs on a Linux host with a Bluetooth adapter and exposes the lock to Home Assistant via MQTT — no cloud, no WiFi bridge needed.

## Features

- **Offline BLE control** — uses extracted offline key, no cloud dependency
- **MQTT + HA auto-discovery** — lock, battery sensor, and door sensor appear automatically
- **Persistent connection** — stays connected via `always_connected` mode for instant state updates
- **Auto-reconnect** — exponential backoff on disconnection, handles BLE flakiness
- **Lock/Unlock commands** — via MQTT from HA UI or automations

## Home Assistant Entities

| Entity | Type | Description |
|--------|------|-------------|
| lock.august_{name} | lock | Lock/unlock control |
| sensor.august_{name}_battery | sensor | Battery percentage |
| binary_sensor.august_{name}_door | binary_sensor | Door open/closed (if DoorSense installed) |

## Prerequisites

- Linux host with Bluetooth 4.0+ adapter
- BlueZ stack (D-Bus based)
- August/Yale smart lock with extracted offline key
- MQTT broker (e.g. Mosquitto)

## Extracting the Offline Key

The offline key must be obtained from the August API using your account credentials:

1. Authenticate with August API (email + 2FA verification code)
2. List locks on account
3. Extract the offline key and key slot from the lock's key list

The key is a 32-character hex string with an associated slot number.

## Setup

### 1. Configure

```bash
cp .env.example .env
# Edit .env with your lock details and MQTT credentials
```

### 2. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Run

```bash
./venv/bin/python3 august_ble_bridge.py
```

### 4. Systemd service (recommended)

```bash
sudo cp august-ble-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now august-ble-bridge
```

## Configuration (.env)

```env
LOCK_NAME=front_door
LOCK_MAC=AA:BB:CC:DD:EE:FF
LOCK_KEY=<32-char hex offline key>
LOCK_KEY_SLOT=0
LOCK_ID=<lock UUID from August API>
MQTT_HOST=192.168.1.x
MQTT_PORT=1883
MQTT_USER=
MQTT_PASS=
```

## How it works

1. Creates a `PushLock` instance with the lock's BLE address and offline key
2. `PushLock.start()` discovers the device via BlueZ D-Bus cache and connects
3. Establishes encrypted BLE session using the offline key
4. Receives state updates (lock status, battery, door) via BLE notifications
5. Publishes state to MQTT with retained messages
6. Listens for LOCK/UNLOCK commands on MQTT and sends via BLE

### Notes

- The lock advertises BLE intermittently. On first boot (or after Bluetooth restart), the first connection attempt may time out while waiting for an advertisement. The retry loop handles this automatically.
- `always_connected=True` keeps the BLE connection alive for instant state updates and command responsiveness.
- The callback must be registered after `wait_for_first_update()` to avoid a race condition where `connection_info` is None.

## Tested Hardware

- August Wi-Fi Smart Lock (AUG-SL05-M01-S01) — advertises as "L5000W2"
- Intel Bluetooth 5.2 adapter (USB, BlueZ 5.x)

## Credits & References

- [yalexs-ble](https://github.com/bdraco/yalexs-ble) — BLE communication library for August/Yale locks
- [yalexs](https://github.com/bdraco/yalexs) — August API client (used for key extraction)
- [bleak](https://github.com/hbldh/bleak) — Cross-platform BLE library for Python
- [paho-mqtt](https://github.com/eclipse/paho.mqtt.python) — MQTT client library

## License

MIT
