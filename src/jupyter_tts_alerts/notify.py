"""Desktop notifications, as a complement to speech.

Speech tells you a cell finished while you are making coffee; a notification
tells you *which* one when you get back to the screen. Off by default;
``configure(notify=True)`` turns it on.

As with the speech engines, message text is never interpolated into a script:
it travels in environment variables or as a distinct argv element.
"""

from __future__ import annotations

import platform
import shutil
from typing import List, Optional, Sequence, Type

from .engines import Command, encode_powershell
from .text import sanitise

TITLE = "Jupyter"
#: Notification popups show a line or two; anything longer is cut off anyway.
MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 200


class Notifier:
    """Base class. One subclass per desktop notification system."""

    name: str = ""
    executables: Sequence[str] = ()

    def __init__(self, path: str) -> None:
        self.path = path

    @classmethod
    def find(cls) -> Optional["Notifier"]:
        """Locate the executable without running it."""
        for exe in cls.executables:
            path = shutil.which(exe)
            if path:
                return cls(path)
        return None

    def notify_command(self, title: str, body: str, urgent: bool) -> Command:
        """The `Command` that shows *title* and *body*, keeping the text out of any script."""
        raise NotImplementedError


class NotifySend(Notifier):
    """Linux, via libnotify."""

    name = "notify-send"
    executables = ("notify-send",)

    def notify_command(self, title: str, body: str, urgent: bool) -> Command:
        return Command([
            self.path,
            "-a", TITLE,
            "-u", "critical" if urgent else "normal",
            "-i", "dialog-error" if urgent else "dialog-information",
            "--", sanitise(title, MAX_TITLE_CHARS), sanitise(body, MAX_BODY_CHARS),
        ], warn_on_failure=True)


class OsascriptNotifier(Notifier):
    """macOS Notification Centre. Text is read from the environment so that
    quotes and apostrophes in an exception message cannot break the script."""

    name = "osascript"
    executables = ("osascript",)

    _SCRIPT = (
        'display notification (system attribute "JTTS_BODY") '
        'with title (system attribute "JTTS_TITLE")'
    )

    def notify_command(self, title: str, body: str, urgent: bool) -> Command:
        return Command(
            [self.path, "-e", self._SCRIPT],
            env={
                "JTTS_TITLE": sanitise(title, MAX_TITLE_CHARS),
                "JTTS_BODY": sanitise(body, MAX_BODY_CHARS),
            },
            warn_on_failure=True,
        )


class ToastNotifier(Notifier):
    """Windows toast, via the WinRT projection in Windows PowerShell.

    Two things here are less obvious than they look.

    The notifier is created under Windows PowerShell's own AppUserModelID
    rather than our name. Windows will not show a toast for an unregistered
    AUMID (``Show()`` throws ``Element not found``), and registering one
    means either a Start-menu shortcut or writing to the user's registry,
    neither of which a ``pip install`` should be doing. Borrowing the ID that
    is already registered is what the established PowerShell toast libraries
    do; the cost is that the toast is attributed to Windows PowerShell.

    And only ``powershell`` is listed, not ``pwsh``: PowerShell 7 cannot load
    WinRT types through ``ContentType=WindowsRuntime`` at all, so offering it
    as a fallback would only produce a silent failure.
    """

    name = "toast"
    executables = ("powershell",)

    #: Windows PowerShell's registered AppUserModelID.
    AUMID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

    _SCRIPT = (
        "$ErrorActionPreference='Stop';"
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,"
        "ContentType=WindowsRuntime]|Out-Null;"
        "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$t=$x.GetElementsByTagName('text');"
        "$t.Item(0).AppendChild($x.CreateTextNode($env:JTTS_TITLE))|Out-Null;"
        "$t.Item(1).AppendChild($x.CreateTextNode($env:JTTS_BODY))|Out-Null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "$env:JTTS_AUMID).Show("
        "[Windows.UI.Notifications.ToastNotification]::new($x))"
    )

    def notify_command(self, title: str, body: str, urgent: bool) -> Command:
        return Command(
            [self.path, *encode_powershell(self._SCRIPT)],
            env={
                "JTTS_TITLE": sanitise(title, MAX_TITLE_CHARS),
                "JTTS_BODY": sanitise(body, MAX_BODY_CHARS),
                "JTTS_AUMID": self.AUMID,
            },
            # No catch{} swallowing this. A toast that never appears, with no
            # word said about why, is worse than no toast feature at all.
            warn_on_failure=True,
        )


def preference_order() -> List[Type[Notifier]]:
    """Which notifiers to try, best first, for the platform we are on."""
    system = platform.system()
    if system == "Darwin":
        return [OsascriptNotifier]
    if system == "Windows":
        return [ToastNotifier]
    return [NotifySend]


def detect_notifier() -> Optional[Notifier]:
    """The desktop notifier for this platform, or ``None`` if there isn't one."""
    for notifier_cls in preference_order():
        notifier = notifier_cls.find()
        if notifier is not None:
            return notifier
    return None
