# WSJT-X Remote

Lightweight browser remote control for WSJT-X using the documented UDP `NetworkMessage` protocol.

## Features

- Shows WSJT-X status and Band Activity in the browser.
- Sends CQ using `FreeText`: `CQ {DE call} {grid4}`.
- Replies to a selected decode with the WSJT-X `Reply` message.
- Sends Halt TX, Replay, Clear, and arbitrary free text commands.
- Provides a `/debug` page with raw UDP hex, parsed messages, parse errors, and manual command sending.
- Keeps a frontend-only Watch List: double-click an All Activity row or press Watch to add it.

Frequency switching is intentionally not implemented yet. The current WSJT-X UDP protocol does not expose a direct “set dial frequency” command.

## Layout

```text
backend/
  start.py
  requirements.txt
  wsjtx_remote/
    protocol.py
    state.py
    udp.py
    web.py
    app.py

frontend/
  package.json
  tsconfig.json
  index.html
  debug.html
  src/
  dist/
```

## Build With Nix

The frontend is built by Nix using `tsc` and `esbuild`. It has no npm install step.

Build the package:

```bash
nix build .#wsjt-remote
```

`nix build .` builds the same default package.

Build only the backend package:

```bash
nix build .#backend
```

## Install Backend

```bash
cd backend
python -m pip install -r requirements.txt
```

## Run

From the repository root:

```bash
python backend/start.py
```

With Nix:

```bash
nix run .#wsjt-remote
```

`nix run .` runs the same default package.

Run only the backend:

```bash
nix run .#backend
```

This starts one web service that serves both the frontend and backend API:

```text
http://127.0.0.1:8080/
```

Pass backend arguments after `--`:

```bash
nix run .#wsjt-remote -- --web-host 0.0.0.0
```

Load a read-only ADIF log for worked-before lookup:

```bash
nix run .#wsjt-remote -- --adif ./wsjtx_log.adi
```

Load Python plugins:

```bash
nix run .#wsjt-remote -- --plugin-dir ./plugins
```

`--plugin-decode-grace` controls how long the backend waits after the last decode in the same WSJT/JTDX time slot before calling plugin batch logic. The default is `1.0` second.

Forward raw received WSJT-X/JTDX UDP packets to another UDP listener:

```bash
nix run .#wsjt-remote -- --udp-forward 192.0.2.10:2333
```

IPv6 targets must use brackets. Repeat `--udp-forward` to send each raw packet to multiple targets:

```bash
nix run .#wsjt-remote -- --udp-forward '[fd00::1]:2333' --udp-forward 192.0.2.10:2333
```

Logs are written to stderr. Use `--log-level` to change verbosity:

```bash
nix run .#wsjt-remote -- --log-level DEBUG
```

Open:

```text
http://127.0.0.1:8080/
http://127.0.0.1:8080/debug
```

To access from another device on your LAN:

```bash
python backend/start.py --web-host 0.0.0.0
```

Do not expose this service to the public internet. It has no authentication and can transmit through WSJT-X.

## WSJT-X Settings

In WSJT-X:

```text
Settings -> Reporting
UDP Server: backend host IP
UDP Server port number: 2237
Accept UDP requests: enabled
```

If WSJT-X and this service run on the same machine, use:

```text
UDP Server: 127.0.0.1
UDP Server port number: 2237
```

## Frontend Backend URL

If the frontend is served by the backend, no configuration is needed.

If the frontend is hosted elsewhere, set the backend URL at the top of the page, for example:

```text
http://192.168.1.20:8080
```

The value is stored in browser `localStorage`.

## CQ And Enable Tx

The `CQ` button uses Niri IPC to focus the WSJT-X/JTDX main window and sends keyboard shortcuts with `wtype`. If the current DX call/grid is populated, it sends `F4` before `Alt+N`; otherwise it sends only `Alt+N`.

## Plugins

Pass `--plugin-dir` to load Python files from a local plugin directory. Files are loaded in filename order, and files starting with `_` are ignored. Plugins run with the same permissions as the backend; this is not a sandbox.

Plugins may define any of these functions:

```python
def on_start(ctx):
    pass

def on_status(ctx, status):
    pass

def on_decode(ctx, decode):
    pass

def on_logged_adif(ctx, raw_adif, indexed_count):
    pass

def on_decode_batch(ctx, decodes):
    return None

def on_stop(ctx):
    pass
```

`on_decode` runs after the backend has calculated DXCC and worked-before fields. Plugins can change display-only fields such as:

```python
decode["dxcc_label"] = "Priority"
decode["plugin_color"] = "nord14"
decode["plugin_note"] = "local rule matched"
```

Worked-before fields are restored after `on_decode`, so plugins cannot affect ADIF worked calculations.

`on_decode_batch` runs after a WSJT/JTDX decode time slot appears complete. The backend groups decodes by `decode["time"]` and waits `--plugin-decode-grace` seconds after the last decode in that slot. Return `None`, a decode dict, or a decode index. The first plugin returning a valid decode triggers one Reply for that batch.

The plugin context exposes helpers and read-only worked sets:

```python
ctx.status
ctx.remote
ctx.adif.worked_calls
ctx.adif.worked_calls_by_band
ctx.adif.worked_grids
ctx.adif.worked_grids_by_band
ctx.adif.worked_dxcc
ctx.adif.worked_dxcc_by_band
ctx.extract_callsign(message)
ctx.extract_grid(message)
ctx.is_cq(message)
ctx.is_calling_own(message)
ctx.current_band()
ctx.worked_call(call, band=None)
ctx.worked_grid(grid, band=None)
ctx.worked_dxcc(call_or_key, band=None)
ctx.reply(decode, modifiers=0)
```

Example:

```python
def on_decode(ctx, decode):
    if ctx.is_cq(decode["message"]):
        decode["plugin_color"] = "nord14"

def on_decode_batch(ctx, decodes):
    for decode in decodes:
        call = ctx.extract_callsign(decode["message"])
        if call and ctx.is_cq(decode["message"]) and not ctx.worked_call(call, band=ctx.current_band()):
            return decode
    return None
```

See `plugins/china_province.py` for a test plugin that replaces China DXCC labels with a province label and colors first-worked province rows with Nord blue.

See `plugins/japan_prefecture.py` for a test plugin that replaces Japan DXCC labels with Chinese WAJA prefecture names. It downloads JJ1WTL's offline JA callbook CSV, caches it under `/tmp/wsjt-remote/plugins/japan_prefecture`, and refreshes the cache every 30 days.

See `plugins/wwa.py` for a World Wide Award helper. Place `wwa_stations.txt` next to the plugin, with one callsign per line. Listed stations are highlighted while they have not been worked on the UTC day and band from `decode["received_at"]`. Set `WWA_AUTO_REPLY=1` to let the plugin reply only while TX is Idle. Direct callers are handled first: if any station is calling your callsign, the plugin replies to the strongest one by SNR without requiring WWA membership and without using pending or blacklist state. If no direct caller exists, it chooses unworked WWA CQ stations on the current day/band by highest SNR. Worked keys are recorded only from `LoggedADIF`, persisted as JSON in `/tmp/wsjt-remote/plugins/wwa_worked.json`, and pruned to the current UTC day on startup. If an auto-replied WWA CQ station is not logged before TX returns to Idle, that day/band/call key is blacklisted in memory for 30 minutes.

The backend also runs a core FT8 watchdog. Any non-Idle Status starts a 150-second countdown, TX Idle disarms it, and a decode only resets it when the station calling your callsign matches the current DX call. Manual/API Reply and plugin Reply also reset the countdown if it is already running. If it expires while TX is still not Idle, the backend logs a warning and sends Halt TX.

## Development

Backend syntax check:

```bash
cd backend
python -m compileall wsjtx_remote
```
