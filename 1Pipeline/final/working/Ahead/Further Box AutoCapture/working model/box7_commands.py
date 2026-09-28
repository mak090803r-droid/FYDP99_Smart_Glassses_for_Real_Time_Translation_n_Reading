"""Deterministic English reading commands; no model imports or side effects."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceCommand:
    action: str
    number: int | None = None
    text: str = ""


NUMBERS = {word: index for index, word in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
NUMBERS.update(dict(first=1, second=2, third=3, fourth=4, fifth=5, sixth=6,
                    seventh=7, eighth=8, ninth=9, tenth=10))


def normalize(text):
    text = str(text).lower().replace("’", "'")
    text = re.sub(r"\bwhat's\b", "what is", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(?:please |can you |could you )+", "", text)
    return re.sub(r" please$", "", text).strip()


def parse_command(text):
    """Use whole-utterance matches. Unsupported control-like speech is rejected.

    Arbitrary document questions are captured for Stage 2, never executed as
    commands, and never described as having been answered by this parser.
    """
    value = normalize(text)
    aliases = {
        "repeat": "repeat_current", "repeat paragraph": "repeat_current",
        "repeat the paragraph": "repeat_current", "repeat this paragraph": "repeat_current",
        "repeat the current paragraph": "repeat_current",
        "read that again": "repeat_current", "repeat that": "repeat_current",
        "next": "next", "next paragraph": "next", "skip this paragraph": "next",
        "skip the paragraph": "next", "previous paragraph": "previous", "go back": "previous",
        "pause": "pause", "pause reading": "pause", "resume": "resume",
        "resume reading": "resume", "continue": "resume", "continue reading": "resume",
        "continue from where i stopped": "resume", "stop": "stop", "stop reading": "stop",
        "read all": "read_all", "read the whole page": "read_all", "read from the beginning": "read_all",
        "what is the heading": "heading", "read the heading": "heading",
        "repeat the heading": "heading", "what is the title": "title", "read the title": "title",
        "read headings": "headings", "read all headings": "headings",
        "what are the headings": "headings", "what are the headings on this page": "headings",
        "repeat headings": "headings", "repeat the headings": "headings", "repeat all headings": "headings",
        "how many paragraphs": "count", "how many paragraphs are there": "count",
        "where am i": "position", "which paragraph is this": "position",
        "which paragraph am i on": "position",
        "voice help": "help", "what can i say": "help", "help": "help",
        "cancel": "cancel", "cancel voice command": "cancel",
    }
    if value in aliases:
        return VoiceCommand(aliases[value], text=text)
    match = re.fullmatch(r"(read from|read|repeat|go to|skip) (?:the )?paragraph (?:number )?(\w+)", value)
    if match:
        verb, token = match.groups()
        number = int(token) if token.isdecimal() else NUMBERS.get(token)
        if number is not None and 1 <= number <= 999:
            action = {"read from": "read_from", "read": "read_number", "repeat": "read_number",
                      "go to": "read_from", "skip": "skip_number"}[verb]
            return VoiceCommand(action, number, text)
        return VoiceCommand("unknown", text=text)
    match = re.fullmatch(r"(?:read|repeat) (?:the )?(\w+) paragraph", value)
    if match and match[1] in NUMBERS and NUMBERS[match[1]] > 0:
        return VoiceCommand("read_number", NUMBERS[match[1]], text)
    if re.match(r"^(what|which|where|when|why|how|who|does|do|is|are|can|could|find|tell me|explain)\b", value):
        return VoiceCommand("question", text=str(text).strip())
    return VoiceCommand("unknown", text=text)


def resolve_selection(command, regions, current=0):
    """Return region indices and policy, or raise an actionable ValueError.

    Spoken paragraph numbers refer to paragraph_number, never the list index:
    page titles and section headings do not consume paragraph numbers.
    """
    if not regions:
        raise ValueError("Capture a page before using reading commands.")
    current = min(max(0, current), len(regions) - 1)
    action = command.action
    if action in ("read_number", "read_from", "skip_number"):
        index = next((i for i, r in enumerate(regions)
                      if r.get("region_type", "paragraph") == "paragraph"
                      and r.get("paragraph_number") == command.number), None)
        if index is None:
            raise ValueError(f"Paragraph {command.number} is not present on this page.")
        if action == "skip_number":
            # Explicit navigation: skip through this paragraph, then continue.
            return list(range(index + 1, len(regions))), "continue"
        return ([index], "preview") if action == "read_number" else (list(range(index, len(regions))), "continue")
    if action == "repeat_current":
        return [current], "preview"
    if action == "read_all":
        return list(range(len(regions))), "continue"
    if action in ("headings", "heading", "title"):
        headings = [i for i, r in enumerate(regions) if r.get("region_type") in ("page_title", "section_heading")]
        if action == "title":
            headings = [i for i in headings if regions[i].get("region_type") == "page_title"]
        elif action == "heading":
            preceding = [i for i in headings if i <= current]
            headings = [preceding[-1]] if preceding else headings[:1]
        if not headings:
            raise ValueError("No heading was detected on this page.")
        return headings, "preview"
    raise ValueError("This command does not select document regions.")


class CaptureHold:
    """Debounced press/hold/release semantics shared by Tk and Pi GPIO.

    Disabled: capture on press (legacy). Enabled: short capture on release;
    one PTT begin after threshold; release ends PTT without a capture event.
    """
    def __init__(self, emit, threshold=0.7):
        self.emit, self.threshold = emit, threshold
        self.down = False
        self.started = 0.0
        self.voice_at_press = False
        self.long = False

    def press(self, now, voice_enabled):
        if self.down:
            return
        self.down, self.started = True, now
        self.voice_at_press, self.long = bool(voice_enabled), False
        if not self.voice_at_press:
            self.emit("capture")

    def tick(self, now):
        if self.down and self.voice_at_press and not self.long and now - self.started >= self.threshold:
            self.long = True
            self.emit("ptt_start")

    def release(self, now):
        if not self.down:
            return
        self.tick(now)
        self.down = False
        if self.voice_at_press:
            self.emit("ptt_end" if self.long else "capture")

    def cancel(self):
        if self.down and self.long:
            self.emit("ptt_cancel")
        self.down = self.long = False
