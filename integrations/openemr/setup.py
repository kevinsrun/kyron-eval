"""Optional isolated OpenEMR environment. Never prints generated credentials."""

import argparse
import json
import secrets
import ssl
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / ".local"
ENV = LOCAL / "secrets.env"


def private_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def compose(*args, capture=False):
    return subprocess.run(
        ["docker", "compose", "--env-file", str(ENV), "-f", str(ROOT / "compose.yaml"), *args],
        check=True,
        capture_output=capture,
        text=True,
    )


def initialize():
    LOCAL.mkdir(mode=0o700, exist_ok=True)
    if not ENV.exists():
        ENV.write_text(
            "\n".join(
                f"{key}={secrets.token_hex(24)}"
                for key in ("KYRON_DB_ROOT_PASSWORD", "KYRON_DB_PASSWORD", "KYRON_ADMIN_PASSWORD")
            )
            + "\n"
        )
        ENV.chmod(0o600)
    tls = LOCAL / "tls"
    tls.mkdir(mode=0o700, exist_ok=True)
    if not (tls / "server.pem").exists():
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(tls / "server.key"),
                "-out",
                str(tls / "server.pem"),
                "-days",
                "7",
                "-subj",
                "/CN=localhost/O=Kyron Synthetic Local",
                "-addext",
                "subjectAltName=DNS:localhost,IP:127.0.0.1",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
            ],
            check=True,
            capture_output=True,
        )
        (tls / "server.key").chmod(0o600)
    print("Generated or reused private local test credentials and explicit TLS trust certificate.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "up", "status", "down", "auth", "seed"])
    args = parser.parse_args()
    if args.command == "init":
        initialize()
    elif args.command == "up":
        initialize()
        compose("up", "-d", "--wait", "--wait-timeout", "360")
    elif args.command == "status":
        compose("ps")
    elif args.command == "down":
        compose("down")  # Preserve volumes. Destructive resets require an explicit user action.
    elif args.command == "auth":
        import httpx

        if (LOCAL / "client.json").exists():
            credentials = json.loads((LOCAL / "client.json").read_text())
        else:
            result = compose(
                "exec", "-T", "openemr", "php", "/kyron-provision/register_client.php", capture=True
            )
            try:
                credentials = json.loads(result.stdout)
                assert credentials["client_id"] and credentials["client_secret"]
            except (ValueError, KeyError, AssertionError) as exc:
                raise SystemExit(
                    "Client registration failed; inspect private logs without exporting secrets."
                ) from exc
            private_json(LOCAL / "client.json", credentials)
        env = dict(line.split("=", 1) for line in ENV.read_text().splitlines())
        with httpx.Client(
            verify=ssl.create_default_context(cafile=str(LOCAL / "tls/server.pem")),
            trust_env=False,
            timeout=20,
        ) as client:
            response = client.post(
                "https://localhost:9310/oauth2/default/token",
                data={
                    "grant_type": "password",
                    "client_id": credentials["client_id"],
                    "client_secret": credentials["client_secret"],
                    "scope": credentials["scope"],
                    "user_role": "users",
                    "username": "synthetic_admin",
                    "password": env["KYRON_ADMIN_PASSWORD"],
                },
            )
            if response.status_code != 200 or not response.json().get("access_token"):
                print("Token request failed:", response.status_code, response.json().get("error"))
                raise SystemExit(1)
            private_json(LOCAL / "token.json", response.json())
        print(
            "Registered test client and obtained OAuth token over verified "
            "TLS; credentials saved privately."
        )
    else:
        from seed import seed

        seed()


if __name__ == "__main__":
    main()
