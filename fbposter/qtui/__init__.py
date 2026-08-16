"""The Qt user interface.

Built alongside the CustomTkinter one rather than replacing it in place, so the
working app kept running while this was written.

The reason it exists is Hebrew. Tk 8.6 has no bidirectional text support: it
lays characters out in logical order and leaves reordering to Windows, which
only manages it one run of one script at a time, so any line mixing Hebrew with
English or digits came out mirrored. Qt shapes text itself and gets it right
with no marks, no tags and no reordering -- verified against the same sentence
that exposed the bug.

Only the view layer changes. The database, the guards, the clock, the posting
worker and the automation engine are untouched and shared.
"""
