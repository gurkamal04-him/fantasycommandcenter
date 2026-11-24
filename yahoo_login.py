import base64, urllib.parse, requests

CLIENT_ID = input("Client ID: ").strip()
CLIENT_SECRET = input("Client Secret: ").strip()
REDIRECT = "https://localhost:8000"

url = "https://api.login.yahoo.com/oauth2/request_auth?" + urllib.parse.urlencode({
    "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
    "response_type": "code", "language": "en-us"})
print("\n1) Open this link, sign in, and click Agree:\n" + url)
print("\n2) Your browser will then show an error page. That is expected.")
print("   Copy the whole address from the address bar and paste it below.\n")
raw = input("Paste the address (or just the code): ").strip()
code = raw
if "code=" in raw:
    code = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)["code"][0]

auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
r = requests.post("https://api.login.yahoo.com/oauth2/get_token",
    headers={"Authorization": f"Basic {auth}",
             "Content-Type": "application/x-www-form-urlencoded"},
    data={"grant_type": "authorization_code", "redirect_uri": REDIRECT, "code": code},
    timeout=30)
r.raise_for_status()
tok = r.json()
print("\nYAHOO_REFRESH_TOKEN=" + tok["refresh_token"])

t = requests.get(
    "https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games;game_codes=nba/leagues",
    params={"format": "json"},
    headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=30)
print("\nTest call status:", t.status_code)

def walk(o):
    if isinstance(o, dict):
        if "league_key" in o:
            print(o["league_key"], "|", o.get("name"), "|", o.get("scoring_type"), "|", o.get("num_teams"))
        for v in o.values():
            walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)

if t.ok:
    walk(t.json())
else:
    print(t.text[:500])