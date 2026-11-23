"""Name-first receptionist. Patient matching grants no authority to mutate the EHR."""

from datetime import UTC, datetime

from kyron_eval.voice.live_lookup import LiveLookupSession, lookup_scenario
from kyron_eval.voice.service import KyronVoiceService

REQUEST_CATALOG = [
    "General consultation",
    "Follow-up visit",
    "Annual wellness visit",
    "Specialist consultation",
]


class ReceptionistSession(LiveLookupSession):
    def __init__(self, provider, artifact_root="artifacts", *, booking_enabled=False):
        scenario = lookup_scenario()
        scenario.id = "receptionist-name-first"
        scenario.title = "AI receptionist · name-first patient matching and appointment retrieval"
        scenario.version = "receptionist-1.0"
        scenario.initial_state.patient_id = "synthetic-unmatched"
        if booking_enabled:
            scenario.id = "receptionist-booking-transaction"
            scenario.title = "Verified Office Visit booking and restricted email notification"
            scenario.caller_goal = (
                "Select and explicitly confirm an operator-controlled "
                "Office Visit and notification."
            )
            scenario.expected.acceptable_outcomes = ["BOOKED", "NOT_BOOKED"]
            scenario.expected.explanation = (
                "A booking claim requires exact authoritative readback; email is separate."
            )

        super().__init__(provider, "", artifact_root, mode="receptionist", scenario=scenario)
        self.world.trace.agent_version = "guava-receptionist-1.0"
        self.candidate = None
        self.verified_patient = None
        self.display_name = None
        self.identity_attempts = 0
        self.sync = "NOT_REQUESTED"
        self.appointments = []
        self.selected_appointment = None
        self.preferences = None
        self.requested_operation = "VIEW_APPOINTMENTS"
        self.phase = "receptionist_name"
        self.booking = None

    def caller(self, call_id, event):
        super().caller(call_id, event)
        with self.lock:
            if self.ended or not self.service.bind_call(call_id):
                return
            self.triage.observe(event.utterance_id, event.utterance, invalidate_prediction=False)
            if self.booking:
                self.booking.observe_caller(call_id, event.utterance_id, event.utterance)
            if self.triage.urgent_latched:
                self.record_triage()

    def identify(self, call_id, full_name, birth_date):
        with self.lock:
            if self.ended or not self.service.bind_call(call_id):
                return {"completed": False}
            self.identity_attempts += 1
            self.candidate = self.verified_patient = self.display_name = None
            self.appointments, self.lookup_result = [], None
            self.selected_appointment = None
            self.service.end_session()
            self.world.state.patient_id = "synthetic-unmatched"
            self.service = KyronVoiceService(self.world)
            self.service.bind_call(call_id)
            if self.identity_attempts > 3:
                return {
                    "completed": False,
                    "message": "Please contact the clinic to verify your details.",
                }
            self.world.now = datetime.now(UTC)
            result = self.world.ehr_call(
                "match_name_and_dob", full_name=full_name, birth_date=birth_date
            )
            self.event(
                "state",
                name="receptionist.identity_search",
                status=result.status,
                disclosure_authorized=False,
                agent_visible=False,
            )
            if result.ok and result.authoritative and result.data.get("name_dob_matched"):
                self.candidate = {
                    "patient_id": result.data["candidate_id"],
                    "birth_date": birth_date,
                }
                return {
                    "completed": True,
                    "additional_factor_required": "PHONE",
                    "disclosure_authorized": False,
                }
            return {
                "completed": False,
                "message": "I could not verify those details. Please contact the clinic.",
            }

    def verify_phone_and_read(self, call_id, phone):
        with self.lock:
            if self.ended or not self.service.bind_call(call_id) or not self.candidate:
                return {
                    "completed": False,
                    "message": "Please verify your name and date of birth first.",
                }
            candidate = self.candidate
            self.candidate = None  # One verification attempt; never reusable model fields.
            self.world.state.patient_id = candidate["patient_id"]
            self.service = KyronVoiceService(self.world)
            self.service.bind_call(call_id)
            verified = self.service.verify_identity(
                patient_id=candidate["patient_id"], birth_date=candidate["birth_date"], phone=phone
            )
            self.event(
                "tool",
                name="receptionist.verify_identity",
                result=verified.model_dump(mode="json"),
                agent_visible=True,
            )
            if not verified.completed:
                self.world.state.patient_id = "synthetic-unmatched"
                self.sync = "IDENTITY_NOT_VERIFIED"
                return {
                    "completed": False,
                    "message": "I could not verify your identity. Please contact the clinic.",
                }
            self.verified_patient = candidate["patient_id"]
            return self.refresh(call_id)

    def refresh(self, call_id):
        with self.lock:
            if (
                self.ended
                or not self.service.bind_call(call_id)
                or not self.service.identity_verified
            ):
                return {"completed": False, "message": "Identity verification is required."}
            self.sync = "LOADING"
            core = self.service.get_appointments(patient_id=self.verified_patient)
            if not core.completed:
                self.sync = core.ehr_status
                return {
                    "completed": False,
                    "message": "Appointments are unavailable. Please contact the clinic.",
                }
            detail = self.world.ehr_call(
                "get_receptionist_appointments", patient_id=self.verified_patient
            )
            if not detail.ok or not detail.authoritative:
                self.sync = detail.status
                return {
                    "completed": False,
                    "message": (
                        "I could not confirm the appointment details. Please contact the clinic."
                    ),
                }
            rows = detail.data["appointments"]
            if any(r["patient_id"] != self.verified_patient for r in rows):
                self.sync = "PATIENT_BINDING_REJECTED"
                return {
                    "completed": False,
                    "message": "I could not confirm the appointment details.",
                }
            self.appointments, self.display_name = rows, detail.data["patient_name"]
            self.sync = "EMPTY" if not rows else "SYNCED"
            self.selected_appointment = rows[0]["appointment_id"] if len(rows) == 1 else None
            self.lookup_result = {
                "completed": True,
                "appointments": rows,
                "read_only": True,
                "ehr_status": detail.status,
                "patient_name": self.display_name,
            }
            self.event(
                "tool",
                name="receptionist.appointment_read",
                result=self.lookup_result,
                agent_visible=True,
            )
            return self.lookup_result

    def select(self, appointment_id):
        with self.lock:
            # Session-end snapshots may still be inspected; selection never authorizes a mutation.
            if not self.verified_patient or not any(
                r["appointment_id"] == appointment_id and r["patient_id"] == self.verified_patient
                for r in self.appointments
            ):
                return {
                    "completed": False,
                    "reason": "Appointment does not belong to the verified session.",
                }
            self.selected_appointment = appointment_id
            self.event(
                "state",
                name="receptionist.appointment_selected",
                appointment_id=appointment_id,
                source="explicit selection; no mutation consent",
            )
            if self.ended:
                self.save("Post-session appointment selection; no mutation")
            return {"completed": True}

    def request(self, call_id, operation, preferences=None):
        with self.lock:
            if (
                self.ended
                or not self.service.bind_call(call_id)
                or not self.service.identity_verified
            ):
                return {"completed": False, "message": "Please verify your identity first."}
            if operation not in {"VIEW_APPOINTMENTS", "NEW_APPOINTMENT", "RESCHEDULE", "CANCEL"}:
                return {"completed": False, "message": "Please contact the clinic for assistance."}
            self.requested_operation = operation
            self.preferences = preferences
            if self.triage.urgent_latched:
                return {
                    "completed": False,
                    "message": (
                        "Routine scheduling is suspended. Seek urgent medical assistance; "
                        "call 911 for an emergency."
                    ),
                }
            result = self.world.ehr_call("get_availability", now=datetime.now(UTC))
            self.availability_result = result.model_dump(mode="json")
            self.event(
                "policy",
                name="receptionist.mutation_restricted",
                operation=operation,
                reason="NO_VERIFIED_AVAILABILITY_AND_INDEPENDENT_WRITE_REVIEW_PENDING",
                preferences=preferences,
                writes_enabled=False,
            )
            return {
                "completed": False,
                "availability_status": result.status,
                "message": (
                    "I cannot confirm an available time or make appointment changes here. "
                    "Please contact the clinic's scheduling team. "
                    "No appointment has been changed."
                ),
            }

    def snapshot(self):
        result = super().snapshot()
        result.update(
            patient_id=self.verified_patient,
            patient_name=self.display_name,
            appointments=self.appointments,
            selected_appointment=self.selected_appointment,
            consent="No exact mutation confirmation established; no write authorized",
            synchronization=self.sync,
            requested_operation=self.requested_operation,
            preferences=self.preferences,
            request_catalog=REQUEST_CATALOG,
            catalog_source="Demonstration request catalog; not verified bookable EHR types",
            active_task="ended"
            if self.ended
            else (
                "Appointment assistance"
                if self.verified_patient
                else "Additional identity verification"
                if self.candidate
                else "Full name and date of birth"
            ),
        )
        if self.booking:
            result["booking"] = self.booking.snapshot()
            result["ehr_acknowledgment"] = self.booking.last_ack or "No booking submitted"
            result["transport"] = self.transport
        return result

    def agent_speech(self, call_id, event):
        super().agent_speech(call_id, event)
        if self.booking and self.service.bind_call(call_id):
            self.booking.observe_agent(self.world.trace.events[-1].id, event.utterance)

    def save(self, reason):
        if self.booking:
            self.world.trace.ehr["booking_transaction"] = self.booking.snapshot()
        self.world.trace.ehr["receptionist_session"] = {
            "patient_id": self.verified_patient,
            "patient_name": self.display_name,
            "selected_appointment": self.selected_appointment,
            "sync": self.sync,
            "requested_operation": self.requested_operation,
            "preferences": self.preferences,
            "identity_policy": "Unique native name/DOB match + existing approved DOB/phone factors",
            "writes_enabled": bool(self.booking),
        }
        return super().save(reason)
