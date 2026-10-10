"""Prepare plain-text messages only. This module has no delivery capability."""

from zoneinfo import ZoneInfo

from app.thresholds import timestamp


def prepare_message(location: dict, decision: dict, manager: dict | None) -> tuple[str, str]:
    """Include local dates, offsets, essential-staff caveat and human review status."""
    zone = ZoneInfo(location["timezone"])
    start = timestamp(decision["affected_start"]).astimezone(zone)
    end = timestamp(decision["affected_end"]).astimezone(zone)
    action = decision["posture"].replace("_", " ")
    contact = f"{manager['name']} ({manager['email']})" if manager else "your facility manager"
    subject = f"{location['name']} — {action} — {start:%b %d}"
    body = (f"DRAFT — pending manager approval; this is not a finalized instruction.\n\n"
            f"Facility: {location['name']}\nRecommended action: {action}\n"
            f"Effective: {start:%Y-%m-%d %H:%M %Z %z} to {end:%Y-%m-%d %H:%M %Z %z} ({zone.key})\n\n"
            f"{decision['reason']}\n\n"
            "Essential personnel: await specific instructions from your manager; do not assume you are off duty.\n"
            f"Manager contact: {contact}\n")
    return subject, body
