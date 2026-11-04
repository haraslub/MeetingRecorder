"""Notice that the meeting of a running recording has ended, so the user is asked to stop.

Only a meeting seen during the recording can end: a recording started without one never
asks. The user is asked once per loss; the meeting has to come back before it asks again.
"""

GRACE_SECONDS = 30


class CallEndWatch:
    def __init__(self, grace=GRACE_SECONDS):
        self.grace = grace
        self.seen = False
        self.last_seen = None
        self.asked = False

    def observe(self, present, now):
        """'ask' to open the prompt, 'back' to close it again, '' for nothing to do."""
        if present:
            back = self.asked
            self.seen, self.last_seen, self.asked = True, now, False
            return "back" if back else ""
        if self.seen and not self.asked and now - self.last_seen >= self.grace:
            self.asked = True
            return "ask"
        return ""
