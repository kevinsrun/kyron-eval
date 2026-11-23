"""Optional genuine browser WebRTC demo. Preflight never prints credential values."""

import argparse
import hmac
import json
import logging
import os
import secrets
from datetime import timedelta
from pathlib import Path
from threading import RLock, Thread
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from kyron_eval.ehr.config import openemr_adapter, openemr_directory
from kyron_eval.voice.live_lookup import LiveLookupSession, build_lookup_agent
from kyron_eval.web import create_app

SECRET_FILE = Path(".env.guava.local")


def load_private_key_file(path=SECRET_FILE):
    if not path.exists() or os.environ.get("GUAVA_API_KEY"):
        return
    if path.stat().st_mode & 0o077:
        raise ValueError("The ignored Guava environment file must have mode 600")
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "GUAVA_API_KEY" and value.strip():
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def auth_probe():
    """Only safe status fields leave this function; SDK logs are suppressed."""
    logging.getLogger("guava").setLevel(logging.CRITICAL)
    from guava.auth import CLIAuth
    from guava.utils import get_base_url

    status = {
        "api_key_present": bool(os.environ.get("GUAVA_API_KEY")),
        "agent_number_present": bool(os.environ.get("GUAVA_AGENT_NUMBER")),
        "cli_auth_present": CLIAuth.exists(),
        "authenticated": False,
        "webrtc_does_not_require_phone_number": True,
        "actual_audio_observed": False,
    }
    endpoint = urlsplit(get_base_url())
    if (
        endpoint.scheme != "https"
        or endpoint.hostname != "app.goguava.ai"
        or endpoint.port not in {None, 443}
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        return {**status, "blocker": "Refusing an unexpected Guava endpoint"}, None
    try:
        import guava

        client = guava.Client()
        numbers = client.list_numbers()
        status.update(authenticated=True, phone_number_count=len(numbers))
        return status, client
    except Exception as exc:
        status["error_type"] = type(exc).__name__
        response = getattr(exc, "response", None)
        if response is not None:
            status["http_status"] = response.status_code
            try:
                code = response.json().get("error")
                if code in {"invalid_grant", "invalid_request", "invalid_client", "access_denied"}:
                    status["oauth_error"] = code
            except (ValueError, AttributeError):
                pass
        status["blocker"] = (
            "Guava authentication/API access not verified; refresh login or test key"
        )
        return status, None


class DemoController:
    def __init__(self, client, code, target, artifact_root="artifacts", provider_factory=None):
        self.client, self.code, self.target = client, code, str(target)
        self.root = artifact_root
        self.nonce = secrets.token_urlsafe(32)
        self.lock = RLock()
        self.session = None
        self.issued = False
        self.listener_ready = lambda: False
        self.listener_started = False
        self.listener_error = None
        self.provider_factory = provider_factory or openemr_adapter
        self.mode = "receptionist"
        self.demo_id = None

    def start_session(self, call_id):
        with self.lock:
            if self.session is not None:
                return None
            if self.mode == "receptionist":
                from kyron_eval.voice.receptionist import ReceptionistSession

                self.session = ReceptionistSession(self.provider_factory(), self.root)
            else:
                self.session = LiveLookupSession(
                    self.provider_factory(),
                    self.target,
                    self.root,
                    mode=self.mode,
                    demo_id=self.demo_id,
                )
            self.session.world.trace.ehr["guava_registration"] = {
                "authenticated_registration": bool(self.client and self.code),
                "sdk_version": "0.53.0",
                "routing_code": "redacted",
                "client_token": "redacted",
                "transport": "WebRTC",
            }
            self.session.start(call_id)
            return self.session

    def public_status(self):
        with self.lock:
            return {
                "mode": "READ_ONLY_WEBRTC_CANDIDATE",
                "guava_registration_verified": bool(self.client and self.code),
                "writes_enabled": False,
                "listener_started": self.listener_started,
                "listener_ready": self.listener_ready(),
                "listener_error": self.listener_error,
                "call_started": bool(self.session),
                "audio_observed": bool(self.session and self.session.audio_observed()),
                "browser": self.session.browser if self.session else None,
                "latest_run": self.session.latest_run if self.session else None,
                "independent_fix_approval": "pending; cancellation disabled",
                "encounter": self.session.snapshot() if self.session else None,
                "configured_mode": self.mode,
                "demo_id": self.demo_id,
            }


def create_demo_app(controller):
    app = create_app(controller.root)
    app.state.templates.env.globals["voice_demo_enabled"] = True

    def authorize(request):
        if not hmac.compare_digest(request.headers.get("X-Kyron-Demo", ""), controller.nonce):
            raise HTTPException(403, "Local demo capability required")

    @app.get("/voice-demo", response_class=HTMLResponse)
    def page():
        template = Path(__file__).parents[1] / "templates" / "voice_demo.html"
        return template.read_text().replace("__DEMO_NONCE__", controller.nonce)

    @app.get("/api/live-demo/status")
    def status():
        return controller.public_status()

    @app.post("/api/live-demo/start")
    def start(request: Request):
        authorize(request)
        with controller.lock:
            if not controller.listener_ready():
                raise HTTPException(503, "The Guava listener is not registered yet")
            if controller.issued:
                raise HTTPException(409, "Single-use demo; restart the server for a fresh session")
            controller.issued = True
        try:
            token = controller.client.create_webrtc_client_token(
                controller.code, expires_in=timedelta(minutes=5)
            )
        except Exception as exc:
            raise HTTPException(
                503, "Guava client-token request failed: " + type(exc).__name__
            ) from None
        return {
            "webrtcCode": controller.code,
            "clientToken": token,
            "customPayload": {"demo_nonce": controller.nonce},
        }

    @app.post("/api/live-demo/configure")
    async def configure(request: Request):
        authorize(request)
        raw = await request.body()
        if len(raw) > 1024:
            raise HTTPException(413, "Small configuration only")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "JSON configuration required") from None
        if not isinstance(payload, dict) or payload.get("mode") not in {
            "lookup",
            "triage",
            "receptionist",
        }:
            raise HTTPException(422, "Choose receptionist, lookup or triage")
        if payload.get("demo_id") not in {None, "nonurgent", "urgent", "insufficient"}:
            raise HTTPException(422, "Unknown fictional scenario")
        with controller.lock:
            if controller.issued or controller.session:
                raise HTTPException(409, "Configuration locked for this session")
            controller.mode = payload["mode"]
            controller.demo_id = payload.get("demo_id")
        return controller.public_status()

    @app.get("/appointments", response_class=HTMLResponse)
    def appointments_page():
        template = Path(__file__).parents[1] / "templates" / "appointments.html"
        return template.read_text().replace("__DEMO_NONCE__", controller.nonce)

    @app.post("/api/receptionist/select")
    async def select_appointment(request: Request):
        authorize(request)
        raw = await request.body()
        if len(raw) > 512:
            raise HTTPException(413, "Small appointment selection only")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "JSON selection required") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("appointment_id"), str):
            raise HTTPException(422, "Appointment identifier required")
        if not controller.session or controller.mode != "receptionist":
            raise HTTPException(409, "No verified receptionist session")
        return controller.session.select(payload["appointment_id"])

    @app.post("/api/live-demo/evidence")
    async def evidence(request: Request):
        authorize(request)
        raw = await request.body()
        if len(raw) > 2048:
            raise HTTPException(413, "Small audio metadata only; no arbitrary uploads")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "JSON audio metadata required") from None
        if not isinstance(payload, dict):
            raise HTTPException(400, "JSON object required")
        if controller.session:
            controller.session.browser_evidence(payload)
        return controller.public_status()

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--preview", action="store_true", help="UI only; never registers a call")
    parser.add_argument("--port", type=int, default=8003)
    args = parser.parse_args()
    os.environ["KYRON_OPENEMR_ALLOW_WRITES"] = "0"
    try:
        load_private_key_file()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    status, client = auth_probe()
    provider = openemr_adapter()
    provider.allow_writes = False
    try:
        patient = provider.get_patient("synthetic-patient-1")
        appointments = provider.get_appointments("synthetic-patient-1")
        manifest = json.loads((openemr_directory() / "manifest.json").read_text())
        target = str(manifest["seeded_appointment_id"])
        receptionist_fixture = manifest.get("receptionist_fixture")
        receptionist_match = (
            provider.match_name_and_dob(
                receptionist_fixture["patient_name"], receptionist_fixture["birth_date"]
            )
            if receptionist_fixture
            else None
        )
        target_present = appointments.ok and any(
            a["appointment_id"] == target for a in appointments.data.get("bookings", [])
        )
        status.update(
            openemr_patient_read=patient.status,
            openemr_appointment_read=appointments.status,
            demo_target_present=target_present,
            writes_enabled=False,
            receptionist_fixture_configured=bool(receptionist_fixture),
            receptionist_name_search=receptionist_match.status
            if receptionist_match
            else "NOT_CONFIGURED",
        )
    finally:
        provider.close()
    print(json.dumps(status, indent=2))
    if args.preflight:
        return
    if args.preview:
        controller = DemoController(
            None, "", target, os.environ.get("KYRON_ARTIFACT_DIR", "artifacts")
        )
        controller.listener_error = "PREVIEW_ONLY: no Guava listener or audio session"
        import uvicorn

        print(f"Blocked UI preview only: http://127.0.0.1:{args.port}/voice-demo")
        uvicorn.run(create_demo_app(controller), host="127.0.0.1", port=args.port)
        return
    if not client or not receptionist_match or not receptionist_match.ok:
        raise SystemExit("Blocked. Resolve preflight; no voice call was created.")
    try:
        code = client.create_webrtc_agent(ttl=timedelta(minutes=30), require_token=True)
    except Exception as exc:
        response = getattr(exc, "response", None)
        status_code = response.status_code if response is not None else "unavailable"
        raise SystemExit(
            f"Guava WebRTC registration failed: {type(exc).__name__}, HTTP {status_code}"
        ) from None
    controller = DemoController(
        client, code, target, os.environ.get("KYRON_ARTIFACT_DIR", "artifacts")
    )
    agent = build_lookup_agent(controller)
    import guava

    runner = guava.Runner().listen_webrtc(agent, code)
    # Read-only readiness observation of this pinned SDK's own health context.
    controller.listener_ready = runner._health_ctx.is_ready

    def listen():
        controller.listener_started = True
        try:
            runner.run(handle_signals=False)
        except Exception as exc:
            controller.listener_error = type(exc).__name__

    Thread(target=listen, daemon=True).start()
    print(f"Read-only browser demo: http://127.0.0.1:{args.port}/voice-demo")
    print(
        "Guava registration requested. A connected audio observation is required for live evidence."
    )
    import uvicorn

    uvicorn.run(create_demo_app(controller), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
