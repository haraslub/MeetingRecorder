import numpy as np

from audiorecorder.slides.selector import GRID, SlideSelector, SlideTuning, cells

ROWS, COLUMNS = GRID
STEP = 5.0


def slide(seed, columns=COLUMNS):
    """Content: a light page with text blocks, whose cells have a high pixel spread."""
    means = np.full(GRID, 230.0, dtype=np.float32)
    spread = np.zeros(GRID, dtype=np.float32)
    local = np.random.default_rng(seed)
    for _ in range(16):
        row, column = local.integers(0, ROWS - 2), local.integers(0, columns - 8)
        means[row:row + 2, column:column + 8] = 150.0
        spread[row:row + 2, column:column + 8] = 60.0
    return means, spread


def avatars(tiles=2):
    """A people view with cameras off: dark tiles, an initial and a name in each."""
    means = np.full(GRID, 30.0, dtype=np.float32)
    spread = np.zeros(GRID, dtype=np.float32)
    tile_rows, tile_columns = ROWS // tiles, COLUMNS // tiles
    for r in range(tiles):
        for c in range(tiles):
            top, left = r * tile_rows, c * tile_columns
            means[top + 1:top + tile_rows - 1, left + 1:left + tile_columns - 1] = 60.0
            centre = (top + tile_rows // 2, left + tile_columns // 2)
            means[centre] = 120.0
            spread[centre] = 50.0
            spread[top + tile_rows - 2, left + 2:left + 6] = 30.0
    return means, spread


def cameras(rng, tiles=2):
    """A people view with cameras on: tiles that change in every sample, static gaps."""
    means, spread = avatars(tiles)
    tile_rows, tile_columns = ROWS // tiles, COLUMNS // tiles
    for r in range(tiles):
        for c in range(tiles):
            top, left = r * tile_rows, c * tile_columns
            area = (slice(top + 1, top + tile_rows - 1), slice(left + 1, left + tile_columns - 1))
            means[area] = rng.uniform(40, 200, means[area].shape)
            spread[area] = 30.0
    return means, spread


def beside_camera_strip(sample, rng):
    """The rightmost fifth of the window replaced by camera tiles that change every sample."""
    means, spread = sample[0].copy(), sample[1].copy()
    strip = COLUMNS - COLUMNS // 5
    means[:, strip:] = rng.uniform(0, 255, (ROWS, COLUMNS - strip))
    spread[:, strip:] = 30.0
    return means, spread


def feed(selector, samples, start=0.0):
    return [selector.offer(sample, start + index * STEP) for index, sample in enumerate(samples)]


def kept(times):
    return [t for t in times if t is not None]


def test_a_camera_grid_keeps_one_snapshot_once_the_cameras_count_as_moving():
    rng = np.random.default_rng(1)
    times = feed(SlideSelector(), [cameras(rng) for _ in range(12)])
    assert len(kept(times)) == 1


def test_a_camera_off_avatar_grid_keeps_one_snapshot():
    times = feed(SlideSelector(), [avatars()] * 12)
    assert times[:2] == [None, 0.0]
    assert len(kept(times)) == 1


def test_a_participant_joining_adds_one_people_snapshot():
    times = feed(SlideSelector(), [avatars(2)] * 4 + [avatars(3)] * 4)
    assert kept(times) == [0.0, 20.0]


def test_each_slide_beside_a_camera_strip_is_kept_once():
    rng = np.random.default_rng(2)
    columns = COLUMNS - COLUMNS // 5
    first, second = slide(1, columns), slide(2, columns)
    samples = ([beside_camera_strip(first, rng) for _ in range(5)]
               + [beside_camera_strip(second, rng) for _ in range(5)]
               + [beside_camera_strip(first, rng) for _ in range(5)])
    assert len(kept(feed(SlideSelector(), samples))) == 2


def test_a_new_people_phase_after_slides_gets_its_own_first_snapshot():
    samples = [avatars()] * 3 + [slide(1)] * 3 + [slide(2)] * 3 + [avatars()] * 3
    assert len(kept(feed(SlideSelector(), samples))) == 4


def test_a_black_window_is_never_kept():
    black = (np.zeros(GRID, dtype=np.float32), np.zeros(GRID, dtype=np.float32))
    assert feed(SlideSelector(), [black] * 4) == [None] * 4


def test_a_slide_still_changing_is_kept_only_after_it_settles():
    times = feed(SlideSelector(), [slide(1), slide(2), slide(3), slide(3)])
    assert times == [None, None, None, 10.0]


def test_cells_of_a_4k_frame_match_its_block_means_and_flat_blocks_have_no_spread():
    rng = np.random.default_rng(7)
    blocks = rng.integers(0, 256, (ROWS, COLUMNS, 3)).astype(np.uint8)
    frame = np.repeat(np.repeat(blocks, 80, axis=0), 80, axis=1)   # 2160 x 3840
    expected = blocks[..., 0] * 0.114 + blocks[..., 1] * 0.587 + blocks[..., 2] * 0.299
    means, spread = cells(frame)
    assert means.shape == GRID and spread.shape == GRID
    assert np.abs(means - expected).max() < 1.0
    assert spread.max() < 1.0


def test_text_raises_the_spread_of_its_cells():
    frame = np.full((540, 960, 3), 230, dtype=np.uint8)
    frame[100:140:4, 100:400] = 20                   # thin text lines
    means, spread = cells(frame)
    assert spread[5, 7] > 12.0
    assert spread[20, 40] == 0.0


def test_cells_accept_a_frame_that_is_not_a_multiple_of_the_grid():
    assert cells(np.zeros((1416, 2576, 3), dtype=np.uint8))[0].shape == GRID


def typed(sample, cells_typed):
    """The slide with text typed into its first empty cells of row 25, as in a shared chart."""
    means, spread = sample[0].copy(), sample[1].copy()
    means[25, 1:1 + cells_typed] = 90.0
    spread[25, 1:1 + cells_typed] = 50.0
    return means, spread


def typing_session(tuning):
    base = slide(1)
    samples = [base] * 3 + [typed(base, n) for n in range(1, 5)] + [typed(base, 4)] * 4
    return kept(feed(SlideSelector(tuning), samples))


def test_a_few_typed_words_stay_below_the_default_new_content_threshold():
    assert len(typing_session(SlideTuning())) == 1


def test_a_lower_new_content_threshold_keeps_the_typed_text():
    assert len(typing_session(SlideTuning(new_content_percent=0.2))) == 2


def test_tuning_from_settings_keeps_valid_values_and_defaults_the_rest():
    tuning = SlideTuning.from_config({
        "sample_seconds": 10, "new_content_percent": 0.5, "settle_percent": -1,
        "changed_level": "6", "unknown": 3})
    assert tuning.sample_seconds == 10.0
    assert tuning.new_content_percent == 0.5
    assert tuning.settle_percent == SlideTuning().settle_percent
    assert tuning.changed_level == SlideTuning().changed_level
    assert SlideTuning.from_config(None) == SlideTuning()
    assert SlideTuning.from_config(tuning.to_config()) == tuning
