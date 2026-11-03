"""Identity-based limits demo: call the Entra-protected gateway API as a person and show team/user limits.

  python entra_limits_demo.py --user ops            # synthetic demo-tenant user in sg-portops-team-ops
  python entra_limits_demo.py --user me --burst 20  # your own az login (admin = power tier in the demo)
  python entra_limits_demo.py --user it --show-token-claims
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time

import requests
from rich.console import Console
from rich.table import Table

import common

console = Console()
SCOPE_SUFFIX = "/.default"


def decode_claims(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def token_me() -> str:
    az = "az.cmd" if sys.platform == "win32" else "az"
    r = subprocess.run([az, "account", "get-access-token", "--resource", common.env("ENTRA_API_AUDIENCE"),
                        "--query", "accessToken", "-o", "tsv"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"az get-access-token failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def token_test_user(user: str) -> tuple[str, str]:
    """Returns (token, flow). ROPC works only for the synthetic users of the demo tenant (no MFA);
    never use it for real users. Falls back to device code if ROPC is blocked."""
    import msal
    upn = common.env(f"PORTOPS_TESTUSER_{user.upper()}_UPN")
    pwd = common.env(f"PORTOPS_TESTUSER_{user.upper()}_PASSWORD")
    scopes = [common.env("ENTRA_API_AUDIENCE") + SCOPE_SUFFIX]
    app = msal.PublicClientApplication(
        common.env("ENTRA_CLIENT_APP_ID"), authority=f"https://login.microsoftonline.com/{common.env('ENTRA_TENANT_ID')}")
    res = app.acquire_token_by_username_password(upn, pwd, scopes=scopes) if upn and pwd else {"error": "no_credentials"}
    flow = "ROPC"
    if "access_token" not in res:
        console.print(f"[yellow]ROPC not possible ({res.get('error')}: {str(res.get('error_description'))[:120]}); using device code.[/yellow]")
        dc = app.initiate_device_flow(scopes=scopes)
        console.print(dc["message"])
        res = app.acquire_token_by_device_flow(dc)
        flow = "device-code"
    if "access_token" not in res:
        sys.exit(f"Token acquisition failed: {res.get('error')} {str(res.get('error_description'))[:200]}")
    return res["access_token"], flow


def call(token: str, model: str, i: int) -> dict:
    url = (f"{common.env('APIM_GATEWAY_URL').rstrip('/')}/entra/openai/deployments/{model}"
           f"/chat/completions?api-version={common.API_VERSION}")
    r = requests.post(url, headers={"Authorization": f"Bearer {token}"}, timeout=60,
                      json={"messages": [{"role": "user", "content": f"Reply with: ok {i}"}], "max_tokens": 20})
    code = ""
    if r.status_code != 200:
        try:
            e = r.json()
            code = str(e.get("error", {}).get("code") if isinstance(e.get("error"), dict) else e.get("error") or e.get("statusCode", ""))
            msg = e.get("error", {}).get("message") if isinstance(e.get("error"), dict) else e.get("message", "")
            code = f"{code} {str(msg or '')[:50]}".strip()
        except ValueError:
            code = r.text[:60]
    h = r.headers
    return {"status": r.status_code, "team": h.get("x-portops-team", ""), "user": h.get("x-portops-user", ""),
            "team_quota": h.get("x-portops-team-remaining-quota", ""),
            "user_tokens": h.get("x-portops-user-remaining-tokens", ""),
            "user_quota": h.get("x-portops-user-remaining-quota", ""),
            "retry": h.get("retry-after", ""), "error": code}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--user", choices=("ops", "it", "cs", "me"), default="me")
    p.add_argument("--burst", type=int, default=1, help="number of requests to send")
    p.add_argument("--show-token-claims", action="store_true")
    p.add_argument("--model", default=None)
    a = p.parse_args()
    common.load_env(("APIM_GATEWAY_URL", "ENTRA_TENANT_ID", "ENTRA_API_AUDIENCE"))
    model = a.model or common.env("MODEL_SMALL", "gpt-4.1-mini")

    if a.user == "me":
        token, flow = token_me(), "az login"
    else:
        token, flow = token_test_user(a.user)
    console.print(f"Token flow: [bold]{flow}[/bold]")
    if a.show_token_claims:
        c = decode_claims(token)
        console.print({k: c.get(k) for k in ("aud", "preferred_username", "oid", "azp", "appid", "roles", "scp")})

    t = Table(title=f"Entra API as '{a.user}' ({model})")
    for col in ("#", "status", "team", "user", "team quota left", "user tokens left", "user quota left", "retry-after", "error"):
        t.add_column(col)
    for i in range(1, a.burst + 1):
        r = call(token, model, i)
        style = "green" if r["status"] == 200 else "red"
        t.add_row(str(i), f"[{style}]{r['status']}[/{style}]", r["team"], r["user"], r["team_quota"],
                  r["user_tokens"], r["user_quota"], r["retry"], r["error"])
        time.sleep(0.2)
    console.print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
