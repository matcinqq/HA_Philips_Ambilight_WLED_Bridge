# Philips Ambilight to WLED Bridge

Python bridge for Philips Titan OS JointSpace Ambilight processed RGB data to a segmented WLED installation.

The bridge uses the Philips `/6/ambilight/processed` endpoint as the live source, maps side zones to 10 logical Ambilight segments, renders a smoothed live frame, and restores the normal WLED preset when it exits. JSON is still used for WLED setup and diagnostics; the preferred live-frame backend is DDP over UDP.

## Physical Mapping

The WLED Ambilight layout is expected to exist as preset `2` and the normal/default layout as preset `1`.

Segment mapping:

| Segment | Name | LEDs |
| --- | --- | --- |
| 0 | left lower | 21..29 |
| 1 | left middle | 30..37 |
| 2 | left upper | 38..46 |
| 3 | right lower | 72..76 |
| 4 | right middle | 77..81 |
| 5 | right upper | 82..85 |
| 6 | bottom outer-left | 10..20 |
| 7 | bottom center-left | 0..9 |
| 8 | bottom center-right | 47..58 |
| 9 | bottom outer-right | 59..71 |

In `ddp_pixels` mode the 10 logical segment colors are expanded into one 86-pixel RGB frame and sent over UDP DDP. In `json_segments` fallback mode the bridge updates segment colors by segment ID and does not recreate segment ranges every frame.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp config.example.yaml config.yaml
cp .env.example .env
```

Edit `.env` with the existing Philips digest credentials. The real `.env` file is ignored by git.
The config file can reference host/IP values from `.env`:

```yaml
tv:
  host: "${PHILIPS_TV_HOST}"
wled:
  host: "${WLED_HOST}"
```

TLS verification defaults to `false` because Philips TVs commonly expose the secured local API with a self-signed or otherwise untrusted LAN certificate.

## Pairing the Philips TV

Pairing is only needed once, or after the TV is reset, credentials are revoked, or existing credentials stop working. It is not part of normal bridge startup.

To pair from the laptop:

```bash
PYTHONPATH=src python -m ambilight_wled_bridge pair --tv-ip YOUR_TV_IP --test
```

The TV will show a PIN. Enter it at the prompt. The command prints a generated `tv_username` and `tv_password`; copy those into Home Assistant add-on options or into your local `.env` as `PHILIPS_TV_USER` and `PHILIPS_TV_PASS`.

Changing the TV IP does not necessarily require re-pairing. If the username/password remain valid, just update the host/IP.

## Commands

```bash
ambilight-wled-bridge check
ambilight-wled-bridge pair --tv-ip YOUR_TV_IP
ambilight-wled-bridge once
ambilight-wled-bridge once --raw --timing
ambilight-wled-bridge once --source measured --raw --timing
ambilight-wled-bridge once --send
ambilight-wled-bridge debug-frame
ambilight-wled-bridge debug-timing --segment 0 --duration 5
ambilight-wled-bridge backend-info
ambilight-wled-bridge debug-ddp-pixels
ambilight-wled-bridge raw-passthrough --dry-run
ambilight-wled-bridge raw-passthrough
ambilight-wled-bridge test-ddp
ambilight-wled-bridge run
ambilight-wled-bridge restore-normal
```

You can also run without installing the console script:

```bash
PYTHONPATH=src python -m ambilight_wled_bridge check
```

`run` loads WLED preset `2`, normalizes WLED once through JSON, polls the TV at the configured Philips interval, renders smoothed frames to the selected output backend at the configured WLED interval, and restores preset `1` on graceful shutdown.

After prolonged TV read failure, the default behavior is to restore preset `1` once, keep retrying the TV, then reload preset `2` when Ambilight data returns.

On Ambilight activation the bridge forces WLED `on: true`, global `bri: 255`, `tt: 0`, and for segments `0..9` forces `on: true`, `bri: 255`, `frz: false`, and `fx: 0`. WLED transitions remain disabled; the bridge generates the intermediate colors itself.

For live frames, use DDP to bypass HTTP JSON overhead:

```yaml
output:
  backend: ddp_pixels

ddp:
  host: "${WLED_HOST}"
  port: 4048
  pixel_count: 86
```

The DDP backend sends RGB only, not RGBW. It expands the 10 logical segment colors to the physical 86-pixel layout and sends one DDP packet per rendered frame. JSON remains available as a fallback/debug backend:

```yaml
output:
  backend: json_segments
```

In JSON fallback mode the known-good color baseline is `[R,G,B,0]`: RGB is copied from the processed Ambilight frame after mapping and bridge-side smoothing, and W is explicitly zero.

`check` reads WLED `/json/cfg` and warns if `light.gc.col` is far from `1.0`. This bridge is calibrated for WLED color gamma around `1.0`; higher gamma values such as `2.8` can crush low values and make dominant channels look too harsh.

Use `debug-frame` when color values look wrong. It prints one Philips frame through the full bridge pipeline: raw TV zones, side-zone mapping, synthetic bottom colors, disabled transform stages, max brightness, smoothing state, intensity compression details, final RGBW, and the exact WLED JSON payload.

Use `debug-timing` when motion looks steppy or laggy. It samples the TV and prints one segment's target RGB, smoothed float RGB, final RGBW bytes, render `dt`, and smoothing `alpha` without sending anything to WLED:

```bash
PYTHONPATH=src python -m ambilight_wled_bridge debug-timing --segment 0 --duration 5
```

Use `backend-info` to confirm live output selection, and `debug-ddp-pixels` to inspect how one current Ambilight frame expands into DDP pixel ranges without sending UDP frames.

Use `test-ddp` to send static DDP patterns for checking pixel order:

```bash
PYTHONPATH=src python -m ambilight_wled_bridge test-ddp --pattern segments --duration 3
```

Use `raw-passthrough` to separate bridge transforms from WLED behavior. It disables bridge transforms for one frame, sends each segment as `[r,g,b,0]`, forces `bri: 255`, `tt: 0`, segment `bri: 255`, `fx: 0`, `frz: false`, then immediately reads `/json/state` and prints sent vs read-back values. Start with `--dry-run` if you only want to inspect the exact payload.

Avoid using WLED global brightness or `max_brightness` as the main visual calibration. Keep WLED at `bri: 255` and use bridge smoothing plus optional peak-intensity compression first.

```yaml
bridge:
  max_brightness: 255
```

For smoother color changes without WLED-side transition latency, use the two-rate timing pipeline. Philips polling defaults to 50 ms, which is about 20 Hz. WLED rendering defaults to 33 ms, which is about 30 Hz. The renderer keeps moving the displayed color toward the latest Philips target even between TV polls:

```yaml
timing:
  philips_poll_interval_ms: 50
  wled_render_interval_ms: 33

smoothing:
  enabled: true
  time_constant_ms: 120
```

Smoothing is time-based: `alpha = 1 - exp(-dt / time_constant)`. Raise `time_constant_ms` for softer, slower changes; lower it for tighter tracking. The first successful frame initializes directly to the Philips target, so startup does not fade in from black. The smoother stores float RGB state internally and rounds only at final output serialization, so repeated smoothing does not throw away low-level values. W is forced back to zero in the baseline path.

Deadband and send-threshold skipping are not used by the live loop; every render tick sends the current smoothed frame. Keep WLED transition time at `0`.

Optional peak-intensity compression reduces harsh output without changing hue as much as per-channel curves. It maps only the peak channel through a curve, then scales all RGB channels by the same factor:

```yaml
intensity_compression:
  enabled: false
  method: "peak"
  strength: 1.0
  curve:
    - [0, 0]
    - [5, 5]
    - [10, 10]
    - [20, 19]
    - [40, 32]
    - [60, 42]
    - [80, 52]
    - [100, 62]
    - [150, 90]
    - [200, 125]
    - [255, 165]
```

Compression is disabled by default so exact RGB transport remains easy to verify. Enable it after smoothing is visually acceptable.

RGBW extraction remains available for future experiments, but it is not part of the default path. Do not enable `white_extraction: "min_rgb"` for the current RGB baseline; it subtracts the common RGB component and can remove the weakest channel entirely. For now RGBW strips should still receive explicit four-channel colors:

```yaml
wled:
  use_white_channel: true

bridge:
  white_extraction: "none"
```

Enable `black_floor_white` if very dark nonzero Ambilight frames should use a dim white floor instead of dropping to an imperceptible dark color. Exact `[0,0,0]` remains off. `black_floor_threshold` controls how dark a color must be to trigger the floor, and `black_floor_white_level` is the 0..255 output level; `5` is about 2%, `12` is about 5%, and `25` is about 10%.

WLED's JSON API supports RGBW segment color arrays with a fourth byte for white.

At `-vv`, bridge timing logs are throttled by `timing_log_interval_seconds`; third-party HTTP debug logs remain hidden. Use `-vvv` only when you explicitly need low-level urllib3 request logging.

## Home Assistant App

The Home Assistant app/add-on wrapper lives in:

```text
addons/ambilight_wled_bridge/
```

It builds a container that runs the same Python package and CLI as the laptop workflow. The add-on reads Home Assistant options from `/data/options.json`, writes `/data/bridge-config.yaml` inside the container, then runs:

```bash
python3 -m ambilight_wled_bridge -c /data/bridge-config.yaml run
```

To use it locally, copy `addons/ambilight_wled_bridge` into your Home Assistant `/addons` directory, install it from local apps/add-ons, fill in the TV/WLED options, and start it. The add-on stores Philips credentials only in Home Assistant options, not in repository code.

For pairing from Home Assistant, set add-on `mode` to `pair`, set `tv_host`, start the add-on, enter the PIN shown on the TV in the add-on log/terminal prompt, copy the printed credentials into `tv_username` and `tv_password`, then switch `mode` back to `bridge`.

After changing Python bridge code on the laptop, refresh the add-on build package before copying/building it:

```bash
python3 scripts/sync_home_assistant_addon.py
```

The laptop CLI remains unchanged:

```bash
PYTHONPATH=src python3 -m ambilight_wled_bridge run
```

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

If pytest is installed:

```bash
pytest
```
