"""Tool schemas for the search agent. Strict, so every call arrives schema-valid."""

from __future__ import annotations

TOOLS: list[dict] = [
    {
        "name": "scan_here",
        "description": (
            "Turn the aircraft on the spot with the camera tilted down at search angle, "
            "running person detection and photo matching on every frame. Returns every "
            "candidate known so far with its match score, band (strong / ambiguous / weak), "
            "distance and bearing.\n"
            "Input: degrees to turn, 90 to 360. Example: {\"degrees\": 360} right after "
            "takeoff, or {\"degrees\": 180} on arriving somewhere you have half seen.\n"
            "Edge cases: people beyond about 35 m are too small to detect; a person half "
            "behind a tree may score low. A scan finds nobody is a normal result, not an error.\n"
            "Boundary: use scan_here to look around from where the aircraft already is. Use "
            "fly_to to go somewhere new, and look_closer to examine one specific candidate."),
        "input_schema": {
            "type": "object",
            "properties": {"degrees": {"type": "integer", "description": "90 to 360"}},
            "required": ["degrees"], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "fly_to",
        "description": (
            "Fly to a point inside the search area at search altitude, camera forward, "
            "detecting and matching all the way. Coordinates are metres east and north of "
            "the takeoff point, the same frame the briefing gives the search area in.\n"
            "Example: {\"east_m\": 12, \"north_m\": 25} to search the far side of the area.\n"
            "Edge cases: a point outside the search area is refused (validation error, not "
            "retryable with the same input); the aircraft may stop short if the leg times out.\n"
            "Boundary: fly_to is for covering ground. To get a better view of one candidate "
            "you already have, use look_closer, which picks the viewpoint for you."),
        "input_schema": {
            "type": "object",
            "properties": {"east_m": {"type": "number"}, "north_m": {"type": "number"}},
            "required": ["east_m", "north_m"], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "look_closer",
        "description": (
            "Active perception on one candidate: move to about 7 m from their estimated "
            "position, face them, tilt the camera onto them and keep looking for several "
            "seconds, so the match is re-scored from a larger, clearer view. Returns the match "
            "before and after, with the candidate's new crop.\n"
            "Example: {\"candidate_id\": \"P3\", \"reason\": \"ambiguous at 28 m, box only 30 px tall\"}.\n"
            "Edge cases: at most two looks per candidate; a candidate with no ground position "
            "yet must be scanned toward first; a viewpoint outside the search area is not used.\n"
            "Boundary: look_closer improves the EVIDENCE. It does not decide anything. Use "
            "ask_coordinator when the evidence is good enough for a person to judge."),
        "input_schema": {
            "type": "object",
            "properties": {"candidate_id": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["candidate_id", "reason"], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "ask_coordinator",
        "description": (
            "Show the search coordinator the candidate's best crop beside the reference "
            "photo, with your message, and wait for their answer: confirmed, rejected or "
            "no_answer. The aircraft holds position and keeps watching while they decide.\n"
            "Example: {\"candidate_id\": \"P3\", \"message\": \"Blue t-shirt, dark trousers, "
            "standing alone near the path. Match 0.91 after a closer look.\"}\n"
            "Edge cases: weak candidates (match below the ambiguous band) are refused, because "
            "asking a person to judge noise wastes the minutes that matter; a rejected "
            "candidate cannot be asked about again; no_answer after the timeout is not a no.\n"
            "Boundary: this is the ONLY way a candidate becomes confirmed. Nothing else, "
            "including a very high match score, confirms anyone."),
        "input_schema": {
            "type": "object",
            "properties": {"candidate_id": {"type": "string"}, "message": {"type": "string"}},
            "required": ["candidate_id", "message"], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "follow_confirmed",
        "description": (
            "Stay with a CONFIRMED person: keep them centred and at the size they appeared "
            "when the follow began, tilting the camera to keep them in shot, and put their "
            "position on the coordinator's map once a second. Returns how long the lock held "
            "and how far the reported positions were from the phone's own GPS, when available.\n"
            "Example: {\"candidate_id\": \"P3\", \"seconds\": 90}.\n"
            "Edge cases: refused unless the coordinator confirmed this candidate (permission "
            "error, not retryable); losing them briefly makes the aircraft turn toward where "
            "they were last seen; the fence and ceiling still apply.\n"
            "Boundary: only for a confirmed person. To get a better look at someone not yet "
            "confirmed, use look_closer."),
        "input_schema": {
            "type": "object",
            "properties": {"candidate_id": {"type": "string"},
                           "seconds": {"type": "integer", "description": "10 to 180"}},
            "required": ["candidate_id", "seconds"], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "end_mission",
        "description": (
            "Finish: fly back over the takeoff point and hand control back for landing, "
            "recording the outcome and a one-paragraph summary for the coordinator.\n"
            "Example: {\"outcome\": \"found_and_followed\", \"summary\": \"P3 confirmed at "
            "12:41, followed for 90 s heading north-east along the path.\"}\n"
            "Edge cases: call it as soon as the job is done or cannot be done; the battery "
            "limit will end the mission anyway, but without your summary.\n"
            "Boundary: the only way to finish. Saying you are done in text does not land "
            "the aircraft."),
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {"type": "string",
                            "enum": ["found_and_followed", "found_not_followed", "not_found", "aborted"]},
                "summary": {"type": "string"}},
            "required": ["outcome", "summary"], "additionalProperties": False},
        "strict": True,
    },
]
