# MSYS audio manager v1

Audio is a replaceable ordinary role, not part of PID 1 or HAL JSON. The
exclusive `audio-manager` role exposes `org.msys.audio.manager.v1` for bounded
device, output, volume, mute, and player control. PCM samples always stay on a
media transport such as ALSA, BlueALSA, PipeWire, shared memory, or a hardware
device; they never cross mIPC.

The reference provider carries a private BlueZ/BlueALSA stack. Its D-Bus bus is
owner-only, package-internal, and is not the host system bus or an MSYS service
bus. Other providers may use PipeWire, a DSP, HDMI ALSA, or a remote sink while
implementing the same role.

`get_state` is authoritative and returns structured unavailable reasons. A
rfkill node is not evidence of a registered controller, and a paired device is
not evidence of a connected A2DP PCM. Volume and mute writes require a real
BlueALSA mixer control and must fail visibly when it is absent.

Pair/connect/disconnect/forget accept only canonical Bluetooth addresses.
Providers must use argv-based execution or a native API and may not interpolate
addresses, output names, server addresses, or player names into a shell.

`scan` keeps a real bounded discovery session active and may attach typed
`scan` metadata to the device catalog. `discovery_started=true` plus the
measured `duration_ms` distinguishes a completed empty RF window from a failure
to start discovery; an empty device array alone is not sufficient diagnostics.

The reference provider persists BlueZ keys under `/opt/msys-state`, keeps the
private bus under the supervised runtime directory, and stores only bounded
player/output preferences in the component state directory. It does not modify
`/etc`, install a package manager database entry, or register a systemd unit.
