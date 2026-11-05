import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))

import config  # noqa: E402


class TestTelegramToken(unittest.TestCase):
    def test_placeholder_token_counts_as_unset(self):
        self.assertEqual(config._clean_telegram_token("123456789:ABCdefGHIjklMNOpqrsTUVwxyz"), "")
        self.assertEqual(config._clean_telegram_token("  123456789:ABCdefGHIjklMNOpqrsTUVwxyz  "), "")

    def test_empty_and_none(self):
        self.assertEqual(config._clean_telegram_token(""), "")
        self.assertEqual(config._clean_telegram_token(None), "")

    def test_real_looking_token_is_kept(self):
        tok = "987654321:AAH_realLookingTokenValue1234567890abcd"
        self.assertEqual(config._clean_telegram_token(f" {tok} "), tok)

    def test_agent_id_never_empty(self):
        self.assertTrue(config.AGENT_ID)


if __name__ == "__main__":
    unittest.main()
