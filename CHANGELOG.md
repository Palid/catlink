# Changelog

## 2.2.0 - 2026-10-30

### Added
- Email and password sign-in alongside the phone number; a config entry is keyed on the CatLink Account, so one Account cannot be configured twice (#13)
- Scooper Pro Ultra settings over the CatLink Cloud: quiet mode and its hours, timed-cleaning schedules with the `add_timing`, `update_timing`, `remove_timing` and `toggle_timing` services, litter and deodorant resets, and cat-weight auto-update (#18)
- Scooper Pro Ultra live status and actions over the Aliyun Channel: status, litter balance, weight, Wi-Fi signal, firmware, problem and bin sensors, clean, empty, level, pause, resume and cancel buttons, the work mode select and the remaining device settings. The Aliyun app key and secret are entered in the config flow and stored in the config entry (#21)
- Reconfigure for the Aliyun credentials from the entry's three-dot menu (#25)

### Fixed
- Session Eviction handled honestly: detected in one place and reclaimed with a single retry, a repair issue after repeated Evictions, and re-authentication when the Session cannot be reclaimed (#17)
- Region discovered from the CatLink Cloud's country table before signing in instead of guessed by signing in on each regional server in turn; re-authentication signs in once on the stored region (#19)
- Entity IDs created in their platform's domain (`sensor.`, `switch.` and so on) instead of `catlink.`, ahead of Home Assistant 2027.5 rejecting the old form. Entities already in the entity registry are expected to keep their IDs; entity IDs of existing installs may still change, so check automations after upgrading (#12)
- Missing cloud values reported as unknown instead of 0, without an error logged on every poll (#14)
- Passwords longer than 16 characters are encrypted before sign-in (#13)
- `catlink.request_api` registered on config-entry setup, returning the response as service response data (#15)
- Tests: the platform allow-list covers `number` and `time` (#20), and the service tests give the mocked Account a string id (#24)

### Changed
- Renamed to CatLink (New) in HACS and in Settings → Devices & services; the documentation and issue tracker links and the code owner point at the fork, `Palid/catlink`. The domain stays `catlink`
- HACS requires Home Assistant 2026.2.0 or newer
- Scooper Pro Ultra pre-selected in the config flow as a supported Device (#18)
- Domain glossary and architecture decision records (#11), and the Local Control spike write-up, `docs/local-control-spike.md`, whose verdict is no-go (#16)

## 2.1.1 - 2026-02-06

### Added
- Open-X/C08 device support (thanks to this nice repo: https://github.com/eulemitkeule/pycatlink)
- Limited Scooper Pro Ultra support
- Reset litter and reset deodorant buttons for litterbox
- Config flow coverage for discovery, reauthentication, and options
- Test suite additions and GitHub Actions workflow for tests

### Fixed
- Device detail parsing fallback when API payloads are incomplete
- Home Assistant 2026.2.0 compatibility issues

### Changed
- Device and entity organization with new helpers and logs mixin
