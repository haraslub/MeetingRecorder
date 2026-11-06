"""Whether a call runs, from who holds the microphone and whether a meeting is on screen.

A meeting app holding the microphone is a call. A browser holding it is a call only when a
meeting window is on screen as the call begins; after that the browser's hold alone keeps
the call, because the meeting tab need not stay the active tab of its window. The call ends
when this evidence has been gone for the grace time. Without microphone records (macOS) the
meeting window alone is the evidence, and it is not exact enough to act without asking.
"""

from audiorecorder.meetings.call_end import GRACE_SECONDS


class CallWatch:
    def __init__(self, grace=GRACE_SECONDS):
        self.grace = grace
        self.last_present = None
        # A recording already ran in this call, or automation started one: never start
        # again in the same call, so a manual stop or a failed start stays respected.
        self.handled = False

    def observe(self, microphone, meeting_shown, recording, now):
        """Whether the call is present now; microphone is a MicrophoneUse or None."""
        ongoing = self.last_present is not None and now - self.last_present < self.grace
        if microphone is None:
            present = meeting_shown
        else:
            present = microphone.meeting_app or (
                microphone.browser and (meeting_shown or ongoing))
        if present:
            self.last_present = now
            self.handled = self.handled or recording
        elif not ongoing:
            self.last_present = None
            self.handled = False
        return present

    def should_start(self, present, exact):
        """Start a recording now: a call with exact evidence that no recording handled yet."""
        return present and exact and not self.handled

    def started(self):
        self.handled = True
