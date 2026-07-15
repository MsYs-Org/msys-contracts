# MSYS Contracts

MSYS contracts define the stable surface shared by the supervisor, SDKs,
system role providers, applications, installers, and update delivery tools.

This repository intentionally contains no systemd, D-Bus, logind, polkit,
UPower, or udev dependency. A host may launch MSYS however it wants, but MSYS
components communicate through mIPC only.

Visual clients are output-neutral: `windowing.display: "inherit"` follows the
profile-selected display session, while only X11 display providers declare a
concrete server number. The same Qt/Electron/Tk/native package therefore runs
on SPI and HDMI without a second manifest.

Normative JSON contracts:

- [`msys.manifest.v1`](examples/package-manifest.schema.json) defines packages
  and components.
- [`msys.profile.v1`](examples/profile.schema.json) defines deployment roles,
  startup order, environment, state location, and profile policy. Its semantic
  invariants are documented in [`specs/profile.md`](specs/profile.md).
- [`msys.role-contract.v1`](schemas/role-contract.v1.schema.json) defines
  versioned, language-neutral role methods, payloads, events, lifecycle
  artifacts, and provider conformance cases. The initial catalog covers
  launcher, window manager, navigation bar, display output, HAL manager, and
  audio manager;
  see [`specs/versioned-role-contracts.md`](specs/versioned-role-contracts.md).
- [`MSYS HAL v1`](specs/hal.md) defines replaceable hardware discovery,
  state, provider selection, and watch semantics over mIPC.
- [`MSYS audio manager v1`](specs/audio.md) keeps PCM outside mIPC while
  standardizing replaceable output, volume, mute, Bluetooth, and player control.
- [`msys.i18n.catalog.v1`](schemas/i18n-catalog.v1.schema.json) defines static,
  language-neutral UI catalogs (including reusable zh bases and en-US), deterministic locale fallback, and
  safe named placeholders; see [`specs/i18n.md`](specs/i18n.md).
- [`font-rendering`](specs/font-rendering.md) fixes the lightweight outline-font
  stack, display-driver boundary, and target release gate without adding a
  resident font service.

The role contract tools have no third-party dependency:

```sh
python3 -m tools.contract_tool validate
python3 -m tools.contract_tool manifest examples/providers/role-provider-manifest.json
python3 -m tools.i18n_tool validate
python3 -m unittest discover -s tests -v
```

## Repositories

- `msys-core`: `msysd`, process supervision, role routing, activation.
- `msys-sdk`: client SDKs for Python and native applications.
- `msys-shell-pyside`: reference shell, launcher, and role providers.
- `msys-settings`: a normal installable settings application and intent handler.
- `msys-hal`: replaceable manager and Linux reference providers.
- `msys-x11-session`: X11 session providers and x11display integration.
- `msys-install`: package install, local registry, and remote update delivery.
- `x11display`: existing SPI LCD X11 output implementation.

## First runnable target

The first milestone is a development runtime:

```sh
msysd --foreground --config ./examples/config --runtime-dir ./run --profile mobile-spi
```

It loads local manifests, starts eager/background components, activates
on-demand roles, routes calls/events over mIPC, and supports safe provider
fallbacks. Packaging and update delivery install into the same manifest format.
