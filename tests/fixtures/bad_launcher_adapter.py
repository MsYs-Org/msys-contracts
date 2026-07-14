import json
import sys


SCHEMA = "msys.provider-conformance.v1"

for line in sys.stdin:
    request = json.loads(line)
    if request.get("op") == "describe":
        response = {
            "schema": SCHEMA,
            "id": request["id"],
            "type": "describe",
            "contracts": [{"id": "org.msys.role.launcher.v1", "version": "1.0.0"}],
        }
    else:
        response = {
            "schema": SCHEMA,
            "id": request["id"],
            "type": "return",
            "payload": {"schema": "wrong", "revision": -1, "preferences": {}},
        }
    print(json.dumps(response), flush=True)
