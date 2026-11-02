import numpy as np

from audiorecorder.audio.backend import rms_level


def _tone(dbfs):
    amplitude = 10 ** (dbfs / 20) * np.sqrt(2)
    return (amplitude * np.sin(np.linspace(0, 200 * np.pi, 4800))).astype(np.float32)


def test_silence_is_zero():
    assert rms_level(np.zeros(480, dtype=np.float32)) == 0.0


def test_speech_levels_stay_apart_and_below_full():
    quiet, normal, loud = rms_level(_tone(-35)), rms_level(_tone(-22)), rms_level(_tone(-12))
    assert 0.0 < quiet < normal < loud < 1.0
    assert loud - quiet > 0.4


def test_clipping_signal_is_full():
    assert rms_level(np.ones(480, dtype=np.float32)) == 1.0


def _bars_after(levels):
    from audiorecorder.ui.level_bars import BarWidget

    bars = BarWidget()
    bars.set_active(True)
    for level in levels:
        bars.set_level(level)
        bars.advance()
    return list(bars._levels)


def test_steady_loud_audio_is_not_full_bars():
    shown = _bars_after([0.9, 0.92, 0.88, 0.91] * 20)
    assert max(shown[-30:]) - min(shown[-30:]) > 0.15
    assert sum(shown[-30:]) / 30 < 0.8


def test_quiet_noise_stays_low_and_silence_is_flat():
    assert max(_bars_after([0.12, 0.14, 0.13] * 20)[-30:]) < 0.4
    assert _bars_after([0.9] * 40 + [0.0] * 40)[-1] < 0.01
