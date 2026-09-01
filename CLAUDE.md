# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python library and set of standalone tools for configuring and pulling
media from IP cameras/DVRs that speak the **DVR-IP / NetSurveillance /
"Sofia" protocol** (Xiongmai XMeye / NETsurveillance ActiveX SDK). There
is no build system beyond `setup.py`, no test suite, and no pinned
runtime dependencies — the core library is pure standard-library Python 3
(≥3.6, Python 3 only).

## Running things

Everything is run directly with `python3 <script>.py`. There are no
lint/test/build commands. Key entry points:

- `python3 DeviceManager.py [-q] [-n] [command;command;...]` — discover
  and configure devices on the LAN. Uses Tkinter if present, otherwise
  falls back to a console interface (`-n` forces console). Its
  command-string interface (`search`, `table`, `json`, ...) is documented
  in its `help` string.
- `./monitor.py <CAMERA_IP> <CAMERA_NAME> <FILE_PATH>` — persistently
  reconnect and record split audio/video into `%Y/%m/%d` 10-minute chunks.
- `python3 NVRVideoDownloader.py` — download recorded clips for a time
  range; config from `NVRVideoDownloader.json` (or path in
  `$NVRVIDEODOWNLOADER_CFG`).
- `python3 download-local-files.py` — solar/battery-camera pull loop; the
  Docker `CMD`. Reads config from the JSON file at `$CONFIG_PATH`. (An
  individual-env-var fallback exists in `load_config()` but is currently
  broken — it does `Path(config_path)` with `config_path=None` when the
  var is unset, and returns a plain dict where `main()` expects
  attribute access — so treat `$CONFIG_PATH` as required.)
- `python3 AlarmServer.py [port]` — standalone TCP server that logs
  `AlarmInfo` packets pushed by cameras configured to report to an
  external alarm center.
- `python3 telnet_opener.py <ip> [-t|-b]` — root exploit tooling (see
  below).

### Optional dependencies

Not required by the core library, but individual features/README examples
need them: `Pillow` (title bitmaps), `deepdiff` (settings diffing),
`ffmpeg` (Docker image, for muxing). The `examples/socketio/` app has its
own `requirements.txt` and is self-contained.

### Docker / CI

`Dockerfile` runs `download-local-files.py`. `.github/workflows/main.yml`
builds and pushes that image to Docker Hub on every branch push;
`codeql.yml` runs CodeQL Python analysis on master.

## Architecture

**`dvrip.py` is the core.** It defines `DVRIPCam`, a synchronous client
that owns the TCP connection (control port `34567`, UDP discovery `34568`)
and implements the wire protocol. Everything else in the repo is a
consumer of this class. `asyncio_dvrip.py` is a **parallel async
reimplementation** of the same class and protocol (methods become
`async def`, `login` takes the event loop) — when you change protocol
logic in one, check whether the other needs the same change; they are not
DRY-shared.

### The wire protocol (the part that requires reading the code)

- Every packet starts with the same 20-byte binary header, packed as
  `struct "BB2xII2xHI"` = magic `0xFF`, version, **session id**, packet
  count, **message id (QCODE)**, body length. **Control** packets carry a
  UTF-8 JSON body terminated with `\x0a\x00` (v0) or `\x00` (v1).
  **Media** packets (monitor stream, snapshot, file/firmware downloads)
  carry raw binary bodies instead — decoded by `reassemble_bin_payload()`,
  which reads its own per-frame stream header (data-type/codec + packed
  timestamp), not JSON. Don't route binary responses through JSON parsing.
- **`QCODES`** (dict on `DVRIPCam`) maps human names → numeric message
  ids. `OPFEED_QCODES` holds the pet-feeder codes with separate SET/GET
  variants. To add a command you generally add a QCODE and a thin wrapper
  method.
- **Auth:** passwords are hashed with `sofia_hash()` — an XM-specific
  8-char digest over the MD5 (not a standard hash; don't substitute).
  `login()` (QCODE 1000) returns a session id used in all later headers;
  `keep_alive()` must run to hold the session.
- `send()` / `send_custom()` serialize+frame a request and, by default
  (`wait_response=True`), block for the reply (`socket_recv(20)` header
  then body). Passing `wait_response=False` sends fire-and-forget and
  reads nothing — snapshot and monitor startup use this, then pull their
  binary streams separately via `reassemble_bin_payload()`. `get_file()` /
  `get_specific_size()` handle bulk/binary responses (snapshots, file
  downloads, firmware). `OK_CODES = [100, 515]` indicate success in `Ret`.

### The config-tree convention

Camera settings are a nested JSON tree addressed by **dotted paths**:
`get_info(path)` / `set_info(path, data)` where path looks like
`"Simplify.Encode"`, `"Camera.Param.[0]"`, `"NetWork.NetDHCP"`,
`"Detect"`, `"fVideo.OSDInfo"`. `[n]` indexes a per-channel array. Named
convenience wrappers (`get_system_info`, `get_detect_info`,
`get_general_info`, ...) are just typed shortcuts over `get_info`/`send`.
Writes can be **sparse** — omitted fields keep their current values — so
the idiom is often get→mutate→set, or set just the subtree you want. The
README's DeepDiff loop is the intended way to reverse-engineer which path
a UI setting maps to.

### Streaming and events

- `start_monitor(callback, user)` / `stop_monitor()` — `start_monitor`
  runs its receive loop **synchronously on the calling thread**, invoking
  `callback(frame, meta, user)` per frame until `self.monitoring` is
  cleared; it does **not** spawn a thread, so it blocks the caller. To
  stop it you either call `stop_monitor()` from inside the callback or run
  `start_monitor` on your own worker thread and call `stop_monitor()` from
  another. The caller decides what to do with each frame (write
  H.264/H.265, count frames, etc.). `snapshot()` grabs a single JPEG.
  (`alarmStart()` is the method that actually spawns a background
  `threading.Thread`; don't conflate `start_monitor`'s inline loop with
  it, or with the `asyncio_dvrip.py` client.)
- Alarms: `setAlarm(fn)` + `alarmStart()` register a handler and start
  `alarm_thread`, which reads `AlarmInfo` (QCODE 1504) pushed over the
  existing session. **Important quirk:** some firmwares only emit to a
  separately-configured external alarm server (`AlarmServer.py`) and will
  silently deliver zero in-session callbacks even when motion is
  detected — this is a firmware split, not a bug in the handler.

### Higher-level consumers

`NVR.py` wraps `DVRIPCam` for multi-channel NVRs and is used by
`NVRVideoDownloader.py`. `solarcam.py` wraps it for
battery/solar cameras and is used by `download-local-files.py`.
`monitor.py`, `connect.py` (an OSD scratch/example script) sit directly on
`DVRIPCam`. All of them construct `DVRIPCam(ip, user=, password=)`,
`login()`, act, `close()`, and treat `SomethingIsWrongWithCamera` as the
connection-failure signal.

### telnet_opener.py (root access tooling)

Separate from the protocol client. Uses the `OPSystemUpgrade` /
`InstallDesc` mechanism to run shell commands as root on Xiongmai
firmware — enable telnet, open a transient shell (`-t`), or run a
self-contained `ipctool` hardware backup to an NFS share (`-b`). Note:
the `InstallDesc` shell-injection path is **blocked on XM firmware built
after 2020-05-07**, which affects what this tool can do on newer cameras.

## Conventions when extending

- Protocol/wire changes belong in `dvrip.py`; mirror into
  `asyncio_dvrip.py` if the async client needs them.
- Add new device commands as a `QCODES` entry plus a small wrapper method
  that calls `send`/`get_info`/`set_info`; follow the existing sparse-set
  and per-channel `[n]` conventions.
- These are undocumented vendor protocols — behavior varies by firmware.
  Prefer probing a real device (the DeepDiff loop) over assuming a field
  exists, and guard firmware-specific paths.
