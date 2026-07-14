# System profile contract v1

An MSYS profile selects policy and providers for one deployment without turning
`msysd` into PID 1 or coupling it to a host service manager. The normative
machine-readable shape is
[`examples/profile.schema.json`](../examples/profile.schema.json).

```json
{
  "schema": "msys.profile.v1",
  "id": "mobile-spi",
  "roles": {
    "launcher": ["org.msys.shell.pyside:launcher"],
    "display-output": [
      "org.msys.openstick.ch347:x11-spi-touch-output"
    ]
  },
  "disabled_roles": ["desktop-panel"],
  "startup": [
    "org.msys.openstick.ch347:x11-spi-touch-output",
    "org.msys.shell.pyside:launcher"
  ],
  "state_dir": "/opt/msys-state",
  "isolation": {"seccomp_helper": "/opt/msys/bin/seccomp-helper"},
  "settings": {"orientation": "portrait"}
}
```

## Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `schema` | yes | Exactly `msys.profile.v1`. |
| `id` | yes | Stable lower-case profile identity and filename stem. |
| `roles` | yes | Ordered candidate component refs for each role. An empty list is valid. |
| `disabled_roles` | no | Roles intentionally suppressed, including discovered providers. |
| `startup` | yes | Unique components requested at session startup, in declared order. |
| `env` | no | String-only non-session defaults merged before component-specific `env`. The selected display-output provider supplies `DISPLAY`. |
| `state_dir` | no | Absolute Linux persistent state root. |
| `isolation.seccomp_helper` | no | Trusted helper executable for opt-in component seccomp policies. |
| `settings` | no | Opaque JSON object for profile-aware providers. |

Top-level and core-owned nested fields are closed contracts. Unknown fields are
rejected unless their names use the `x-` vendor-extension namespace. Keys under
`settings` are intentionally not core-owned and may use ordinary names.

## Invariants

- `roles`, `disabled_roles`, `startup`, and each candidate list reject duplicate
  values.
- A role cannot appear in both `roles` and `disabled_roles`; disabling always
  must be an explicit, unambiguous profile decision.
- Component references use the same local or `package:component` grammar as
  package manifests.
- A syntactically valid component ref may be absent from the current catalog.
  This is required for optional hardware providers and packages installed
  later; candidate fallback ignores absent entries.
- The requested profile name is validated before constructing a path, and the
  loaded document `id` must equal its filename stem.
- JSON object field duplication and non-finite numbers are rejected before the
  profile reaches the supervisor.

Profile settings do not grant authority and are not a security boundary.
Component permissions and isolation declarations remain package-manifest
policy. The profile only selects deployment-wide defaults and the optional
trusted seccomp helper.

`DISPLAY` is intentionally not a normal profile constant. The supervisor
derives it from the selected `display-output` component's `DISPLAY_ID` or
`DISPLAY`, allowing the same visual application manifest to follow SPI `:24`,
HDMI `:0`, or another provider. A component-level `env.DISPLAY` is still an
explicit per-component override.
