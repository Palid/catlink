# Local Control spike: is Local Control possible?

Time-boxed investigation for fork issue #9. Terms in capitals follow `CONTEXT.md`
(Device, Account, Session, CatLink Cloud, Aliyun Channel, Thing Property, Local
Control).

## Result

**No-go.** On the evidence below, the Device offers no Local Control: no channel
was found over which Home Assistant could read state or issue a command directly,
without a round trip through the CatLink Cloud or the Aliyun Channel. This
confirms the prior static-analysis expectation rather than overturning it.

The result combines two kinds of evidence, kept separate throughout:

- **Proven by the app code** — how the CatLink app itself reaches a Device.
- **Proven by the probes** — what the owner's Device actually exposed on the LAN.
- **Inferred** — what the two together make likely about the firmware. The app
  not using a local channel does not by itself prove the firmware has none; that
  is why the probes were run.

## Device under test and method

- **Device:** the owner's Scooper Pro Ultra, at LAN address `192.168.3.204`
  (address supplied by the owner).
- **Static analysis:** the decompiled CatLink Android app (jadx, version 4.3.5).
- **Live probes:** exactly three active probes, each run once, only against
  `192.168.3.204` and the one CoAP multicast group, under the owner's explicit
  per-step consent. Passive traffic capture and Bluetooth enumeration were **not
  performed** — the owner did not consent to them this session.

## Investigation steps and results

The PRD lists six investigation steps. Each is recorded below with what was done
and what came back.

### 1. Find the Device's address

The owner provided the LAN address (`192.168.3.204`) directly, so the router
lease table and the MAC from `aboutDevice` were not needed and no MAC appears
here.

**Probe 1 — reachability.** Run once:

- `ping -c 3 192.168.3.204` → 3 replies, 0.0% packet loss (~2.7–8.6 ms).
- `arp 192.168.3.204` → `no entry`.
- `route get 192.168.3.204` → routed via gateway `192.168.1.1` on interface
  `en8`.

The Device is on a different subnet (`192.168.3.0/24`) from this Mac and is
reached across the router, which is why it answers `ping` yet has no ARP entry
(ARP holds only same-segment hosts). The owner's VLAN warning held in part — the
Device is not on this Mac's segment — but inter-subnet routing is permitted, so
it is reachable. Because `ping` got replies, the "unreachable" condition (no ping
reply **and** no ARP entry) was not met, and probes 2 and 3 proceeded.

### 2. Scan it: TCP ports and UDP 5683

**Probe 2 — TCP port scan.** Run once. `nmap` was not installed, so a gentle
Python `socket` connect scan of all TCP ports (1–65535) was used, at limited
concurrency with a short connect timeout. Result:

- Open: **TCP 53**, **TCP 9990**. No banner was returned on a passive read of
  either (both are request-driven).
- TCP 5683 was **not** open (CoAP runs over UDP; see step 3).
- No open port matching an HTTP, MQTT, or documented local-control service.

Neither open port corresponds to a known Local Control channel. TCP 53 is the
DNS port; TCP 9990 is non-standard. Because inter-subnet routing is involved, it
cannot be proven from a port scan alone that these ports terminate on the Device
rather than on a middlebox on the path — confirming that would need the traffic
capture, which was not consented this session. Either way, neither is a channel
the integration could use.

**UDP 5683** was exercised as part of step 3.

### 3. ALCS / CoAP discovery

**Probe 3 — CoAP discovery.** Run once. A minimal CoAP `GET /.well-known/core`
was built in Python (version 1, GET, the two Uri-Path options `.well-known` and
`core`, a random message id and token) and sent:

- **Unicast**, type CON, to `192.168.3.204:5683` → no reply within 5 s.
- **Multicast**, type NON, to the ALCS/CoAP group `224.0.1.187:5683` → no reply
  within 5 s.

Each datagram was sent exactly once. No ALCS/CoAP local service answered, on the
Device directly or on the multicast group.

### 4. Capture the Device's traffic for an hour (incl. a cleaning cycle)

**Not performed (no consent this session).** Passive traffic capture was outside
the consent boundary for this session. This is the step that would have shown
which hosts and ports the Device talks to at runtime, whether TLS is used, the
server name, and whether anything is sent in clear — and it is the step that
would settle what TCP 9990 is. It is the main open item if the question is
reopened.

### 5. Enumerate the Device's Bluetooth services

**Not performed (no consent this session).** BLE enumeration was outside the
consent boundary and would also have required re-provisioning the Device. The
app-code reading in the next section covers what Bluetooth is used for.

### 6. Repeat steps 2 and 4 on an older, non-camera Device

**Not performed.** No older ESP-based CatLink Device was available this session,
and it is outside the consent boundary (only the owner's Scooper Pro Ultra was
probed). It remains the family where a positive result is most plausible and is
noted as a follow-up in the PRD.

## Static-analysis findings

File references are `path:line` within the decompiled app tree
(`.../jadx-out/sources/`). No key, secret, token, serial, or MAC value is
reproduced here.

**The app reaches Devices only through the cloud, addressed by Aliyun `iotId`.**

- Runtime state and commands go through the Aliyun panel abstraction:
  `com/catlinks/utils/PanelDeviceUtil.java:110` and `:130`
  (`panelDevice.setProperties(...)`), `:147` (`panelDevice.invokeService(...)`).
- Every `PanelDevice` is constructed from an `iotId` (a cloud identifier), never
  a LAN address — e.g. `com/catlinks/viewmodel/HomeDeviceVm.java:2182` and, for
  the Device under test's own screen,
  `com/catlinks/app/device/smartfeeder/detail/ScooperProUltraDetailActivity.java:2190`.
- State is exchanged as Thing Properties (`{key:{value:...}}`); the key registry
  and parsers are `com/catlinks/utils/ToolUtils.java` (e.g. `:35`
  `RUNSTATUS = "RunStatus"`).

**The cloud HTTP client is HTTPS to Aliyun.**

- `com/catlinks/utils/AliIotRequestUtils.java:45` sets `Scheme.HTTPS`; `:49`
  sends via `new IoTAPIClientFactory().getClient().send(...)`. Request paths are
  Aliyun IoT API endpoints (e.g. `user/account/identity/query`,
  `vision/customer/vod/...`). No local address is ever used.

**MQTT endpoints are Aliyun cloud brokers; there is no local broker.**

- `com/aliyun/alink/linksdk/channel/core/persistent/mqtt/MqttConfigure.java:8`
  and `:10` — `iot-as-mqtt.cn-shanghai.aliyuncs.com`.
- `com/aliyun/iot/aep/sdk/framework/config/SDKConfig.java:5` and
  `com/aliyun/iot/aep/sdk/framework/region/RegionManager.java:435` —
  `*.iot-as-mqtt.unify.aliyuncs.com`. The app never sets an MQTT host itself.

**ALCS / local CoAP is present only as bundled Aliyun SDK code, never wired up by
the app.**

- Alibaba's local CoAP (ALCS) classes exist under `com/aliyun/alink/linksdk/alcs/…`
  and `com/aliyun/alink/linksdk/tmp/device/panel/linkselection/LocalChannelDevice.java`,
  as expected for the bundled SDK.
- No code under `com/catlinks/` references a local channel, `AlcsServerConnect`,
  `CoAPClient`, `LocalChannelDevice`, `NsdManager`, `MulticastLock`, mDNS, SSDP,
  or the multicast group `224.0.1.187` (grep of the app package: no matches).
- The `com.aliyun.alink.linksdk.alcs.coap.resources.LinkFormat` import that
  appears in ~60 view-models is an unused decompiler artifact, not local use; the
  only other app-side ALCS reference is an `ErrorCode` enum import at
  `com/catlinks/app/common/c.java:5`.
- Caveat: the PRD's specific claim that the app "switches ALCS off immediately
  after initialising the SDK" was **not independently re-verified**, because the
  SDK is initialised in `com/catlinks/app/common/AppApplication.java`, which this
  spike was instructed not to open. What is verified here is stronger for the
  question at hand: the app enables and uses no local channel anywhere, so no
  such channel is offered to Home Assistant regardless of the SDK's default.

**The only direct LAN contact with a Device is SoftAP provisioning.**

- `com/catlinks/app/device/add/WifiConnectNetStep3Activity.java:1345` builds a
  `WifiConfig(61691, wifi_cipher, wifi_ssid)` and `:1350` sends it by UDP to
  `InetAddress.getByName("192.168.4.1")` on port 1000, while the phone is joined
  to the Device's own access point (SSID e.g. `catlink_fountain`, `:209`). The
  fountain path is the same: `com/catlinks/app/device/add/pure/a.java:56`.
- The payload carries the home Wi-Fi SSID and password so the Device can join the
  home network and reach Aliyun; no server address is handed to the Device, so it
  cannot be pointed at a local server at setup.
- `192.168.4.1` is the only private address in the app's own code (a further
  occurrence in `com/ezviz/sdk/configwifi/touchAp/TouchApApi.java` is a bundled
  camera-provisioning SDK and is likewise setup-only). Device binding after
  provisioning is a cloud call: `com/catlinks/viewmodel/WifiConnetStep3Vm.java`
  implements `AliIotRequestUtils.AliIotRequestListener` and binds by serial /
  `dn` / token over the Aliyun HTTP client.

**Bluetooth** for litter boxes and feeders carries Wi-Fi provisioning only
(matching the SoftAP path above); this session did not re-examine the BLE service
table, as BLE enumeration was not consented.

## Go / No-Go

**Go** required the Device to answer on a local channel such that state can be
read or a command issued without a cloud round trip per request (a channel whose
keys must first be fetched from Aliyun would have counted only as a partial
result). **No-go** otherwise.

The Device answered on no local channel:

- The consented probes found no CoAP/ALCS service (silent on unicast `5683` and
  on the multicast group) and no open port offering a usable local API — only
  TCP 53 and TCP 9990, neither a Local Control channel and neither even
  confirmed to terminate on the Device.
- The app code shows the vendor's own client never talks to a Device on the home
  LAN at runtime: it addresses Devices by Aliyun `iotId` over HTTPS and Aliyun
  MQTT, and wires up none of the bundled local-CoAP machinery. The only direct
  LAN contact is one-shot SoftAP provisioning at `192.168.4.1`.

**Decision: no-go.** Inferred from the two together: a usable, cloud-independent
Local Control path for this Device is highly unlikely to exist. The residual
uncertainty — chiefly what TCP 9990 is — could only be closed by the traffic
capture, which was not consented this session; but nothing observed points toward
Local Control, and everything observed points toward a cloud-only design.

## Follow-up (given a no-go)

Local Control is not available, so the worthwhile follow-up is cloud resilience,
not a local channel:

- **Keep last-known state offline.** When the CatLink Cloud or the Aliyun Channel
  is unreachable, retain and show the last-known Thing Property values rather than
  flapping every entity to unavailable.
- **Prefer push over polling.** Receive state as it changes by subscribing over
  the Aliyun Channel, instead of polling the cloud on a timer.

Building Local Control is explicitly out of scope and would be a new PRD, written
only after a go.

## Consent and scope notes

- All three active probes were run once each, only against `192.168.3.204` and
  the multicast group `224.0.1.187:5683`, within the owner's stated consent. No
  probe was re-run; none was blocked by the sandbox.
- Passive traffic capture and BLE enumeration were not performed (no consent this
  session) and are recorded as such above.
- Open question for the owner: if they want the residual uncertainty about TCP
  9990 (and the runtime host/TLS picture) closed, that needs the consented
  hour-long traffic capture from step 4, on a Device and network they own.
