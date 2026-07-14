#!/usr/bin/env python3
"""Minimal stateful adapter used to demonstrate the conformance protocol.

It is intentionally not an MSYS launcher implementation.  A real provider's
test adapter should translate each ``call`` record to the provider's ordinary
dispatch function or to a live mIPC call and copy the returned payload/error.
"""

import json
import sys


SCHEMA = "msys.provider-conformance.v1"
CLAIM = {"id": "org.msys.role.launcher.v1", "version": "1.0.0"}
DEFAULTS = {
    "layout": "profile",
    "wallpaper_color": "#10151c",
    "accent_color": "#66b3ff",
    "icon_size": 56,
    "show_labels": True,
    "sort": "name",
}
preferences = dict(DEFAULTS)
revision = 0


def state():
    return {
        "schema": "msys.shell-preferences.v1",
        "revision": revision,
        "preferences": dict(preferences),
    }


def reply(request):
    global preferences, revision
    request_id = request.get("id")
    if request.get("schema") != SCHEMA:
        return {"schema": SCHEMA, "id": request_id, "type": "error", "code": "BAD_REQUEST", "message": "wrong adapter schema"}
    if request.get("op") == "describe":
        return {"schema": SCHEMA, "id": request_id, "type": "describe", "contracts": [CLAIM]}
    if request.get("op") != "call" or request.get("contract") != CLAIM:
        return {"schema": SCHEMA, "id": request_id, "type": "error", "code": "BAD_REQUEST", "message": "unsupported adapter operation"}
    method = request.get("method")
    payload = request.get("payload")
    if method in {"get_preferences", "status"}:
        result = state()
    elif method == "set_preferences":
        changes = payload.get("preferences", payload)
        preferences.update(changes)
        revision += 1
        result = state()
    elif method == "reset_preferences":
        preferences = dict(DEFAULTS)
        revision += 1
        result = state()
    else:
        return {"schema": SCHEMA, "id": request_id, "type": "error", "code": "NO_METHOD", "message": "unknown method"}
    return {"schema": SCHEMA, "id": request_id, "type": "return", "payload": result}


for line in sys.stdin:
    try:
        request = json.loads(line)
        response = reply(request)
    except Exception as exc:
        response = {"schema": SCHEMA, "id": 0, "type": "error", "code": "BAD_REQUEST", "message": str(exc)}
    sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
    sys.stdout.flush()
