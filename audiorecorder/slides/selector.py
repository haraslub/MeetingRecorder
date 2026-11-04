"""Decide which samples of the meeting window to keep, from a coarse grid of gray cells.

A sample is either content (slides, a shared screen) or a people view (camera tiles or
avatars). Content is kept whenever it is new. A people view is kept when a people phase
starts and again only when the people change, so a long talk adds one snapshot, not one
per sample.
"""

from dataclasses import asdict, dataclass, fields

import numpy as np

GRID = (27, 48)
# Pixels sampled per cell side; enough to see a changed line of text, few enough that a
# 4K frame costs about 250 KB of copying instead of 33 MB.
SAMPLES_PER_CELL = 8
# A camera tile changes in every sample, so two changes in a row mark it volatile.
ACTIVITY_WEIGHT = 0.35
VOLATILE_ACTIVITY = 0.5
MIN_CONTRAST = 2.0
DETAILED_CELL = 12.0


@dataclass(frozen=True)
class SlideTuning:
    """The values that decide when a sample is taken and kept; the user can change them."""

    sample_seconds: float = 5.0
    # Gray levels a cell mean has to move to count as changed.
    changed_level: float = 6.0
    # Percent of the stable cells that may change between two samples of a settled view.
    settle_percent: float = 2.0
    # Percent of the stable cells that must differ from every kept slide.
    new_content_percent: float = 4.0
    # Percent of the window covered by detailed stable cells to count as content.
    content_area_percent: float = 12.0
    # Percent of the cells whose layout must change for a new people view.
    new_people_percent: float = 15.0

    @classmethod
    def from_config(cls, stored):
        """Tuning from settings; a missing or out-of-range value takes its default."""
        stored = stored if isinstance(stored, dict) else {}
        values = {}
        for field in fields(cls):
            low, high = RANGES[field.name]
            value = stored.get(field.name)
            if type(value) in (int, float) and low <= value <= high:
                values[field.name] = float(value)
        return cls(**values)

    def to_config(self):
        return asdict(self)


RANGES = {
    "sample_seconds": (1.0, 60.0),
    "changed_level": (1.0, 50.0),
    "settle_percent": (0.1, 50.0),
    "new_content_percent": (0.1, 50.0),
    "content_area_percent": (1.0, 90.0),
    "new_people_percent": (1.0, 90.0),
}


def _centres(count, length):
    return ((np.arange(count) + 0.5) * length / count).astype(int)


def cells(frame):
    """Gray means and spreads of the GRID cells of a BGR frame, from a sparse pixel sample."""
    rows, columns = GRID
    height, width = frame.shape[:2]
    ys = _centres(rows * SAMPLES_PER_CELL, height)
    xs = _centres(columns * SAMPLES_PER_CELL, width)
    sample = frame[np.ix_(ys, xs)].astype(np.float32)
    gray = sample[..., 0] * 0.114 + sample[..., 1] * 0.587 + sample[..., 2] * 0.299
    blocks = gray.reshape(rows, SAMPLES_PER_CELL, columns, SAMPLES_PER_CELL)
    return blocks.mean(axis=(1, 3)), blocks.std(axis=(1, 3))


def _share(mask, within):
    return float(mask[within].mean()) if within.any() else 0.0


class SlideSelector:
    """Keeps settled samples: new content always, people views once per phase or change.

    Cells that keep changing (camera tiles, video, a ticking clock) count as volatile and
    are left out of every comparison, so a slide beside a live camera strip still settles.
    """

    def __init__(self, tuning=None):
        # Replaced as a whole from the UI thread while the worker runs; offer reads it once.
        self.tuning = tuning or SlideTuning()
        self.previous = None
        self.previous_time = None
        self.activity = np.zeros(GRID, dtype=np.float32)
        self.kept_content = []
        # (volatile cells, means) of the last kept people view; None starts a new phase.
        self.people = None

    def offer(self, frame_cells, at):
        """The time to stamp a kept snapshot with, or None when this sample is not kept."""
        tuning = self.tuning
        means, spread = frame_cells
        if self.previous is None:
            self.previous, self.previous_time = means, at
            return None
        changed = np.abs(means - self.previous) > tuning.changed_level
        self.activity = (1 - ACTIVITY_WEIGHT) * self.activity + ACTIVITY_WEIGHT * changed
        stable = self.activity < VOLATILE_ACTIVITY
        first_seen = self.previous_time
        self.previous, self.previous_time = means, at
        if _share(changed, stable) > tuning.settle_percent / 100:
            return None
        if means.std() < MIN_CONTRAST:
            return None
        if (stable & (spread > DETAILED_CELL)).mean() >= tuning.content_area_percent / 100:
            return self._content(means, stable, first_seen, tuning)
        return self._people(means, stable, first_seen, tuning)

    def _content(self, means, stable, first_seen, tuning):
        for old in self.kept_content:
            differs = np.abs(means - old) > tuning.changed_level
            if _share(differs, stable) < tuning.new_content_percent / 100:
                return None
        self.kept_content.append(means)
        self.people = None
        return first_seen

    def _people(self, means, stable, first_seen, tuning):
        volatile = ~stable
        if self.people is not None:
            old_volatile, old_means = self.people
            moved = (volatile != old_volatile) | (
                stable & ~old_volatile & (np.abs(means - old_means) > tuning.changed_level))
            if moved.mean() < tuning.new_people_percent / 100:
                return None
        self.people = (volatile, means)
        return first_seen
