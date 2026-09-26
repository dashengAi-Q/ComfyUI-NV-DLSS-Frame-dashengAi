"""Regression coverage for the SDR output contract, independent of NR."""

import unittest

import numpy as np

from dlss_engine.core.composition import compose_sdr, validate_detail_strength
from dlss_engine.video.models import ConversionOptions
from dlss_engine.video.processor import convert_video


class CompositionTests(unittest.TestCase):
    def frame(self, rgb, alpha=173):
        return np.full((4, 6, 4), (*rgb, alpha), dtype=np.uint8)

    def test_default_is_bit_exact_and_does_not_copy(self):
        rendered = self.frame((12, 230, 78))
        self.assertIs(compose_sdr(self.frame((80, 80, 80)), rendered), rendered)

    def test_unchanged_pixels_and_alpha_are_preserved(self):
        source = self.frame((19, 117, 205))
        result = compose_sdr(source, source, 2)
        np.testing.assert_array_equal(result, source)

    def test_brightening_and_darkening_are_amplified(self):
        source = self.frame((100, 100, 100))
        brighter = compose_sdr(source, self.frame((120, 120, 120)), 2)
        darker = compose_sdr(source, self.frame((80, 80, 80)), 2)
        self.assertTrue((brighter[..., :3] > 120).all())
        self.assertTrue((darker[..., :3] < 80).all())
        self.assertTrue((brighter[..., 3] == 173).all())

    def test_extreme_change_is_bounded(self):
        result = compose_sdr(self.frame((50, 50, 50)), self.frame((200, 200, 200)), 2)
        self.assertLessEqual(int(result[0, 0, 0]), 102)
        self.assertGreaterEqual(int(result[0, 0, 0]), 99)

    def test_black_model_falls_back_to_source(self):
        source = self.frame((80, 100, 120))
        np.testing.assert_array_equal(compose_sdr(source, self.frame((0, 0, 0)), 2), source)

    def test_resize_and_row_tiles(self):
        source = self.frame((100, 100, 100))
        rendered = np.full((133, 17, 4), (120, 120, 120, 80), dtype=np.uint8)
        result = compose_sdr(source, rendered, 2)
        self.assertEqual(result.shape, rendered.shape)
        np.testing.assert_array_equal(result[0], result[-1])
        self.assertTrue((result[..., 3] == 80).all())

    def test_rejects_nonfinite_and_out_of_range_controls(self):
        for value in (float('nan'), float('inf'), 0, 2.1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_detail_strength(value)

    def test_hdr_rejected_before_starting_runtime(self):
        with self.assertRaisesRegex(ValueError, 'SDR-only'):
            convert_video('unused.mp4', ConversionOptions(
                preserve_hdr=True, output_detail_strength=2,
            ), output_directory='unused', jobs_directory='unused',
                logs_directory='unused')


if __name__ == '__main__':
    unittest.main()
