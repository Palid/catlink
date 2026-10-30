<div align="center">
  <h2>CatLink (New)</h2>
</div>

<p align="center">A maintained fork of <a href="https://github.com/hasscc/catlink">hasscc/catlink</a> with the Scooper Pro Ultra fully supported.</p>

<div align="center">
  <img src="https://play-lh.googleusercontent.com/eHPhN_fUDhdxMK4JAvlzjB5Mh-H72crLn2U3Khk37lzolNg2CTDgZXkB5bjPiM3CDqM" alt="CatLINK Logo" width="100">
  <span style="font-size: 50px; margin: 0 20px;">+</span>
  <img src="https://upload.wikimedia.org/wikipedia/en/thumb/4/49/Home_Assistant_logo_%282023%29.svg/2048px-Home_Assistant_logo_%282023%29.svg.png" alt="Home Assistant Logo" width="100">
</div>

<div align="center">
  <h3>Made easy, for 😸 lovers.</h3>
</div>

<br>

<div align="right">
  <span style="margin-right: 10px; font-size: 16px; font-style: italic">Spotted the issue?</span>
  <a href="https://github.com/Palid/catlink/issues/new?assignees=&labels=bug%2Ctriage&template=bug_report.md&title=%5BBug%5D%3A+" target="_blank" style="text-decoration: none;"><span style="background-color: #f44b42; border: none; color: white; padding: 10px 20px; text-align: center; text-decoration: none; display: inline-block; font-size: 16px; margin: 4px 2px; cursor: pointer; border-radius: 4px;">Report a Bug</span></a>
</div>

---

### Table of Contents

- [Features](#features)
- [Installation](#installation)
  - [HACS (custom repository)](#hacs-custom-repository)
  - [Manually](#manually)
- [Supported Devices and Operations](#supported-devices-and-operations)
  - [Scooper Pro Ultra](#scooper-pro-ultra)
  - [Other Devices](#other-devices)
- [How to Configure?](#how-to-configure)
- [One Session per Account](#one-session-per-account)
- [Services (Optional)](#services-optional)
- [Does this work without the cloud?](#does-this-work-without-the-cloud)
- [Changelog](#changelog)
- [How to contribute?](#how-to-contribute)
- [Disclaimer on Using Logos](#disclaimer-on-using-logos)

The CatLink (New) custom integration connects a CatLink Account to Home Assistant, so the litter boxes, feeders, fountains and Cats on that Account can be monitored, controlled and automated. It reaches Devices through the CatLink Cloud and, for the Scooper Pro Ultra, the Aliyun Channel.

#### Features:

- **Scooper/Litterbox Device Integration**: Effortlessly connect your CatLINK Scooper and Litterbox devices to Home Assistant, enabling centralized control and monitoring within your smart home environment.

- **Scooper Pro Ultra**: Live status, cleaning actions, work mode, device settings and timed-cleaning schedules. See [Scooper Pro Ultra](#scooper-pro-ultra).

- **Email or phone sign-in**: Sign in with the Email or the phone number you use in the CatLink app. The region is discovered automatically.

- **Status Monitoring**: Track essential metrics such as work status, alarm status, weight, litter weight, cleaning times, and more, refreshed from the cloud on the interval you choose.

- **Mode Selection**: Choose between different modes of operation (Auto, Manual, Time) to customize the behavior of your CatLINK devices according to your needs and preferences.

- **Advanced Actions**: Perform specific actions such as initiating a clean cycle, pausing the device, or changing the litter bag directly from Home Assistant.

- **Comprehensive Logging**: Access detailed event logs for all activities, including manual and auto-clean events, cat visits with associated cat details, and other device operations. This feature helps you keep track of your pets' habits and the device's performance.

- **Customizable Alerts and Automations**: Set up notifications and automate tasks based on the state of your CatLINK devices. For example, receive alerts when the litterbox is full or automatically start a cleaning cycle during quiet times.

- **Entity Attributes**: The integration exposes various attributes related to your CatLINK devices, such as litter weight, total and manual clean times, alarm status, and more, allowing for detailed customization and automation.

---

# Installation:

Requires Home Assistant 2026.2 or newer.

This fork installs to the same `custom_components/catlink` folder, under the same `catlink` domain, as upstream CatLink, so only one of the two can be installed at a time. If you installed upstream CatLink through HACS, remove it in HACS before downloading this one. Config entries you already have use the same domain and are picked up by this fork.

### HACS (custom repository)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=integration&owner=Palid&repository=catlink)

Or add it by hand:

1. In HACS, open the three-dot menu → **Custom repositories**.
2. Add `https://github.com/Palid/catlink` with the category **Integration**.
3. Find **CatLink (New)** in HACS, download it, and restart Home Assistant.

### Manually

#### Method 1: Manual installation via Samba / SFTP
> Download this repository and copy its `custom_components/catlink` folder to the `custom_components` folder in your Home Assistant config folder, then restart Home Assistant.

#### Method 2: One-line shell via SSH / Terminal & SSH add-on
```shell
wget -O - https://get.hacs.vip | DOMAIN=catlink REPO_PATH=Palid/catlink ARCHIVE_TAG=main bash -
```

#### Method 3: shell_command service
1. Copy this code to file `configuration.yaml`
    ```yaml
    shell_command:
      update_catlink: |-
        wget -O - https://get.hacs.vip | DOMAIN=catlink REPO_PATH=Palid/catlink ARCHIVE_TAG=main bash -
    ```
2. Restart HA core
3. Call this [`service: shell_command.update_catlink`](https://my.home-assistant.io/redirect/developer_call_service/?service=shell_command.update_catlink) in Developer Tools
4. Restart HA core again

## Supported Devices and Operations

### Scooper Pro Ultra

<div align="center">
  <img src="https://www.catlinkus.com/cdn/shop/files/ULTRA3_832ba0c1-c1b6-4ec0-ba8a-6ec5122897dd_610x610_crop_center.webp?v=1768480746" alt="Scooper Pro Ultra" width="150">
</div>

The Scooper Pro Ultra reports its live status and takes its cleaning commands only over the Aliyun Channel, Alibaba Cloud's IoT service. Its counters, schedules and some settings come from the CatLink Cloud. The config entry needs the Aliyun app key and secret for it (see [How to Configure?](#how-to-configure)).

| Platform | Entities | Reached over |
| --- | --- | --- |
| Sensor | `status` (such as idle, cleaning, emptying, levelling, paused), `litter_balance` (%), `weight`, `wifi_signal` (dBm), `firmware` | Aliyun Channel |
| Sensor | `litter_remaining_days`, `deodorant_countdown`, `total_clean_time`, `schedules` (the schedule list as an attribute), `about`, `error`, `last_log` | CatLink Cloud |
| Binary sensor | `problem` (the reason as an attribute), `bin_full`, `bin_installed` | Aliyun Channel |
| Button | `clean`, `empty`, `level`, `pause`, `resume`, `cancel` | Aliyun Channel |
| Button | `reset_litter`, `reset_deodorant` | CatLink Cloud |
| Select | `work_mode` (induction, manual, timing) | read over the Aliyun Channel, set over the CatLink Cloud |
| Select | `indicator_light` and `fill_light` (off, on, auto), `litter_type` (clay, mixed) | Aliyun Channel |
| Switch | `child_lock`, `continuous_clean`, `prompt_sounds`, `soft_stool`, `camera_sleep` | Aliyun Channel |
| Switch | `quiet_mode`, `all_timings`, `auto_update_pet_weight` | CatLink Cloud |
| Number | `clean_delay` (1–60 min), `full_bin_threshold` (level 1–4), `levelling_duration` (level 1–4) | Aliyun Channel |
| Time | `quiet_start`, `quiet_end` (the quiet-mode hours) | CatLink Cloud |
| Service | `catlink.add_timing`, `catlink.update_timing`, `catlink.remove_timing`, `catlink.toggle_timing` (see [Services](#scooper-pro-ultra-schedules)) | CatLink Cloud |

Entity IDs follow the pattern `sensor.visual_pro_ultra_<last 4 of the MAC>_status`.

Actions over the Aliyun Channel, and the work mode, check the live status first and are refused when there is none. `clean`, `empty` and `level` run only when the Device is idle, reports no problem and its bin is not full; `pause` works only while it runs, `resume` only while it is paused, and `cancel` in either case. The work mode and the settings reached over the Aliyun Channel can be changed only while the Device is idle.

### Other Devices

Other models keep upstream's behaviour:

<div style="display: flex; justify-content: space-between; flex-wrap: nowrap; gap: 16px;">

  <div style="text-align: center; width: 33.33%;">
    <h3><a href="https://www.catlinkus.com/products/catlink-smart-litter-box-scooper-se">Scooper SE</a></h3>
    <img src="https://www.catlinkus.com/cdn/shop/files/CATLINK-Lite-01_757acadb-ebb8-4469-88c6-3ca3dd820706_610x610_crop_center.jpg?v=1691003577" alt="Scooper SE" width="150">
    <h4>Operations</h4>
    <ul style="text-align: left;">
      <li>Changing Operation Mode (Auto, Manual, Time)</li>
      <li>Actions (Cleaning, Pause, Change Garbage Bag)</li>
      <li>Wastebin Full flag</li>
      <li>Litter weight measurement</li>
      <li>Litter days left</li>
      <li>Deodorant replacement countdown in days</li>
      <li>(NEW) Reset litter and deodorant buttons</li>
      <li>Occupacy flag</li>
      <li>Cleaning count</li>
      <li>Knob status</li>
      <li>Garbage Tobe status</li>
      <li>Online status</li>
      <li>Logs & Errors</li>
      <li>Entities: sensor, binary sensor, select, switch, button</li>
    </ul>
  </div>

  <div style="text-align: center; width: 33.33%;">
    <h3><a href="https://www.catlinkus.com/products/catlink-self-cleaning-cat-litter-box-pro">Scooper PRO</a></h3>
    <img src="https://www.catlinkus.com/cdn/shop/files/1500-1500_610x610_crop_center.jpg?v=1691705114" alt="Scooper PRO" width="150">
    <h4>Operations</h4>
    <ul style="text-align: left;">
      <li>Changing Operation Mode (Auto, Manual, Time, Empty)</li>
      <li>Actions (Start, Pause)</li>
      <li>Deodorant replacement countdown in days</li>
      <li>Litter days left</li>
      <li>Litter weight measurement</li>
      <li>Reset litter and deodorant buttons</li>
      <li>Occupacy flag</li>
      <li>Cleaning count</li>
      <li>Temperature (Celsius)</li>
      <li>Humidity</li>
      <li>Online status</li>
      <li>Logs & Error</li>
      <li>Entities: sensor, binary sensor, select, switch, button</li>
    </ul>
  </div>

  <div style="text-align: center; width: 33.33%;">
    <h3><a href="https://www.catlinkus.com/products/catlink-ai-feeder-for-only-pet-young">Feeder Young</a></h3>
    <img src="https://web.archive.org/web/20221230071208im_/https://cdn.shopify.com/s/files/1/0641/0056/5251/products/3_cd58df89-6457-45af-a5c7-6ceb01272c40_700x.jpg?v=1657250711" alt="Feeder Young" width="150">
    <h4>Operations</h4>
    <ul style="text-align: left;">
      <li>Feed Button</li>
      <li>Food tray weight</li>
      <li>Online status</li>
      <li>Logs & Error</li>
      <li>Entities: sensor, binary sensor, button</li>
    </ul>
  </div>

</div>

#### Additional supported devices

<div style="display: flex; justify-content: space-between; flex-wrap: nowrap; gap: 16px;">

  <div style="text-align: center; width: 33.33%;">
    <h3>Open-X/C08</h3>
    <img src="https://www.catlinkus.com/cdn/shop/files/OPENX9_610x610_crop_center.webp?v=1767769832" alt="Open-X/C08" width="150">
    <h4>Operations</h4>
    <ul style="text-align: left;">
      <li>Operation Modes (Auto, Manual, Scheduled)</li>
      <li>Actions (Clean, Pause, Cancel, Pave)</li>
      <li>Litter data & Deodorant countdown</li>
      <li>Quiet mode, Child lock, Light, Tone</li>
      <li>Pet stats & Notice switches</li>
      <li>Entities: sensor, binary sensor, select, switch, button</li>
    </ul>
  </div>

  <div style="text-align: center; width: 33.33%;">
    <h3>PurePro Fountain</h3>
    <img src="https://www.catlinkus.com/cdn/shop/files/imgi_6_1_800x800_0f2d3807-c768-4891-a375-f56c0c9af190_610x610_crop_center.webp?v=1767511590" alt="PurePro Fountain" width="150">
    <h4>Operations</h4>
    <ul style="text-align: left;">
      <li>Operation Modes (Flowing, Eco, Smart)</li>
      <li>Water Level & Filter Life</li>
      <li>Water Temperature</li>
      <li>UV Sterilization tracking (UV_active)</li>
      <li>Heating, Light & Hair cleaning status</li>
      <li>Drinking Logs & Log Book</li>
      <li>Online status</li>
      <li>Entities: sensor, binary sensor, select</li>
    </ul>
  </div>

</div>

#### Cats

<div style="display: flex; justify-content: space-around;">

  <div style="text-align: center; width: 45%;">
    <h3>Smart collars (via the Cats integration)</h3>
    <img src="https://play-lh.googleusercontent.com/eHPhN_fUDhdxMK4JAvlzjB5Mh-H72crLn2U3Khk37lzolNg2CTDgZXkB5bjPiM3CDqM" alt="CatLINK smart collar" width="150">
    <h4>Operations</h4>
    <ul style="text-align: left;">
      <li>Activity and status sensors</li>
      <li>Weight and body metrics sensors</li>
      <li>Presence and last seen tracking</li>
      <li>Entities: sensor, binary sensor</li>
    </ul>
  </div>

</div>


### How to Configure?

> ! Read [One Session per Account](#one-session-per-account) first: signing Home Assistant in on the Account your phone uses signs the phone app out.

1. Settings → Devices & services → **Add integration** → **CatLink (New)**.
2. Choose how you sign in to the CatLink app: **Email and password**, or **Phone number and password**. Enter a phone number in full with its country code (e.g. +447911123456).
3. Pick the Devices to add and the refresh interval. Supported Devices are pre-selected.

The region is discovered automatically. Before signing in, the integration reads the CatLink Cloud's own country-to-server table and signs in on the server it names, trying the other regional servers only if that sign-in fails. The server it finds is stored with the entry, so re-authentication signs in once. Cats and Devices are discovered from the Account; the entry's **Configure** button changes the Devices and the refresh interval later.

Adding the integration again for an Account that is already configured, with a different Email or phone number, offers to switch the existing entry over instead of adding a second one.

The sign-in form also has two optional fields, the Aliyun app key and secret. Leave them blank unless the Account owns a Scooper Pro Ultra; for an Account that owns one the form will not save without them, because the Aliyun Channel needs them for live status and cleaning. They are the Aliyun IoT app key and secret that the CatLink Android app uses; this integration does not ship them.

You can add or change the Aliyun credentials at any time from the integration's entry menu: Settings → Devices & services → CatLink (New), then the entry's three-dot menu → Reconfigure. A blank secret keeps the stored one, and while the entry is running the new pair is tried against Aliyun before it is saved. When an entry's Scooper Pro Ultra cannot open the Aliyun Channel, Home Assistant asks you to re-authenticate, and that form takes the credentials too.

## One Session per Account

The CatLink Cloud allows one Session per Account. Signing in anywhere starts a new Session and ends the previous one, an Eviction. When you sign Home Assistant in on the same Account you use on your phone, each poll reclaims the Session and evicts the phone app, while opening the phone app evicts Home Assistant. Neither side keeps the Session for long.

To keep both working, give Home Assistant its own Account:

1. Create a second CatLink Account (a separate Email or Phone).
2. In the CatLink phone app, share your Devices to that Account.
3. Configure this integration with the dedicated Account, or re-authenticate an existing entry to switch it over.

If Home Assistant detects repeated Evictions on one Account (three within an hour), it raises a repair issue pointing here so you can move to a dedicated Account; the issue clears after a day without an Eviction. If the Session cannot be reclaimed at all, Home Assistant asks you to re-authenticate.

## Services (Optional)

#### Request API

`catlink.request_api` calls any CatLink Cloud endpoint using the target entity's Account Session. This is the escape hatch for anything this integration does not model yet. The target entity only selects which Account's Session signs the request; it does not have to be related to the endpoint you call. `params` must be a mapping.

The response is returned as service response data, so scripts and automations can use it. It is also shown as a persistent notification by default; pass `throw: false` to suppress it.

> ! Warning: there is no denylist, so this can also reach destructive endpoints, such as unbinding a Device. Only call endpoints you trust.

Read-only call, no parameters:

```yaml
service: catlink.request_api
target:
  entity_id: sensor.scooper_xxxxxx_state # Any sensor entity on the Account
data:
  api: token/device/union/list/sorted
```

Call with parameters:

```yaml
service: catlink.request_api
target:
  entity_id: sensor.scooper_xxxxxx_state
data:
  api: token/litterbox/aboutDevice
  params:
    deviceId: "1234567890"
```

#### Scooper Pro Ultra schedules

`catlink.add_timing`, `catlink.update_timing`, `catlink.remove_timing` and `catlink.toggle_timing` manage a Scooper Pro Ultra's timed cleanings. Target any sensor entity of that Device; the `schedules` sensor lists the current schedules, with their ids, in its `schedules` attribute. Times are `HH:mm`.

```yaml
service: catlink.add_timing
target:
  entity_id: sensor.visual_pro_ultra_xxxx_schedules
data:
  time: "09:30"
```

## Does this work without the cloud?

Short answer: **no.** This integration reaches your Devices only through the
CatLink Cloud and the Aliyun Channel; there is no Local Control (state and
commands exchanged directly between Home Assistant and a Device over your home
network). A spike against a real Scooper Pro Ultra — static analysis of the
CatLink app plus consented network probes of the Device — found no local channel:
no CoAP/ALCS service answered, and the app itself never talks to a Device on the
home LAN at runtime (the only direct LAN contact is one-shot Wi-Fi provisioning).

If the internet or the vendor cloud is down, the Devices go unavailable. The
full write-up, evidence, and the follow-up worth doing instead (cloud
resilience: keep last-known state offline, receive state by push rather than
polling) are in [`docs/local-control-spike.md`](docs/local-control-spike.md).

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md) for release notes.

### How to contribute?

Please visit [CONTRIBUTE](/CONTRIBUTE.md), and be aware of [this](/CODE_OF_CONDUCT.md).

---

### Disclaimer on Using Logos

<p style="font-size: 12px; color: gray;">
  <strong>Disclaimer on Using Logos:</strong> Please note that the logos used in this documentation, including the CatLINK and Home Assistant logos, are the property of their respective owners.
  <br><br>
  <em>Trademark Acknowledgment:</em> The CatLINK and Home Assistant logos are trademarks of their respective companies. This documentation uses these logos solely for informational and illustrative purposes. No endorsement by or affiliation with the trademark holders is implied.
  <br><br>
  <em>Usage Restrictions:</em> Ensure that you have the appropriate permissions or licenses to use these logos in your own materials. Unauthorized use of logos can result in trademark infringement or other legal issues.
  <br><br>
  <em>Modifications:</em> If you modify or resize the logos for use in your projects, ensure that the integrity of the logos is maintained and that they are not used in a misleading or inappropriate manner.
  <br><br>
  By using these logos in your documentation or materials, you acknowledge and agree to comply with all applicable trademark laws and the usage guidelines set by the respective trademark holders.
</p>
