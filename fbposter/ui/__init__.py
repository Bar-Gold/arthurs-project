"""CustomTkinter user interface.

The UI runs entirely on the main thread. Anything that blocks -- which means
anything touching Playwright -- goes through fbposter.ui.background so the
window never freezes and never has to be raised to report a result.
"""
