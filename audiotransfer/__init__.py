"""MP3 melody extraction to JSON, CSV, and Arduino sketches."""

from .events import NoteEvent, NoteFrame, merge_note_frames, scale_note_events
from .notes import quantize_frequency

__all__ = [
    "NoteEvent",
    "NoteFrame",
    "merge_note_frames",
    "scale_note_events",
    "quantize_frequency",
]

__version__ = "0.3.2"
