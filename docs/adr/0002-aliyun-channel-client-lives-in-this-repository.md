# The Aliyun Channel client lives in this repository, modelled on PyMammotion

Camera-generation Devices such as the Scooper Pro Ultra take commands and report live state only over the Aliyun Channel, so the integration needs a client for it. We write a small, Device-agnostic client in this repository, next to the account module, that follows the sequence PyMammotion already uses in production for the Mammotion-HA integration: region lookup, OAuth sign-in, `createSessionByAuthCode`, then session refresh. We do not depend on PyMammotion itself. Request signing uses Alibaba's published `alibabacloud-iot-api-gateway` package, as PyMammotion does, provided it is healthy and installs on Home Assistant's supported Python versions; otherwise the signing is implemented here.

The only vendor-specific parts are the starting point, an authorisation code issued by the CatLink Cloud, and the app key and secret embedded in the CatLink app.

## Considered Options

- **Depend on PyMammotion.** It works today, but it bundles Bluetooth, protobuf and Mammotion's own app key, and its release cadence follows lawn mowers, not litter boxes. A breaking change there would break this integration for reasons unrelated to CatLink.
- **Extract a separate PyPI library for the Aliyun flow.** The cleanest layering, and what Home Assistant core would require. This repository keeps all of its cloud code in-tree, and a second package to publish and version is a cost with no user-visible benefit yet. Worth revisiting if a second integration wants the same client.
- **Write the request signing by hand.** Removes a dependency, but Alibaba's gateway signature is fiddly (canonical headers, content hash) and easy to get subtly wrong. Kept as the fallback only.

## Consequences

- PyMammotion (`pymammotion/aliyun/cloud_gateway.py`) is the reference when the flow misbehaves. It has been reviewed through a summary, not line by line; read it properly before implementing.
- Mammotion-HA tells people to use a second account with the mower shared to it for cloud control. That suggests the Aliyun platform has a single-session rule of its own, separate from the CatLink Cloud's Session rule. Whether opening the Aliyun Channel from Home Assistant disturbs the phone app is an open question to settle on real hardware.
- The vendor can rotate the embedded app key and secret in any app update, which would break the Aliyun Channel until the credentials in each config entry are updated. Mammotion-HA carries the same risk.
