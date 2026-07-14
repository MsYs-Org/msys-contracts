# MSYS HAL v1

HAL is a replaceable set of ordinary supervised components. It is not part of
PID 1, not a Python plugin registry, and not tied to systemd, D-Bus, logind,
polkit, a package manager, or the udev API.

## Roles, interfaces, and addressing

The selected manager provides the exclusive `hal-manager` role and the
non-exclusive interface `org.msys.hal.manager.v1`. Domain providers expose
`org.msys.hal.provider.v1` plus capabilities such as `hal.power`,
`hal.thermal`, `hal.backlight`, `hal.display.layout`, or
`hal.input.inventory`.

The manager discovers interface providers through `msys.core.discover` and
calls them by exact `component:<package>:<component>` identity. It never imports
provider code. A provider can therefore be native C/C++, Rust, Python, or any
other runtime that implements mIPC.

Automatic selection is ordered by manifest priority and stable component id.
An unavailable automatic provider may fall through to the next candidate. An
explicit user selection is pinned and fails visibly instead of silently
switching hardware implementations. Selection state is written atomically.

## Manager API

All request and response values are bounded JSON objects. Device ids are stable
strings of the form `domain:provider-local-id`.

```text
inventory({domains?, refresh?})
get_state({id, refresh?})
set_state({id, changes})
list_providers({domain?, refresh?, probe?})
get_provider({domain, component?, refresh?, probe?})
select_provider({domain, component?, expected_revision?, allow_unavailable?})
reset_provider({domain, expected_revision?})
watch({after_revision?, timeout_ms?, domains?})
```

`inventory` returns `schema`, a monotonic `revision`, per-domain status rows,
and devices. Missing kernel classes, devices, or higher-level roles are normal
`unavailable` results, not process failures. Each device describes its domain,
active provider, mutable fields, and bounded metadata.

`set_state` accepts domain-specific fields only. A caller can never submit an
arbitrary sysfs path. For example, a backlight provider validates and re-reads
brightness, while the reference display provider calls only the selected
`window-manager` role with the typed layout contract.

Provider management is intended for Settings. A domain-filtered
`list_providers` returns detailed candidates, including their bounded
domain-prefixed `capabilities` and the last observed `health`. With
`probe:true`, the manager calls each candidate's existing idempotent
`inventory` method in parallel before replying. A domain-less call keeps the
compact v1 catalog and does not accept `probe:true`.

Health is observational data, not a lease. Its status is `available`,
`degraded`, `unavailable`, or `unknown`; a failed probe also carries a bounded
`error_code`. `get_provider` returns one exact candidate (or the effective
candidate when `component` is omitted) and may probe only that candidate.

Passing a discovered component to `select_provider` pins that domain. The
manager first verifies the provider protocol and performs an inventory
preflight. `available` and `degraded` providers are accepted. A valid,
structured `unavailable` result requires explicit operator confirmation via
`allow_unavailable:true`; transport failures and invalid provider responses
are never overridable. `reset_provider` restores automatic selection.

Settings copies the catalog `revision` to `expected_revision` for select and
reset. If the revision advanced, the manager returns `HAL_CONFLICT`, changes
neither memory nor disk, and the caller must reload before retrying. Omitting
the field preserves the original v1 last-writer-wins behavior. Selection and
its revision journal are committed atomically. `watch` is a bounded long poll
and complements the `msys.hal.changed` event topic.

## Provider API

Every `org.msys.hal.provider.v1` component implements:

```text
describe({})
inventory({domains?})
get_state({id})
set_state({id, changes})
```

`describe` returns provider identity and domains and may include at most 32
capability strings. Each capability begins with a declared domain, for example
`backlight.inventory`, `backlight.state.read`, or
`backlight.state.write`. Managers give an older provider that omits the field
only the conservative `<domain>.inventory` and `<domain>.state.read` baseline;
capabilities do not imply current availability.

Providers must keep device ids stable across restarts, reject unknown fields,
and reject every write they do not explicitly implement. High-rate pixels,
audio, video, and raw input do not travel through HAL JSON.

## Stable errors

Manager and provider failures use an mIPC `error` record with a stable code,
short message, and optional bounded payload. Callers branch on the code:

- `HAL_BAD_PAYLOAD`: invalid type, field, id, size, or empty changes;
- `HAL_UNKNOWN_METHOD`: method is outside the v1 interface;
- `HAL_UNAVAILABLE`: the requested hardware or required route is unavailable;
- `HAL_READ_ONLY`: the requested write is not implemented;
- `HAL_PROVIDER_ERROR`: provider transport/protocol failure;
- `HAL_PERSISTENCE_ERROR`: an atomic selection commit failed and was restored;
- `HAL_CONFLICT`: `expected_revision` is stale;
- `HAL_BUSY`: the bounded request queue is full;
- `HAL_INTERNAL_ERROR`: redacted unexpected implementation failure.

`HAL_BAD_PAYLOAD`, `HAL_UNAVAILABLE`, and `HAL_READ_ONLY` from a conforming
provider survive the manager boundary. Unknown downstream codes are mapped to
`HAL_PROVIDER_ERROR` with bounded diagnostic details.

## Trust boundary

Manifest `mipc.call:*` and `mipc.event:*` permissions are enforced by the
broker for supervised component channels. They do not prove that a provider is
safe: filesystem/device permissions outside mIPC remain audit metadata,
filesystem ownership and optional process isolation are independent controls,
and the HAL manager is not an authorization server. An unmatched root peer on
the public operator socket is outside component ACLs; a public peer attributable
to a supervised component is checked as that component.
