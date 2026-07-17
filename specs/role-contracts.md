# MSYS Role Contracts

MSYS roles are replaceable system jobs. A role is not a specific program or UI
toolkit. Callable roles expose mIPC, while lifecycle roles such as
`display-output` may expose a supervisor-observed readiness artifact instead.
The versioned machine-readable surface and provider conformance rules are in
[`versioned-role-contracts.md`](versioned-role-contracts.md).

## Current MVP correction

The first runnable prototype intentionally uses X11 and Tk, but the architecture
must not collapse into “a launcher script plus some floating windows”.

The important rule is:

- `navigation-bar` draws navigation UI only.
- `window-manager` owns the callable window stack/navigation contract;
  `window-policy` is the early-v1 compatibility role name.
- `launcher` starts apps and presents app choices.
- `system-chrome` presents status.
- `notification-presenter` shows a transient toast and never owns history.
- `notification-center` owns bounded persistent history and the pull-down UI.
- `screen-shield` temporarily owns the whole display.
- `task-switcher` presents recent applications; it does not own window policy.
- `chooser` selects one provider for an ambiguous intent; it does not launch
  the selected application itself.
- `transition-presenter` renders non-blocking application lifecycle animation;
  it never owns or delays the application process.
- `hal-manager` discovers replaceable domain providers and exposes the stable
  hardware API; it does not import provider implementation code.

For example, the Back button must not guess process names or kill apps itself.
It calls:

```text
role:window-manager.back()
```

The active provider first offers Back to the foreground application's
`org.msys.application-navigation.v1` interface. Only `handled:false`, or an
application which does not provide that interface, allows the manager to
restore the previous task or Home. `close_active()` remains a separate,
explicit lifecycle operation. During migration the manager may also hold the
`window-policy` compatibility lease.

In the current MVP, `msysd` also keeps a foreground component stack for apps it
started itself. This is the reliable first path for mobile navigation:

- `start(component)` records foreground manual window components.
- `role:window-manager.list_windows()` exposes that stack.
- `role:window-manager.back()` first navigates inside the top application.
- Root-page Back restores the previous component or Home.
- `role:window-manager.close_active()` explicitly stops the top component.

Raw X11 window killing is only a fallback for unmanaged external windows.

## launcher MVP methods

The launcher discovers only components authorized by `msys.core.list_apps`;
presentation metadata cannot create a launch target. In addition to starting
an exact component id, a launcher may expose provider-owned appearance state:

```text
get_preferences({}) -> { schema, revision, preferences }
set_preferences({ layout?, wallpaper_color?, wallpaper_path?, accent_color?,
                  icon_size?, grid_columns?, grid_rows?, show_labels?, acrylic?,
                  navigation_mode?, navigation_visibility?, status_visibility?,
                  icon_spacing?, folders_enabled?, large_folders_enabled?,
                  animations_enabled?, reduce_motion?, sort? })
reset_preferences({}) -> { schema, revision, preferences }
```

The six original v1 presentation fields remain required in a preference state.
The later mobile fields are optional so a compact third-party launcher remains
conformant.  A provider that implements them uses `buttons|pill` for navigation
presentation and `always|auto-hide` independently for navigation and status
visibility.  `auto-hide` keeps an edge reveal target; it does not invent a
second navigation provider or change the selected role.

## screen-shield v1 methods

The selected provider temporarily owns the complete display without implying
authentication or a login boundary:

```text
show({})   -> status
hide({})   -> status
toggle({}) -> status
status({}) -> status
```

`status` uses `msys.screen-shield.status.v1` and contains `visible`, monotonic
provider-local `revision`, `touch_dismiss_enabled`, `last_reason`, and (for a
mutation) `changed`. Show and hide are idempotent. If its X11 surface is
destroyed or unmapped externally, the provider must reconcile `visible=false`
and invalidate stale presentation commands; a later show recreates the surface.
Touch dismissal is a product policy, default-on in the reference provider and
configurable through its manifest environment. Even when dismissal is disabled,
the shield consumes pointer input rather than leaking it to an obscured app.

The legacy `msys.role.screen-shield` event with `action=show|hide|toggle` is a
compatibility input only. New callers use `role:screen-shield` RPC so success,
failure, and current state are observable.

`layout=profile` follows later product/layout-profile changes. Other values are
explicit launcher overrides. Successful changes are atomic and broadcast as
`msys.shell.preferences.changed`; Settings calls the role and never edits the
provider's private file.

Role-based visual activation is a Core operation, not a launcher package
convention:

```text
msys.core.activate_role({ role: "launcher" })
  -> { ok, role, provider, generation, state, activation }
```

Core resolves the active provider, or the current preferred provider when no
lease is active, and calls `ensure_ready` on that exact component. It derives
`component`, `identity`, and `title` solely from the selected provider's
manifest, then calls `role:window-manager.activate_component`. Calling the API
again reuses and raises the running generation; it does not spawn a package by
well-known id.

`BAD_ROLE`, `UNKNOWN_ROLE`, `NO_PROVIDER`, and `ROLE_UNAVAILABLE` distinguish
request/catalog/start failures. `ROLE_ACTIVATION_FAILED` preserves the actual
provider plus the downstream activation error. Activating the
`window-manager` role itself is rejected as `ROLE_ACTIVATION_RECURSION` rather
than recursively calling that same role.

A window-manager handling `home()` may call `activate_role(launcher)`. Core's
authenticated internal `activate_component` callback is reentrant and must not
wait behind the outer Home role lock; the provider must likewise keep its mIPC
reader available while waiting for `activate_role` to return.

## window-manager MVP methods

`window-manager` is the canonical callable v1 role. A transition provider may
also advertise `window-policy` for older profiles, but new callers and contract
claims use `role:window-manager`.

```text
list_windows() -> { windows: [{ id, title }] }
close_active() -> { ok, closed?, title?, reason? }
close_active() -> { ok, closed_component? }  # explicit lifecycle path
back() -> { ok, destination: "application" | "component" | "home", ... }
home() -> delegate to msys.core.activate_role({role:"launcher"})
recents() -> list_windows()
activate_component(component, identity, title) -> identity-first raise/map of an existing surface
```

Each `msys.window.v1` may include an absolute `thumbnail` path to a bounded,
provider-owned preview cache. It is an optional presentation hint rather than
window identity: task switchers must fall back to the application icon when it
is absent, stale, unreadable, or unsupported by a replacement window manager.
The reference X11 provider uses atomically replaced P6 files in the runtime
directory so no image bytes or framework-specific object cross mIPC.

The reference `task-switcher` calls `role:window-manager.recents()` and exposes:

```text
show() -> { ok, visible, count }
hide() -> { ok, visible }
```

Back dismisses the task switcher before it navigates the foreground application.
Home raises the dynamically selected launcher rather than spawning a duplicate
process. Window policy must not match a toolkit package id, a localized title,
or the reference `MSYS Launcher` text to decide what Home means.

## chooser MVP methods

When intent resolution produces more than one handler, core calls the active
chooser provider. Candidate component ids come from the registry and the
chooser must return one of those ids unchanged:

```text
choose_intent({
  request: { action, uri?, mime?, name?, ... },
  candidates: [{ component, name, runtime, priority }]
}) -> { component, remembered, preference_key? }
```

The reference graphical chooser offers an explicit one-time choice and an
optional "remember" switch. A remembered handler is used only while it remains
in the candidate list; uninstalling or changing a handler invalidates a stale
entry and shows the chooser again. Preference scope is:

- URI handlers: action plus URI scheme;
- MIME handlers: action plus the concrete requested MIME type;
- settings handlers: action plus panel name;
- custom handlers: action plus conventional `name`, `mime`, or `scheme`
  discriminators when present.

The preference store is provider-owned state, not part of an application
manifest. It defaults to
`${MSYS_STATE_DIR}/preferences/intents.json`. A settings provider may manage it
through:

```text
forget_intent({ request }) -> { removed, preference_key }
clear_preferences() -> { removed }
list_preferences() -> { preferences }
cancel_choice() -> { cancelled, visible }  # used by Back navigation
```

Cancel and deadline expiry return typed `CHOICE_CANCELLED` or `CHOICE_TIMEOUT`
errors to the original activation caller. mIPC forwards the caller's
monotonic `deadline_ms` through nested role calls, so the chooser uses the
remaining caller budget (up to its configured UI limit) rather than receiving
a fresh fixed five-second timeout. The reference remote development client
allows 30 seconds and the chooser reserves a 1.5 second margin for returning the
selection through core.

## notification-center MVP methods

The toast presenter and history center are deliberately separate providers.
Both can observe `msys.role.notification-presenter`; applications may also post
structured history entries on `msys.notification.post`.

```text
show() -> { visible, count }
hide() -> { visible, count }
toggle() -> { visible, count }
list({ limit? }) -> { notifications, count, limit, visible }
clear() -> { removed, count, visible }
```

The reference provider atomically persists a bounded history under
`${MSYS_STATE_DIR}/notifications/history.json`. It starts with a hidden 1x1
host, creates the panel only when requested, never takes a global X/Tk grab,
and uses the same top-level ButtonRelease fallback as navigation. Tapping or
dragging downward on `system-chrome` toggles this role. Back hides it before
closing a foreground application.

Later methods:

```text
focus_window(id)
close_window(id)
minimize_window(id)
move_window(id, x, y)
resize_window(id, w, h)
set_layout(mode)
```

## navigation-bar MVP behavior

Profiles decide whether the role provider shows:

- three-button navigation: Back / Home / Apps
- gesture pill navigation
- no navigation bar

The reference implementation is Tk for fast iteration. It is replaceable by Qt,
Electron, native C/C++, or any other framework that provides the same role.
On the gesture-pill provider, a tap retains the three horizontal/vertical hot
zones and an inward swipe (up from a bottom edge or left from a right edge)
requests `close_active`; release-only touch hardware falls back to the zones.

## display-output switching

The selected display-output provider owns one live display session and
publishes `msys.display-session.v1`. Switching providers keeps the old output
alive until the new provider is ready and all running X11 consumers whose
`windowing.display` is `inherit` have migrated. Success then retires the old
provider; failure restores the old role lease, display environment, and
consumers. A textual X11 display such as `:24` is only an endpoint name: a
provider component or process generation change invalidates the visual session
and restarts inherited consumers even when that text is unchanged.
Two manifests which merely wrap the same external X server and PID/runtime
directory are not independent fallback providers and must not be listed as
such; they would compete for ownership of one hardware stack.

`msys.core.select_role` and `reset_role` return a planned transaction instead
of holding the caller's component channel while that same component may be
restarted. The returned object and every `msys.display.migration` event use:

```json
{
  "schema": "msys.display-migration.v1",
  "id": 7,
  "role": "display-output",
  "phase": "planned|switching|succeeded|rolled-back",
  "from_provider": "org.example.spi:output",
  "to_provider": "org.example.hdmi:output",
  "from_display": ":24",
  "to_display": ":0",
  "consumers": [],
  "restarted": []
}
```

Terminal failures include a bounded structured `error`; success may include
the committed role summary and restored foreground stack. Clients correlate
strictly by migration `id`, ignore delayed records for another transaction,
and treat only `succeeded` as success. A client without a supervised event
stream polls `msys.core.display_migration_status({id})`; passing no id returns
the active or most recent record, or an `idle` record when none exists.

Unexpected loss of the active display provider creates a display failure
domain. Core stops inherited visual consumers, preserves their startup order
and foreground stack, and does not charge provider-caused stops/retries to
their restart or quarantine budgets. A ready replacement generation restores
that snapshot once. If an operator selects a backup during the outage, the
same snapshot is the migration source of truth; a failed backup transaction
keeps the outage waiting for the original provider instead of starting an
empty desktop. Migration progress is observable and must not interrupt the IPC
response which requested the switch.

## transition-presenter behavior

The provider subscribes to `msys.lifecycle.transition` and correlates events
by `(component, generation)`. It may show a short launch/close mask, but must
remain correct when events are duplicated, delayed, or absent. It must not use
a global input grab. A profile can replace it or disable the role entirely;
application lifecycle continues unchanged either way.

## HAL manager behavior

The selected manager provides role `hal-manager` and interface
`org.msys.hal.manager.v1`. Hardware implementations provide the non-exclusive
`org.msys.hal.provider.v1` interface and domain capabilities. This keeps
provider selection separate from UI selection and allows several board HALs
to coexist. The complete method/data contract is in [`hal.md`](hal.md).

## Optional jobs and kiosk profiles

Omitting a role preference does not by itself disable discovery: installed
providers can still be selected by priority. A profile that intentionally has
no such system job lists it in `disabled_roles`:

```json
{
  "disabled_roles": [
    "launcher",
    "system-chrome",
    "notification-center",
    "task-switcher",
    "chooser",
    "navigation-bar"
  ]
}
```

A background/session component whose every declared role is disabled stays
dormant. Components with no role, and providers selected for another enabled
role, retain their normal lifecycle. This lets a kiosk profile truly contain
only display output, window policy, update/install agents, and its chosen
fullscreen application instead of merely hiding bars after starting them.

## Candidate selection and leases

Profiles list preferred providers in order. Remaining installed providers are
ordered by manifest priority. Only one provider holds an exclusive role lease.
Provider selection is persisted under `MSYS_STATE_DIR`, and switching follows:

```text
start candidate -> wait ready -> transfer lease -> stop old provider
```

Crashes, IPC failures, and quarantine release leases so the next candidate can
take over. A running provider is addressed by component identity and generation;
X11 titles are presentation text, not security or lifecycle identity.
