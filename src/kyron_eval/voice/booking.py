"""Clinic-controlled synthetic inventory and exact-consent booking transaction.

This inventory is authoritative only for the operator-reserved slots in its marked,
exclusive synthetic namespace. No availability is inferred from an empty calendar.
"""

import fcntl
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from functools import wraps
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from clinical_triage.domain.scheduling import (
    AppointmentConfirmation,
    AppointmentOffer,
    AppointmentSlot,
    AvailabilitySnapshot,
)

STATES = {
    "IDENTITY_PENDING": {"IDENTITY_VERIFIED"},
    "IDENTITY_VERIFIED": {"TRIAGE_PENDING"},
    "TRIAGE_PENDING": {"SCHEDULING_PERMITTED", "COMPLETE"},
    "SCHEDULING_PERMITTED": {"OPTIONS_PRESENTED", "COMPLETE"},
    "OPTIONS_PRESENTED": {"OPTION_SELECTED", "COMPLETE"},
    "OPTION_SELECTED": {"AWAITING_CONFIRMATION"},
    "AWAITING_CONFIRMATION": {"BOOKING_IN_PROGRESS", "COMPLETE"},
    "BOOKING_IN_PROGRESS": {"EHR_CONFIRMED", "BOOKING_UNKNOWN", "COMPLETE"},
    "EHR_CONFIRMED": {"EMAIL_QUEUED"},
    "EMAIL_QUEUED": {"EMAIL_SENT", "EMAIL_FAILED"},
    "EMAIL_SENT": {"COMPLETE"},
    "EMAIL_FAILED": {"COMPLETE"},
    "BOOKING_UNKNOWN": {"COMPLETE"},
    "COMPLETE": set(),
}


def normalized(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def affirmative_response(text):
    """Recognize a plain affirmative, rejecting mixed/qualified clinical answers.

    This only interprets speech; current exact question and session evidence are
    separately required. A model field never calls this on the caller's behalf.
    """
    return bool(
        re.fullmatch(
            r"(?:yes|yeah|yep|yup|sure|absolutely|correct|"
            r"(?:that s|that is) (?:correct|right|fine))"
            r"(?: (?:(?:that s|that is) (?:correct|right|fine)|it is(?: routine)?|"
            r"please(?: do| book it)?|go ahead|book (?:it|that appointment|the appointment)))*",
            normalized(text),
        )
    )


def serialized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self.session.lock:
            return method(self, *args, **kwargs)

    return locked


class ClinicInventory:
    def __init__(self, config, ledger):
        self.config_path = Path(config) if isinstance(config, (str, Path)) else None
        self.config = (
            json.loads(Path(config).read_text()) if isinstance(config, (str, Path)) else config
        )
        if self.config.get(
            "authority"
        ) != "OPERATOR_APPROVED_EXCLUSIVE_SYNTHETIC_CLINIC" or not self.config.get("approved_by"):
            raise ValueError("Explicit operator-approved clinic inventory required")
        self.slots = [AppointmentSlot.model_validate(s) for s in self.config["slots"]]
        if any(
            s.provider_id != "synthetic-provider-a" or s.location_id != "synthetic-clinic"
            for s in self.slots
        ):
            raise ValueError("Inventory targets are not allowlisted")
        if any(
            a.starts_at < b.ends_at and b.starts_at < a.ends_at
            for i, a in enumerate(self.slots)
            for b in self.slots[i + 1 :]
        ):
            raise ValueError("Overlapping clinic inventory")
        if len({s.slot_id for s in self.slots}) != len(self.slots):
            raise ValueError("Duplicate inventory slot")
        self.ledger = Path(ledger)
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS reservations (slot TEXT PRIMARY KEY, "
                "tx TEXT UNIQUE NOT NULL, receipt TEXT)"
            )
        self.ledger.chmod(0o600)

    def db(self):
        return sqlite3.connect(self.ledger, timeout=15)

    def available(self, provider, now):
        if self.config_path and json.loads(self.config_path.read_text()) != self.config:
            raise ValueError("Clinic inventory changed; previous options invalid")
        if now.tzinfo is None:
            raise ValueError("Aware clock required")
        # All-clinic native read, not just this patient's bookings. The pinned endpoint
        # returns an unpaginated list. Any unexpected envelope/large result fails closed.
        if (
            self.config.get("provider_uuid") != provider.manifest["provider"]["uuid"]
            or self.config.get("facility_uuid") != provider.manifest["facility"]["uuid"]
            or self.config.get("appointment_type") != "Office Visit"
        ):
            raise ValueError("Clinic inventory binding mismatch")
        rows = provider._request("GET", "api/appointment", params={"_count": "1000"})
        if not isinstance(rows, list) or len(rows) >= 1000:
            raise ValueError("Incomplete clinic conflict evidence")
        allowed_pids = {str(p["pid"]) for p in provider.patient_map.values()}
        if any(
            str(r.get("pid")) not in allowed_pids
            or not str(r.get("pc_title", "")).startswith("KYRON_SYNTHETIC")
            for r in rows
        ):
            raise ValueError("Unmarked clinic appointment; inventory disabled")
        with self.db() as db:
            reserved = {r[0] for r in db.execute("SELECT slot FROM reservations")}
        result = []
        for slot in self.slots:
            if slot.slot_id in reserved or slot.starts_at <= now:
                continue
            conflict = False
            for row in rows:
                if str(row["pc_aid"]) != str(provider.manifest["provider"]["id"]):
                    continue
                start = datetime.fromisoformat(
                    row["pc_eventDate"] + "T" + row["pc_startTime"] + "+00:00"
                )
                end = datetime.fromisoformat(
                    row["pc_eventDate"] + "T" + row["pc_endTime"] + "+00:00"
                )
                if start < slot.ends_at and slot.starts_at < end:
                    conflict = True
            if not conflict:
                result.append(slot)
        return result


class BookingTransaction:
    def __init__(self, session, provider, inventory, email, root, *, writes_enabled=False):
        self.session, self.provider, self.inventory, self.email = (
            session,
            provider,
            inventory,
            email,
        )
        self.root = Path(root)
        self.id = uuid4().hex
        self.state = "IDENTITY_PENDING"
        self.history, self.options = [], []
        self.selected = self.consent = self.receipt = self.notification = None
        self.prompt_events, self.prompt_text = [], ""
        self.prompt_intent = False
        self.prompt_at = None
        self.seen_caller = {}
        self.writes_enabled = writes_enabled
        self.final_message = None
        self.last_ack = None
        self.preferences = None
        self.issued_at = None
        self.reason = None
        self.visit_prompt = None
        self.visit_consent = None
        self.visit_prompt_text = ""
        self.visit_prompt_events = []

    def transition(self, target):
        if target not in STATES[self.state]:
            raise ValueError("Invalid booking transition " + self.state + " -> " + target)
        old, self.state = self.state, target
        event = self.session.event(
            "state", name="booking.transition", transaction_id=self.id, previous=old, state=target
        )
        self.history.append({"event_id": event.id, "state": target})

    def authorized(self):
        return (
            not self.session.ended
            and self.session.service.identity_verified
            and self.session.verified_patient == self.session.service.bound_patient_id
            and not self.session.triage.urgent_latched
            and (not self.session.triage.prediction or self.session.triage.state == "NONURGENT")
            and self.session.world.state.disposition.value not in {"EMERGENCY", "HUMAN_REVIEW"}
        )

    @serialized
    def present(self, reason, preferences):
        if self.state not in {"IDENTITY_PENDING", "TRIAGE_PENDING"} or not self.authorized():
            return {"completed": False, "message": "Identity verification is required."}
        if self.state == "IDENTITY_PENDING":
            self.transition("IDENTITY_VERIFIED")
            self.transition("TRIAGE_PENDING")
        # A free-text request is not a diagnosis or proof of clinical eligibility.
        # Ground it in speech, then clarify the actual configured service rather
        # than rejecting callers whose wording is absent from a phrase allowlist.
        caller = [normalized(t) for t in self.session.caller_revisions.values()]
        if not normalized(reason or "") or not any(normalized(reason) in t for t in caller):
            return {
                "completed": False,
                "status": "NEEDS_REASON",
                "message": "What would you like the appointment for?",
            }
        if self.reason != normalized(reason):
            self.visit_consent = self.visit_prompt = None
            self.visit_prompt_text = ""
            self.visit_prompt_events = []
        self.reason = normalized(reason)
        proof = self.visit_consent
        if not proof or not self._visit_consent_valid():
            return {
                "completed": False,
                "status": "NEEDS_CLARIFICATION",
                "message": (
                    "We can schedule a routine Office Visit. Is this a routine visit "
                    "with no new or worsening symptoms?"
                ),
            }
        if not proof["accepted"]:
            self.transition("COMPLETE")
            return {
                "completed": False,
                "message": "Please contact the clinic for help choosing appropriate care. "
                "No appointment was booked and no email was sent.",
            }
        self.preferences = preferences
        self.transition("SCHEDULING_PERMITTED")
        now = datetime.now(UTC)
        try:
            self.options = self.inventory.available(self.provider, now)[:3]
        except Exception as exc:
            self.session.event(
                "error", name="booking.availability_unavailable", error=type(exc).__name__
            )
            self.options = []
        if not self.options:
            self.transition("COMPLETE")
            return {
                "completed": False,
                "message": (
                    "I cannot confirm an available time. Please contact the clinic "
                    "scheduling team. No appointment was booked."
                ),
            }
        self.issued_at = now
        self.transition("OPTIONS_PRESENTED")
        return {
            "completed": True,
            "options": [self.describe(s, str(i + 1)) for i, s in enumerate(self.options)],
            "source": self.inventory.config["authority"],
            "expires_in_seconds": 180,
        }

    def describe(self, slot, option):
        return {
            "option": option,
            "slot_id": slot.slot_id,
            "local_time": slot.starts_at.astimezone(ZoneInfo("America/New_York")).strftime(
                "%A, %B %d, %Y at %I:%M %p %Z"
            ),
            "starts_at": slot.starts_at.isoformat(),
            "ends_at": slot.ends_at.isoformat(),
            "clinician": self.inventory.config["clinician"],
            "location": self.inventory.config["location"],
            "appointment_type": self.inventory.config["appointment_type"],
        }

    @serialized
    def select(self, option):
        if (
            self.state != "OPTIONS_PRESENTED"
            or not self.authorized()
            or option not in [str(i + 1) for i in range(len(self.options))]
        ):
            return {"completed": False}
        if datetime.now(UTC) > self.issued_at + timedelta(seconds=180):
            return {
                "completed": False,
                "message": "The options expired. Please contact the clinic.",
            }
        self.selected = self.options[int(option) - 1]
        self.transition("OPTION_SELECTED")
        self.transition("AWAITING_CONFIRMATION")
        result = self.describe(self.selected, option)
        result.update(
            recipient=self.email.recipient,
            confirmation_phrase="Yes, please book that appointment and email my confirmation.",
        )
        self.session.event(
            "state",
            name="booking.selection_readout_instruction",
            result=result,
            delivered_to_caller=False,
        )
        return {"completed": True, **result}

    @serialized
    def observe_agent(self, event_id, text):
        if self.state == "TRIAGE_PENDING":
            self.visit_prompt_text += " " + text
            self.visit_prompt_events.append(event_id)
            words = normalized(self.visit_prompt_text)
            if "routine office visit" in words and "no new or worsening symptoms" in words:
                self.visit_prompt = {
                    "event_id": event_id,
                    "at": datetime.now(UTC),
                    "reason": self.reason,
                    "event_ids": list(self.visit_prompt_events),
                }
            elif "?" in text:
                self.visit_prompt = None
                self.visit_prompt_text = ""
                self.visit_prompt_events = []
            return
        if self.state != "AWAITING_CONFIRMATION":
            return
        self.prompt_text += " " + text
        self.prompt_events.append(event_id)
        words = normalized(text)
        if (
            re.search(r"\b(book|booking|appointment)\b", words)
            and re.search(r"\b(email|confirmation)\b", words)
            and re.search(r"\b(confirm|yes|go ahead|may i|shall i|would you|do you)\b", words)
        ):
            self.prompt_intent = True
            self.prompt_at = datetime.now(UTC)
        elif "?" in text:
            self.prompt_intent = False

    @serialized
    def observe_caller(self, call_id, utterance_id, text):
        if not utterance_id or not self.session.service.bind_call(call_id):
            return
        if self.seen_caller.get(utterance_id) == text:
            return
        self.seen_caller[utterance_id] = text
        self.consent = None
        if self.visit_consent and self.visit_consent["utterance_id"] == utterance_id:
            self.visit_consent = None
        if self.state == "TRIAGE_PENDING" and self.authorized():
            prompt = self.visit_prompt
            words = normalized(text)
            if (
                prompt
                and prompt["reason"] == self.reason
                and datetime.now(UTC) <= prompt["at"] + timedelta(seconds=90)
                and (affirmative_response(text) or words in {"no", "no thanks"})
            ):
                self.visit_consent = {
                    "accepted": words not in {"no", "no thanks"},
                    "utterance_id": utterance_id,
                    "text": text,
                    "patient_id": self.session.verified_patient,
                    "service_session_id": self.session.service.session_id,
                    "reason": self.reason,
                }
                self.session.event(
                    "state",
                    name="booking.visit_clarification",
                    source="actual caller speech callback",
                    prompt_event_id=prompt["event_id"],
                    prompt_event_ids=prompt["event_ids"],
                    **self.visit_consent,
                )
            return
        if self.state != "AWAITING_CONFIRMATION" or not self.authorized():
            return
        prompt = normalized(self.prompt_text)
        recipient_words = re.sub(r"[^a-z0-9]", "", self.email.recipient.casefold())
        spoken_address = self.prompt_text.casefold().replace(" dot ", ".").replace(" at ", "@")
        address_present = recipient_words in re.sub(r"[^a-z0-9]", "", spoken_address)
        selected = self.selected.starts_at.astimezone(ZoneInfo("America/New_York"))
        words = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", prompt)
        for word, number in [("ten", "10"), ("eleven", "11"), ("twelve", "12"), ("thirty", "30")]:
            words = re.sub(r"\b" + word + r"\b", number, words)
        hour = str(int(selected.strftime("%I")))
        minute = selected.strftime("%M")
        clock = (
            hour
            + (r"(?: 00)?" if minute == "00" else " " + minute)
            + " "
            + selected.strftime("%p").casefold()
        )
        readout_complete = (
            normalized(self.inventory.config["clinician"]) in words
            and normalized(self.inventory.config["location"]) in words
            and "office visit" in words
            and (selected.strftime("%B").casefold() + " " + str(selected.day)) in words
            and str(selected.year) in words
            and re.search(r"\b" + clock + r"\b", words)
            and re.search(r"\b(?:est|eastern)\b", words)
        )
        if not address_present or not readout_complete:
            return
        words = normalized(text)
        contextual_yes = affirmative_response(text)
        explicit_booking_email = bool(
            re.fullmatch(
                r"yes (?:please )?book(?: (?:it|that appointment|the appointment|appointment))?"
                r"(?: please)?(?: and)? (?:email|send)(?: me)? "
                r"(?:my |the )?confirmation(?: email)?",
                words,
            )
        )
        prompt_fresh = self.prompt_at and datetime.now(UTC) <= self.prompt_at + timedelta(
            seconds=90
        )
        if not (self.prompt_intent and prompt_fresh and (contextual_yes or explicit_booking_email)):
            return
        selection_hash = sha256(
            json.dumps(
                {"slot": self.selected.model_dump(mode="json"), "recipient": self.email.recipient},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        event = self.session.event(
            "state",
            name="booking.exact_consent",
            source="actual caller speech callback",
            utterance_id=utterance_id,
            patient_id=self.session.verified_patient,
            service_session_id=self.session.service.session_id,
            slot=self.selected.model_dump(mode="json"),
            recipient=self.email.recipient,
            prompt_event_ids=list(self.prompt_events),
            selected_payload_sha256=selection_hash,
        )
        self.consent = {
            "event_id": event.id,
            "selected_payload_sha256": selection_hash,
            "utterance_id": utterance_id,
            "text": text,
            "at": datetime.now(UTC),
            "patient_id": self.session.verified_patient,
            "service_session_id": self.session.service.session_id,
        }

    def _visit_consent_valid(self):
        proof = self.visit_consent
        return bool(
            proof
            and proof["reason"] == self.reason
            and proof["patient_id"] == self.session.verified_patient
            and proof["service_session_id"] == self.session.service.session_id
            and self.seen_caller.get(proof["utterance_id"]) == proof["text"]
        )

    @serialized
    def commit(self):
        if self.receipt or self.state == "COMPLETE":
            if self.consent and (
                self.consent["patient_id"] != self.session.verified_patient
                or self.consent["service_session_id"] != self.session.service.session_id
            ):
                return {
                    "completed": False,
                    "message": "This transaction belongs to another verified identity session.",
                }
            return self.outcome()
        now = datetime.now(UTC)
        c = self.consent
        if (
            self.state != "AWAITING_CONFIRMATION"
            or not self.authorized()
            or not self._visit_consent_valid()
            or not self.visit_consent["accepted"]
            or not c
            or c["patient_id"] != self.session.verified_patient
            or c["service_session_id"] != self.session.service.session_id
            or now > c["at"] + timedelta(seconds=90)
            or now > self.issued_at + timedelta(seconds=180)
            or self.seen_caller.get(c["utterance_id"]) != c["text"]
            or c["selected_payload_sha256"]
            != sha256(
                json.dumps(
                    {
                        "slot": self.selected.model_dump(mode="json"),
                        "recipient": self.email.recipient,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        ):
            return {
                "completed": False,
                "message": (
                    "I do not have fresh confirmation for this exact appointment and "
                    "email address. No booking was submitted."
                ),
            }
        if not self.writes_enabled:
            return {
                "completed": False,
                "message": (
                    "Booking is disabled. Please contact the clinic. No appointment was booked."
                ),
            }
        self.transition("BOOKING_IN_PROGRESS")
        lockpath = self.inventory.ledger.with_suffix(".lock")
        with lockpath.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                available = self.inventory.available(self.provider, now)
                if self.selected not in available:
                    self.final_message = (
                        "That time is no longer available. No booking was submitted. "
                        "Please contact the clinic."
                    )
                    self.transition("COMPLETE")
                    return self.outcome()
                with self.inventory.db() as db:
                    db.execute(
                        "INSERT INTO reservations(slot,tx) VALUES (?,?)",
                        (self.selected.slot_id, self.id),
                    )
                snapshot = AvailabilitySnapshot(
                    snapshot_id=self.id,
                    observed_at=now,
                    expires_at=now + timedelta(seconds=30),
                    slots=tuple(available),
                )
                offer = AppointmentOffer(
                    offer_id=self.id,
                    snapshot=snapshot,
                    slot=self.selected,
                    offered_at=now,
                    expires_at=snapshot.expires_at,
                )
                confirmation = AppointmentConfirmation(
                    confirmation_id=self.id,
                    confirmation_event_id=c["event_id"],
                    offer=offer,
                    confirmed_at=now,
                )
                self.provider.allow_writes = True
                ack = self.provider.create_appointment(
                    patient_id=c["patient_id"], confirmation=confirmation, idempotency_key=self.id
                )
                self.provider.allow_writes = False
                self.last_ack = ack.model_dump(mode="json")
                self.session.event(
                    "tool",
                    name="booking.create_appointment",
                    result=ack.model_dump(mode="json"),
                    agent_visible=True,
                )
                readback = self.provider.get_appointments(c["patient_id"])
                matches = [
                    r
                    for r in readback.data.get("bookings", [])
                    if r.get("patient_id") == c["patient_id"]
                    and AppointmentSlot.model_validate(r.get("slot")) == self.selected
                    and r.get("confirmation_event_id") == c["event_id"]
                ]
                aid = str(ack.data.get("appointment_id", ""))
                verified = (
                    readback.ok
                    and readback.authoritative
                    and len(matches) == 1
                    and (not aid or matches[0]["appointment_id"] == aid)
                )
                self.session.event(
                    "tool",
                    name="booking.independent_readback",
                    result=readback.model_dump(mode="json"),
                    verified=verified,
                    agent_visible=True,
                )
                if not verified:
                    self.transition("BOOKING_UNKNOWN")
                    self.final_message = (
                        "I couldn't verify whether the appointment was saved. Please "
                        "check with the clinic before trying again. No confirmation email "
                        "was sent."
                    )
                    self.transition("COMPLETE")
                    return self.outcome()
                details = self.provider.get_receptionist_appointments(c["patient_id"])
                actual = [
                    a
                    for a in details.data.get("appointments", [])
                    if a["appointment_id"] == matches[0]["appointment_id"]
                ]
                if (
                    not details.ok
                    or not details.authoritative
                    or len(actual) != 1
                    or actual[0]["clinician"] != self.inventory.config["clinician"]
                    or actual[0]["location"] != self.inventory.config["location"]
                    or actual[0]["appointment_type"] != self.inventory.config["appointment_type"]
                ):
                    raise ValueError("Display fields not verified")
                description = self.describe(self.selected, "")
                description["appointment_id"] = matches[0]["appointment_id"]
                self.receipt = {
                    "status": "EHR_CONFIRMED",
                    "transaction_id": self.id,
                    "patient_id": c["patient_id"],
                    "appointment": description,
                    "acknowledgment": ack.model_dump(mode="json"),
                    "readback": readback.model_dump(mode="json"),
                    "consent_event_id": c["event_id"],
                }
                self.root.mkdir(parents=True, exist_ok=True)
                receiptfile = self.root / ("confirmation-" + self.id + ".json")
                with receiptfile.open("x") as out:
                    json.dump(self.receipt, out, indent=2)
                receiptfile.chmod(0o600)
                with self.inventory.db() as db:
                    db.execute(
                        "UPDATE reservations SET receipt=? WHERE tx=?",
                        (json.dumps(self.receipt), self.id),
                    )
                self.transition("EHR_CONFIRMED")
            except Exception as exc:
                self.provider.allow_writes = False
                self.receipt = None
                self.session.event(
                    "error", name="booking.transaction_error", error=type(exc).__name__
                )
                self.transition("BOOKING_UNKNOWN")
                self.final_message = (
                    "I couldn't verify whether the appointment was saved. Please "
                    "check with the clinic before trying again. No confirmation email "
                    "was sent."
                )
                self.transition("COMPLETE")
                return self.outcome()
        self.transition("EMAIL_QUEUED")
        try:
            self.notification = self.email.send(self.receipt)
        except Exception as exc:
            self.notification = {
                "status": "UNKNOWN",
                "error": type(exc).__name__,
                "delivery_status": "UNKNOWN",
            }
        self.session.event(
            "tool", name="booking.confirmation_email", result=self.notification, agent_visible=True
        )
        self.transition("EMAIL_SENT" if self.notification["status"] == "SENT" else "EMAIL_FAILED")
        a = self.receipt["appointment"]
        self.final_message = (
            f"Your appointment is confirmed for {a['local_time']} "
            f"with {a['clinician']} at {a['location']}. "
            f"Your reference is {a['appointment_id']}. "
        )
        if self.notification["status"] == "SENT":
            self.final_message += (
                (
                    "The confirmation was accepted by the local test inbox; it was "
                    "not sent to your Gmail inbox."
                )
                if not self.email.outbound
                else (
                    "The email service accepted your confirmation email. Inbox "
                    "delivery is not verified."
                )
            )
        else:
            self.final_message += (
                "I couldn't verify that the confirmation email was sent, but your "
                "appointment is saved."
            )
        self.transition("COMPLETE")
        self.session.refresh(self.session.service._call_id)
        return self.outcome()

    def outcome(self):
        return {
            "completed": bool(self.receipt),
            "message": self.final_message or "No booking was submitted.",
            "receipt": self.receipt,
            "email": self.notification,
        }

    def snapshot(self):
        return {
            "transaction_id": self.id,
            "acknowledgment": self.last_ack,
            "state": self.state,
            "history": self.history,
            "options": [self.describe(s, str(i + 1)) for i, s in enumerate(self.options)],
            "selected": self.describe(self.selected, "") if self.selected else None,
            "confirmation_recorded": bool(self.consent),
            "receipt": self.receipt,
            "email": self.notification,
            "email_preview": self.email.preview(self.receipt) if self.receipt else None,
            "mailpit_url": "http://127.0.0.1:9325",
            "writes_enabled": self.writes_enabled,
            "request_reason": self.reason,
            "visit_clarification": self.visit_consent,
        }
