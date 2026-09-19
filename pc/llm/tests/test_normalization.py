from __future__ import annotations

import unittest

from pc.llm.normalization import normalize_command_text


class CommandNormalizationTests(unittest.TestCase):
    def test_specific_music_verb_is_expanded(self) -> None:
        self.assertEqual(normalize_command_text("幫我放韓文歌"), "幫我播放韓文歌")

    def test_generic_music_gets_a_safe_playlist_subject(self) -> None:
        self.assertEqual(normalize_command_text("放歌"), "播放熱門歌曲")

    def test_short_hour_only_expands_in_timer_context(self) -> None:
        self.assertEqual(normalize_command_text("計時兩小"), "計時兩小時")
        self.assertEqual(normalize_command_text("兩小孩"), "兩小孩")

    def test_cooking_omissions_are_expanded(self) -> None:
        self.assertEqual(normalize_command_text("雞胸肉怎煮"), "雞胸肉怎麼煮")
        self.assertEqual(normalize_command_text("這個怎做"), "這個怎麼做")


if __name__ == "__main__":
    unittest.main()
