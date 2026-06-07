import json, os, websocket

TOKEN = os.environ["DERIV_TOKEN"]
APP_ID = 1089  # Deriv's public default app_id for testing
URL = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"

ws = websocket.create_connection(URL, timeout=30)

def send(payload):
    ws.send(json.dumps(payload))
    while True:
        msg = json.loads(ws.recv())
        # match by echo: return first message that is a response to our req
        if "error" in msg:
            return msg
        # return when the response type matches one of the request keys
        if any(k in msg for k in payload.keys() if k not in ("subscribe","passthrough")):
            return msg
        return msg

# 1. Authorize
auth = send({"authorize": TOKEN})
if "error" in auth:
    print("AUTH ERROR:", auth["error"]["message"]); ws.close(); raise SystemExit
a = auth["authorize"]
print("=== AUTHORIZED ===")
print("loginid:", a.get("loginid"))
print("is_virtual:", a.get("is_virtual"))
print("balance:", a.get("balance"), a.get("currency"))
print("email:", a.get("email"))
print("country:", a.get("country"))

# 2. Balance (explicit)
bal = send({"balance": 1})
print("balance call:", bal.get("balance", {}).get("balance"), bal.get("balance", {}).get("currency"))

ws.close()
print("OK")
