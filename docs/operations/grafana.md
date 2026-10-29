---
title: InfluxDB and Grafana
parent: Operations
nav_order: 1
---

# InfluxDB and Grafana


## InfluxDB
During my research into which dataset type would be suitable for us, one situation I came across was that InfluxDB has several different versions. I had initially thought that the latest version would provide better system longevity and ease of maintenance; however, due to the limitations and licensing issues that came with version 3, I decided to use InfluxDB v2, which we also use in our other experiments (MonXe, XeBRA). Accordingly, I carried out the installation using the procedure from the relevant [website](https://docs.influxdata.com/influxdb/v2/install/?t=Raspberry+Pi#download-and-install-influxdb-v2)

During the InfluxDB installation, we need to determine an organization name and a bucket. As the organization, I chose ''telescope_org'', so as to avoid repeating "muon" all the time. Then, for the buckets in which the data will be saved, I decided on the names ''telescope_env'', which I created for the data from the system's environmental sensors, and ''telescope_rate'', which I created for the signal rate.
For the installation, from the terminal screen:
```bash
influx setup \
    --username influxdbadmin \
    --password <super_secure_password> \
    --org telescope_org \
    --bucket telescope_env \
    --retention 0 \
    --force
```

As a result,
  * the username: `influxdbadmin'`
  * password: `<super_secure_password>`

Since the InfluxDB installation only allows a single bucket to be created, I then ran the following to create the ''telescope_rate'' bucket, where the corresponding data will be stored:
```bash
influx bucket create --name telescope_rate --org telescope_org --retention 0
```
and to verify it:
```bash
influx bucket list
```

In order for our relevant functions to be able to communicate with InfluxDB and write data to the database, we need an access token. This token is given during the InfluxDB installation, but in case it is forgotten, a new token can be obtained with:

```bash
  influx auth create \
    --org telescope_org \
    --all-access \
    --description "muon_telescope_full_access"
```

For this, I made the preparations before installation by taking the entries found in the file I created as `.env.template` and modifying the relevant parts in a file named `.env`:
```bash
  INFLUX_URL=http://localhost:8086
  INFLUX_ORG=telescope_org
  INFLUX_TOKEN=<very_secret_token>
  
  # Bucket seperation: env = low frequency/state data, rate = coincidence rate
  INFLUX_BUCKET_ENV=telescope_env
  INFLUX_BUCKET_RATE=telescope_rate
```

## Grafana
In the first stage of our project, we were using a [Flask](https://palletsprojects.com/projects/flask)-based web interface for monitoring the system through the web. However, after switching the database to InfluxDB, we decided to use Grafana due to its familiarity and the ready-made tools it provides. For this, we first installed Grafana on the Raspberry Pi following the tutorial found [here](https://grafana.com/tutorials/install-grafana-on-raspberry-pi/).

Grafana automatically configures its service to use port 3000. After setting up services such as automatic startup on boot and starting the Grafana service `grafana-server`, we can access the system via `http://<ip address>:3000`.

The first login to the site is done with the default username `admin` and password `admin`; afterwards, a new password must be set.

Afterwards, integration can be done through the web interface, or alternatively via the `RPi` terminal with

```bash
influx bucket list --org telescope_org
```

we can see the buckets we created during the InfluxDB installation. From the results here, a separate token is created using the __IDs__ in the `telescope_env` and `telescope_rate` rows:

```bash
influx auth create \
  --org telescope_org \
  --description "grafana read-only" \
  --read-bucket <telescope_env_ID> \
  --read-bucket <telescope_rate_ID>
```

In the next step, in the browser: Grafana → left menu Connections → Data sources → Add data source → InfluxDB. Settings:

1. Query language: Flux — this is the most critical line; InfluxQL comes as the default and none of our queries will work with it. Changing this must be the first thing to do.
2. HTTP → URL: http://localhost:8086 (Grafana and InfluxDB are on the same Pi, so localhost is correct)
3. All toggles in the Auth section (Basic auth, etc.) should remain off; this way we make sure the system is strictly read-only.
4. InfluxDB Details:
    Organization: telescope_org
    Token: the read-only token created just now
    Default Bucket: telescope_env

Then, with `Save & Test`, we have the opportunity to test the system.

### Grafana Dashboard: Panels and Data Bindings

Next, in the `Dashboard` section, we need to create the relevant panels and data bindings to visualize our InfluxDB data in Grafana. We will start with temperature and connect the remaining sensors in a similar way.

__Panel 1: Temperature (step by step)__

1. Left menu → Dashboards → New → New dashboard
2. '+' Add visualization → select InfluxDB as the datasource
3. Paste the following into the query box at the bottom (in Flux mode this is a plain text editor):

```bash
from(bucket: "telescope_env")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "environment" and r._field == "temp_c")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> yield(name: "temp_c")
```
4. The graph can be viewed with Refresh (run query) at the top right. Setting the time picker in the top-right corner to "Last 3 hours" should show the same data visible in Data Explorer (the 26.5–27.2 °C band).
5. Settings in the right-hand panel:
 - At the top, Title: Temperature
 - Standard options → Unit: type "celsius" in the search box → Celsius (°C)
 - Optional: Graph styles → Line width: 1, Fill opacity: 10 (a light fill improves readability)

__Panel 2: Humidity__

Duplicate → in the query, replace temp_c with humidity_rh, Title: Humidity, Unit: Percent (0-100). Also set Standard options → Min: 0, Max: 100 — keeping the humidity axis fixed makes gaps or sensor failures immediately visible.

__Panel 3: Orientation (angle + IMU tilt together)__

Duplicate → query:

```bash
from(bucket: "telescope_env")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "environment")
  |> filter(fn: (r) => r._field == "angle_deg" or r._field == "imu_tilt_deg")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
```

Grafana automatically turns the two fields into two series. Unit: Degrees (°). This panel is the visual counterpart of our creep/drift monitoring: if the two lines start to diverge, there is mechanical slippage. In the legend, the series appear as angle_deg/imu_tilt_deg; for more readable names, use Overrides → Fields with name → Display name to set "Target angle" / "IMU tilt".

__Panel 4: Gyro RMS__

Duplicate → field imu_rms_dps, Title: Gyro RMS. There is no °/s in the unit list; it can be entered as a custom unit under Misc → custom unit. Additionally, by adding Thresholds, a yellow/red line can be placed at the settling threshold (the value used in the daemon for the stability decision, e.g. around 0.5 °/s). It appears as a threshold line on the graph, and vibration events can be distinguished at a glance. To make the threshold visible: Graph styles → Show thresholds: As lines.

__Panel 5: IMU Stable (state timeline)__

Add a new panel (not a duplicate, since the visualization type is different). From the visualization selector at the top right, choose State timeline. Query:

```bash
from(bucket: "telescope_env")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "environment" and r._field == "imu_stable")
  |> map(fn: (r) => ({ r with _value: if r._value then 1 else 0 }))
  |> aggregateWindow(every: v.windowPeriod, fn: min, createEmpty: false)
```
The choice of fn: min is deliberate: if a window contains even a single unstable sample, that window should appear as "unstable". Then add Value mappings: 1 → Text: "Stable", color green; 0 → "Vibration", color orange. The result is a strip of green/orange blocks along the time axis — motor movements and external vibrations show up as clearly delimited blocks.

__Panel 6: Data age (is the daemon alive?)__

New panel → visualization type Stat. Query:

```bash
from(bucket: "telescope_env")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "environment" and r._field == "temp_c")
  |> last()
  |> map(fn: (r) => ({ _time: r._time, _value: float(v: uint(v: now()) - uint(v: r._time)) / 1000000000.0 }))
```
The value is "age of the last point, in seconds". Unit: seconds (s). Thresholds: green at 0, yellow at 360, red at 900. The logic behind the thresholds: with a 10 s cadence and a 30 s flush (`[influx]`), this value stays under about a minute in a healthy system; if it exceeds 6 minutes a flush is being missed, and above 15 minutes the daemon or InfluxDB is down. In other words, this single number serves as a zero-code system health monitor.

__Rate panel: together with the run variable__

First the variable: gear icon at the top right (Dashboard settings) → Variables → New variable:

Type: Query, Name: run
Data source: InfluxDB
Query:

```bash
import "influxdata/influxdb/schema"

schema.tagValues(bucket: "telescope_rate", tag: "run_name")
```

Sort: Alphabetical (Z–A) — since the run names carry ISO timestamps, the newest run appears at the top of the list.
Apply/Save. A run dropdown will appear at the top of the dashboard.

Then the panel (Time series):

```bash
from(bucket: "telescope_rate")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "coincidence_rate")
  |> filter(fn: (r) => r.run_name == "${run}")
  |> filter(fn: (r) => r._field == "rate_hz")
```

Two important settings:

- No aggregateWindow — the daemon already bins the rate (`[influx] rate_bin_s`, 60 s by default); Grafana should not resample it.
- Graph styles → Show points: Always, Line interpolation: Step after — for binned averages, step + point display is more honest than the illusion of a continuous curve.

Select Hertz (Hz) as the unit. The error band (±1σ) can be added later with an upper/lower query pair once the panel is working; it is a cosmetic layer on top of the basic panel.

One point to note: if no run is currently active, the panel appears empty — this is not an error; publish_rate_forever only writes during a run. Selecting a past run from the dropdown and setting the time range to that run's dates (Absolute time range at the top right) shows the historical rate data.

__Final touches__

- Arrange the panels by dragging: Data age (small) + Rate (wide) in the top row, Temperature/Humidity side by side below, and Orientation/Gyro/IMU Stable at the bottom.
- Auto-refresh at the top right can be set to 1m.
- Dashboard settings → JSON Model → copy the contents → commit to the repository as grafana/telescope_dashboard.json. If the SD card dies, the dashboard can be restored with a single import, and the dashboard's version history is kept in git.

## Anonymous dashboard access (Viewer role)

Currently, accessing the dashboard requires a user login. To make the dashboard accessible in anonymous mode, Grafana is configured to automatically assign the read-only Viewer role to anyone who is not logged in. Dashboards then open without a login, but the Viewer role cannot change or delete anything — the admin panel, datasource settings, and dashboard editing all still require a login. This matches exactly what we want: only the dashboards are publicly accessible, while admin functions remain closed.

On the Pi:

```bash
sudo nano /etc/grafana/grafana.ini
```

Find the `[auth.anonymous]` section (it is present in the file in commented-out form) and set it as follows:

```ini
[auth.anonymous]
enabled = true
org_name = Main Org.
org_role = Viewer
```

Two points to note: do not forget to remove the `;` characters at the beginning of the lines (the comment marker in ini format), and the org_name value must match the organization name in Grafana exactly — in a default installation this is "Main Org." (including the dot) which we left as this. If it has been changed, check it in the admin panel under Administration → Organizations and use the same name.

Then:

```bash
sudo systemctl restart grafana-server
```

Now, when http://<pi-ip>:3000 is opened from any device on the network, the Grafana interface appears directly (in viewer mode) instead of the login screen. For admin tasks, logging in via "Sign in" at the bottom right still works as before.

__Landing directly on the dashboard: home dashboard__

To have anonymous users land directly on our dashboard instead of the generic welcome screen: log in as admin → Administration → Default preferences (or org preferences) → set "Muon Telescope Dashboard" as the Home Dashboard. This way, anyone opening http://<pi-ip>:3000 is taken straight to the panels.
<>