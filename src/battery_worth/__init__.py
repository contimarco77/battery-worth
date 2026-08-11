"""battery-worth — retrospective what-if analysis for home batteries.

Project identity lives here, at the root of the package, because it is printed
into artifacts that leave the machine: the summary card that gets posted to a
forum, and the Markdown report that gets pasted into a thread. Those two are the
project's only return channel — a reader who wants the tool has nothing but the
URL on the picture — so the string is defined once and imported, never retyped.
A card and a report pointing at two different repositories, or at a dead link,
would make the artifact worthless as a way back here.
"""

from __future__ import annotations

PROJECT_NAME = "battery-worth"
REPO_URL = "https://github.com/contimarco77/battery-worth"

# The bare host+path form, for places with no room for a scheme (the card footer,
# where it sits beside the project name at 13pt). Derived rather than written out
# again: two literals would be two things to update.
REPO_DISPLAY_URL = REPO_URL.removeprefix("https://")

__all__ = ["PROJECT_NAME", "REPO_DISPLAY_URL", "REPO_URL"]
