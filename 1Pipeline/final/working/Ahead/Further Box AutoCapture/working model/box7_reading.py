"""Optional voice navigation over the unchanged Box6 OCR region records."""
import sys
import time
from box7_commands import resolve_selection
from box7_runtime import PlaybackControls, VoiceAction

HELP = ("Say repeat the paragraph, read paragraph two, read from paragraph two, "
        "next paragraph, previous paragraph, read all headings, what is the heading, "
        "what is the title, how many paragraphs, where am I, or continue reading. "
        "Document questions are recorded for the next feature stage.")


def announcement(host, region, regions, image, language):
    location = host.paragraph_page_location(region, image.shape)
    kind = region.get("region_type", "paragraph")
    if language == "urdu":
        locations = {"at the top of the page": "صفحے کے اوپری حصے میں",
            "in the upper part of the page": "صفحے کے بالائی حصے میں",
            "in the middle of the page": "صفحے کے درمیان میں",
            "in the lower part of the page": "صفحے کے نچلے حصے میں",
            "at the bottom of the page": "صفحے کے آخر میں"}
        where = locations.get(location, "صفحے پر")
        if kind == "page_title":
            return f"عنوان، {where}۔"
        if kind == "section_heading":
            return f"سرخی {region.get('heading_number', '')}، {where}۔"
        return f"پیراگراف {region.get('paragraph_number', '?')}، کل {host._region_counts(regions)['paragraphs']} میں سے، {where}۔"
    if kind == "page_title":
        return f"Title, {location}."
    if kind == "section_heading":
        return f"Heading {region.get('heading_number', '')}, {location}."
    return f"Paragraph {region.get('paragraph_number', '?')} of {host._region_counts(regions)['paragraphs']}, {location}."


def segments(text):
    result = []
    while len(text) > 900:
        cut = max(text.rfind(mark, 0, 901) for mark in (". ", "! ", "? ", "۔", "。"))
        if cut < 300:
            cut = text.rfind(" ", 0, 901)
        cut = 900 if cut <= 0 else cut + 1
        result.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        result.append(text)
    return result


def _feedback(host, message, keys, events):
    host._gui_emit("voice", message=message)
    document = host._LAST_DOCUMENT or {}
    tts = document.get("status_tts") or document.get("tts")
    if tts is None:
        app = host._GUI_CONTROLLER
        tts = next(iter(getattr(app, "tts_modules", [])), None)
    if tts is not None:
        app = host._GUI_CONTROLLER
        previous_phase = getattr(app, "_voice_phase", None)
        if previous_phase is not None:
            app._voice_phase = "feedback"
        try:
            return host._speak_segment_with_stop(tts, message, keys, events)
        finally:
            if previous_phase is not None:
                app._voice_phase = previous_phase
    return "finished"


def handle(host, command, keys, events, regions=None, current=None):
    """Return (playlist, preview_mode), or None for information/stop commands."""
    document = host._LAST_DOCUMENT
    if command.action in ("unknown", "cancel", "pause", "stop"):
        return None
    if command.action == "help":
        _feedback(host, HELP, keys, events)
        return None
    if command.action == "question":
        host._gui_emit("voice_question", question=command.text,
                       region_id=(document or {}).get("active_region_id"),
                       status="Captured; document retrieval is Stage 2")
        _feedback(host, "Question captured. Source linked document search will be added in stage two. Say continue reading to return to the page.", keys, events)
        return None
    if not document:
        _feedback(host, "Capture a page before using reading commands.", keys, events)
        return None
    regions = regions or [r for r in document["paragraphs"] if r.get("spoken_text", "").strip()]
    current = document.get("index", 0) if current is None else current
    current = min(max(current, 0), max(len(regions) - 1, 0))
    if command.action == "count":
        counts = host._region_counts(regions)
        _feedback(host, f"This page has {counts['paragraphs']} paragraphs, {counts['headings']} section headings, and {counts['titles']} titles.", keys, events)
        return None
    if command.action == "position":
        if regions:
            _feedback(host, f"Your reading position is {host._region_label(regions[document.get('voice_bookmark', current)])}.", keys, events)
        return None
    if command.action == "resume":
        return list(range(document.get("voice_bookmark", current), len(regions))), False
    if command.action in ("next", "previous"):
        step = 1 if command.action == "next" else -1
        return list(range(min(max(0, current + step), len(regions) - 1), len(regions))), False
    try:
        indices, policy = resolve_selection(command, regions, current)
    except ValueError as exc:
        _feedback(host, str(exc), keys, events)
        return None
    return indices, policy == "preview"


def run(host, tts, paragraphs, image, keys=None, events=None, language="english", start_index=0,
        selection=None, preview=False, resume_after_preview=False):
    readable = [p for p in paragraphs if p.get("spoken_text", "").strip()]
    if not readable:
        return 0.0
    started = time.time()
    indices = list(range(min(max(0, start_index), len(readable) - 1), len(readable))) if selection is None else list(selection)
    controls = PlaybackControls()
    document = host._LAST_DOCUMENT
    saved = document.get("voice_bookmark", document.get("index", start_index)) if document else start_index
    if resume_after_preview:
        saved = min(max(0, start_index), len(readable) - 1)
    return_to = saved if preview and resume_after_preview else None
    host._gui_emit("stage", stage="audio", status="Speaking document")
    try:
        while indices or return_to is not None:
            if not indices:
                # A requested excerpt is a temporary detour, not end-of-page.
                indices = list(range(return_to, len(readable)))
                return_to, preview = None, False
                continue
            index = indices.pop(0)
            region = readable[index]
            if document:
                document["index"] = index
                document["active_region_id"] = region.get("region_id")
                if not preview:
                    saved = index
                    document["voice_bookmark"] = index
            host.show_paragraph_preview(image, readable, active_index=index)
            label = host._region_label(region)
            host._gui_emit("active_region", index=index, label=label, region_id=region.get("region_id"),
                           source_text=region.get("source_text", ""), spoken_text=region["spoken_text"])
            host._safe_debug_call(host._ACTIVE_DEBUG_RECORDER, "record_event", "paragraph_started",
                                 region_id=region.get("region_id"), index=index, label=label)
            text = announcement(host, region, readable, image, language) + " " + region["spoken_text"].strip()
            action = "finished"
            for segment in segments(text):
                action = host._speak_segment_with_stop(tts, segment, keys, events, controls)
                if action != "finished":
                    break
            host._safe_debug_call(host._ACTIVE_DEBUG_RECORDER, "record_event", "paragraph_playback_result",
                                 region_id=region.get("region_id"), index=index, action=str(action))
            if isinstance(action, VoiceAction):
                choice = handle(host, action.command, keys, events, readable, index)
                if choice is None:
                    break
                indices, preview = choice
                if preview:
                    # Nested requests retain the original reading bookmark.
                    if return_to is None:
                        return_to = saved
                else:
                    return_to = None
            elif action in ("stop", "quit", "disconnect"):
                break
            elif action == "repeat":
                indices.insert(0, index)
            elif action in ("next", "previous"):
                target = min(max(0, index + (1 if action == "next" else -1)), len(readable) - 1)
                indices, preview = list(range(target, len(readable))), False
                return_to = None
    finally:
        if document and preview:
            document["index"] = saved
        host.show_paragraph_preview(image, readable, active_index=None)
        host._gui_emit("active_region", index=None, label="", source_text="")
        host._gui_emit("stage", stage="camera", status="Ready; G for voice, S for capture")
    return time.time() - started


def idle_command(host, command, keys, events):
    choice = handle(host, command, keys, events)
    if choice is None:
        return
    document = host._LAST_DOCUMENT
    indices, preview = choice
    run(host, document["tts"], document["paragraphs"], document["image"], keys, events,
        document["output_language"], selection=indices, preview=preview)
