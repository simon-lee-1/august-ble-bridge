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

The offline key must be obtained from the August API. You can use the [yalexs](https://github.com/bdraco/yalexs) library directly, or use a helper script:

### Using yalexs (Python)

```bash
pip install yalexs
python3 -c "
from yalexs.api import Api
from yalexs.authenticator import Authenticator, AuthenticationState, ValidationResult
import json

api = Api()
auth = Authenticator(api, 'email', 'your@email.com', 'YourPassword', access_token_cache_file='token.json')
state = auth.authenticate()

# If state == REQUIRES_VALIDATION, check your email for a code:
# auth.send_verification_code()
# auth.validate_verification_code('123456')
# state = auth.authenticate()  # retry after validation

api_instance = api
locks = api_instance.get_locks(auth.get_access_token())
for lock in locks:
    print(f'Lock: {lock.device_name}, ID: {lock.device_id}')
    keys = api_instance.get_lock_detail(auth.get_access_token(), lock.device_id)
    # Offline key is in the lock's key list
"
```

> **Note:** The yalexs library's built-in `AuthenticatorAsync` may return 403 errors due to API key changes. If so, use the August API directly with the key `79fd0eb6-381d-4adf-95a0-47721289d1d9` and header `x-august-api-key`.

### Alternative: august2mqtt key extractor

The [august2mqtt](https://github.com/codyc1515/august2mqtt) project includes key extraction tooling. Clone it and follow its auth instructions to extract offline keys.

### What you need

After extraction, you'll have:
- **Offline key**: 32-character hex string (e.g. `a1b2c3d4e5f6...`)
- **Key slot**: integer (usually 0-4)
- **Lock ID**: UUID of the lock (e.g. `4A133E63-1ED7-4031-8389-9A55750A245C`)

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
