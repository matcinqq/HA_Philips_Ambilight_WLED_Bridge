# Ambilight WLED Bridge

## Setup

1. Copy this `ambilight_wled_bridge` folder into your Home Assistant `/addons` directory, or use it as a local app/add-on folder.
2. Install the app from Home Assistant Settings -> Apps/Add-ons -> Local apps/add-ons.
3. Set the Philips TV host, Philips digest credentials, and WLED host in the app options.
4. Start the app.

## Pairing the Philips TV

Pairing is only needed once, or after the TV is reset, credentials are revoked, or existing credentials stop working. The bridge does not pair automatically during normal startup.

To pair from this app:

1. Set `mode` to `pair`.
2. Set `tv_host`.
3. Leave `pair_pin`, `tv_username`, `tv_password`, and `wled_host` blank.
4. Start the app. It requests a PIN from the TV, stores the pending pairing state in `/data/pair-state.json`, then exits.
5. Put the PIN shown on the TV into `pair_pin`.
6. Start the app again before the TV pairing window expires. If the grant fails or the window expires, clear `pair_pin` and start again to request a fresh PIN.
7. Copy the printed `tv_username` and `tv_password` into the app options.
8. Set `mode` back to `bridge`.
9. Clear `pair_pin`.
10. Set `wled_host`.
11. Start the app normally.

Changing the TV IP does not necessarily require re-pairing. If the generated username/password still work, only update `tv_host`.

## Runtime

The app runs:

```bash
python3 -m ambilight_wled_bridge -c /data/bridge-config.yaml run
```

The generated config is stored at `/data/bridge-config.yaml` inside the app container. Do not put secrets in the repository; Home Assistant options provide the TV credentials at runtime.

## Defaults

- `output_backend`: `ddp_pixels`
- `ddp_port`: `4048`
- `ddp_pixel_count`: `86`
- `philips_poll_interval_ms`: `50`
- `wled_render_interval_ms`: `33`
- `smoothing_time_constant_ms`: `120`
- `color_profile`: `philips_match`
- `max_brightness`: `255`
- normal preset: `1`
- Ambilight preset: `2`

Use `max_brightness` only as a hard per-channel ceiling.

Use `philips_match` to apply the calibrated hue, saturation, and white-point
profile. Select `raw` to bypass color calibration and send the TV API values
unchanged.

Use `json_segments` as `output_backend` if DDP behaves unexpectedly and you need the previous JSON live-frame behavior.

## Laptop CLI

The Home Assistant wrapper does not replace the normal CLI. From the project checkout you can still run:

```bash
PYTHONPATH=src python3 -m ambilight_wled_bridge run
```
