"""Durable, restricted SMTP notifications. Acceptance is never inbox delivery."""

import json
import smtplib
import sqlite3
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from email.policy import SMTP
from hashlib import sha256
from pathlib import Path
from uuid import uuid4


class ConfirmationEmail:
    def __init__(
        self,
        ledger,
        *,
        recipient,
        host="127.0.0.1",
        port=9326,
        sender="appointments@willow.example.test",
        username=None,
        password=None,
        outbound=False,
        authorized_recipients=(),
    ):
        self.ledger = Path(ledger)
        self.host, self.port = host, int(port)
        self.recipient, self.sender = recipient, sender
        self.username, self.password = username, password
        self.outbound = outbound
        if any(c in recipient + sender for c in "\r\n") or "@" not in recipient:
            raise ValueError("Invalid notification address")
        if recipient not in authorized_recipients:
            raise ValueError("Recipient not explicitly authorized")
        if not outbound and (host not in {"localhost", "127.0.0.1"} or self.port != 9326):
            raise ValueError("Local capture must use the isolated loopback SMTP port")
        if outbound and (not username or not password):
            raise ValueError("Outbound SMTP requires explicit credentials and TLS")
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS notifications (key TEXT PRIMARY KEY, "
                "result TEXT NOT NULL)"
            )
        self.ledger.chmod(0o600)

    def db(self):
        return sqlite3.connect(self.ledger, timeout=15)

    def preview(self, receipt):
        if receipt.get("status") != "EHR_CONFIRMED":
            raise ValueError("Verified EHR receipt required")
        a = receipt["appointment"]
        return {
            "to": self.recipient,
            "from": self.sender,
            "subject": "Willow Creek Clinic test appointment " + a["appointment_id"],
            "body": (
                "Willow Creek Clinic\n\nAppointment confirmed\n"
                f"Type: {a['appointment_type']}\nDate/time: {a['local_time']}\n"
                f"Clinician: {a['clinician']}\nLocation: {a['location']}\n"
                f"Reference: {a['appointment_id']}\n\n"
                + (
                    "For changes, contact the clinic scheduling team through your "
                    "usual clinic channel.\n"
                )
                + "Fictional clinic / authorized synthetic test only.\n"
            ),
        }

    def send(self, receipt):
        message = self.preview(receipt)
        key = sha256(
            (
                "appointment-confirmation|"
                + receipt["patient_id"]
                + "|"
                + receipt["appointment"]["appointment_id"]
                + "|"
                + self.recipient
            ).encode()
        ).hexdigest()
        attempt = {
            "attempt_id": uuid4().hex,
            "status": "QUEUED",
            "message": message,
            "provider_message_id": None,
            "transport": "SMTP_OUTBOUND" if self.outbound else "LOCAL_MAILPIT_CAPTURE",
            "delivery_status": "UNKNOWN",
            "created_at": datetime.now(UTC).isoformat(),
        }
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT result FROM notifications WHERE key=?", (key,)).fetchone()
            if prior:
                result = json.loads(prior[0])
                if result["status"] == "QUEUED":
                    result.update(status="UNKNOWN", error="PRIOR_ATTEMPT_UNRESOLVED_NO_RETRY")
                return result
            db.execute("INSERT INTO notifications VALUES (?,?)", (key, json.dumps(attempt)))
        mail = EmailMessage()
        mail["To"], mail["From"], mail["Subject"] = (
            message["to"],
            message["from"],
            message["subject"],
        )
        mail["Message-ID"] = "<" + attempt["attempt_id"] + "@kyron.example.test>"
        mail.set_content(message["body"])
        data_started = False
        try:
            with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
                smtp.ehlo()
                if self.outbound:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                    smtp.login(self.username, self.password)
                code, _ = smtp.mail(self.sender)
                if code != 250:
                    raise smtplib.SMTPSenderRefused(code, b"Sender rejected", self.sender)
                code, _ = smtp.rcpt(self.recipient)
                if code not in {250, 251}:
                    raise smtplib.SMTPRecipientsRefused(
                        {self.recipient: (code, b"Recipient rejected")}
                    )
                data_started = True
                code, response = smtp.data(mail.as_bytes(policy=SMTP))
                if code != 250:
                    raise smtplib.SMTPDataError(code, b"Message rejected")
                attempt.update(
                    status="SENT",
                    provider_message_id=response.decode("utf8", "replace")[:240],
                    smtp_acceptance_code=code,
                    inbox_delivery_proven=False,
                )
        except smtplib.SMTPResponseException as exc:
            if attempt["status"] != "SENT":
                attempt.update(status="FAILED", error=type(exc).__name__, smtp_code=exc.smtp_code)
            else:
                attempt["connection_close_error"] = type(exc).__name__
        except smtplib.SMTPRecipientsRefused:
            attempt.update(status="FAILED", error="SMTPRecipientsRefused")
        except (OSError, smtplib.SMTPException) as exc:
            attempt.update(status="UNKNOWN" if data_started else "FAILED", error=type(exc).__name__)
        with self.db() as db:
            db.execute("UPDATE notifications SET result=? WHERE key=?", (json.dumps(attempt), key))
        return attempt


class ConnectorEmail(ConfirmationEmail):
    """Explicit demo bridge to the user's connected Gmail tool, not SMTP credentials.

    A separately running authorized Codex dispatcher writes provider acknowledgment.
    Without that dispatcher no SENT claim is made. Pending attempts are never retried.
    """

    def __init__(self, ledger, bridge, *, recipient, authorized_recipients):
        super().__init__(ledger, recipient=recipient, authorized_recipients=authorized_recipients)
        self.outbound = True
        self.sender = self.recipient
        self.bridge = Path(bridge)
        self.bridge.mkdir(parents=True, exist_ok=True)

    def send(self, receipt):
        import time

        from kyron_eval.storage import atomic_json

        preview = self.preview(receipt)
        key = sha256(
            (
                "appointment-confirmation|"
                + receipt["patient_id"]
                + "|"
                + receipt["appointment"]["appointment_id"]
                + "|"
                + self.recipient
            ).encode()
        ).hexdigest()
        attempt = {
            "attempt_id": uuid4().hex,
            "appointment_id": receipt["appointment"]["appointment_id"],
            "status": "QUEUED",
            "message": preview,
            "provider_message_id": None,
            "transport": "CODEX_GMAIL_CONNECTOR_ASSISTED",
            "delivery_status": "UNKNOWN",
        }
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT result FROM notifications WHERE key=?", (key,)).fetchone()
            if prior:
                return json.loads(prior[0])
            db.execute("INSERT INTO notifications VALUES (?,?)", (key, json.dumps(attempt)))
        request = self.bridge / (attempt["attempt_id"] + ".request.json")
        resultfile = self.bridge / (attempt["attempt_id"] + ".result.json")
        atomic_json(request, {**attempt, "verified_receipt": receipt})
        request.chmod(0o600)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if resultfile.exists():
                result = json.loads(resultfile.read_text())
                if (
                    result.get("attempt_id") == attempt["attempt_id"]
                    and result.get("appointment_id") == attempt["appointment_id"]
                    and result.get("recipient") == self.recipient
                    and result.get("status") == "SENT"
                    and result.get("provider_message_id")
                ):
                    attempt.update(
                        status="SENT",
                        provider_receipt=result,
                        provider_message_id=result["provider_message_id"],
                        inbox_delivery_proven=result.get("inbox_observed") is True,
                        delivery_status="DELIVERED"
                        if result.get("inbox_observed") is True
                        else "UNKNOWN",
                    )
                else:
                    attempt.update(status="UNKNOWN", error="CONNECTOR_ACK_NOT_VERIFIED")
                break
            time.sleep(0.1)
        with self.db() as db:
            db.execute("UPDATE notifications SET result=? WHERE key=?", (json.dumps(attempt), key))
        return attempt
