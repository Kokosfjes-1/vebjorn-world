"""Admin API for invitation responses.

GET    /api/rsvps        -> all responses, newest first
DELETE /api/rsvps/{id}   -> delete one response
"""

import base64
import json
import logging
import os
import re
from datetime import datetime

import azure.functions as func
from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableClient

PARTITION = "rsvp"
ROW_KEY_RE = re.compile(r"^[0-9a-f]{32}$")

_table = None


def get_table() -> TableClient:
    global _table
    if _table is None:
        _table = TableClient.from_connection_string(
            os.environ["STORAGE_CONNECTION_STRING"],
            table_name=os.environ.get("TABLE_NAME", "Rsvp"),
        )
    return _table


def respond(status: int, body) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body, ensure_ascii=False),
        status_code=status,
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


def is_admin(req: func.HttpRequest) -> bool:
    """Extra check on top of staticwebapp.config.json.

    Azure puts the logged-in user in this header. Outside Azure it's missing,
    so the API refuses to answer.
    """
    header = req.headers.get("x-ms-client-principal")
    if not header:
        return False
    try:
        principal = json.loads(base64.b64decode(header))
    except (ValueError, TypeError):
        return False
    return "admin" in (principal.get("userRoles") or [])


def to_row(entity) -> dict:
    submitted = entity.get("SubmittedAt")
    return {
        "id": entity.get("RowKey"),
        "name": entity.get("Name", ""),
        "email": entity.get("Email", ""),
        "attending": bool(entity.get("Attending", False)),
        "guests": int(entity.get("Guests", 0) or 0),
        "allergies": entity.get("Allergies", ""),
        "message": entity.get("Message", ""),
        "submittedAt": submitted.isoformat() if isinstance(submitted, datetime) else None,
    }


def main(req: func.HttpRequest) -> func.HttpResponse:
    global _table

    if not is_admin(req):
        return respond(401, {"error": "Du må være logget inn som admin."})

    try:
        if req.method == "GET":
            rows = [to_row(e) for e in get_table().query_entities(f"PartitionKey eq '{PARTITION}'")]
            rows.sort(key=lambda r: r["submittedAt"] or "", reverse=True)
            return respond(200, rows)

        if req.method == "DELETE":
            row_id = req.route_params.get("id", "")
            if not ROW_KEY_RE.match(row_id):
                return respond(400, {"error": "Ugyldig ID."})
            try:
                get_table().delete_entity(partition_key=PARTITION, row_key=row_id)
            except ResourceNotFoundError:
                pass  # already gone, which is what we wanted
            return respond(200, {"ok": True})

        return respond(405, {"error": "Metoden er ikke støttet."})

    except KeyError:
        _table = None
        return respond(500, {"error": "Mangler innstilling på serveren.", "code": "MISSING_SETTING"})
    except Exception as exc:
        logging.exception("Admin API failed")
        _table = None
        return respond(500, {"error": "Noe gikk galt på serveren.", "code": type(exc).__name__})
