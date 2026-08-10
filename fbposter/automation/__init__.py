"""Driving the Facebook composer.

Nothing in here logs in, and nothing clicks through a checkpoint, a CAPTCHA or
a "posting too fast" warning. Any of those halts the batch and hands control
back to the user.
"""

from .detect import PageVerdict, classify
from .humanize import HumanProfile, Humanizer
from .poster import GroupPoster, PostOutcome, PostRequest

__all__ = [
    "GroupPoster",
    "HumanProfile",
    "Humanizer",
    "PageVerdict",
    "PostOutcome",
    "PostRequest",
    "classify",
]
