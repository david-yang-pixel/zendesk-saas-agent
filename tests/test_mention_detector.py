import unittest

from zd_agent.mention.detector import is_mention


class TestIsMention(unittest.TestCase):
    def test_detects_mention_in_internal_note(self) -> None:
        comment = {"is_public": False, "body": "Hey @support-agent can you look at this?"}
        self.assertTrue(is_mention(comment, "@support-agent"))

    def test_ignores_public_comment(self) -> None:
        comment = {"is_public": True, "body": "Hey @support-agent can you look at this?"}
        self.assertFalse(is_mention(comment, "@support-agent"))

    def test_case_insensitive(self) -> None:
        comment = {"is_public": False, "body": "Hey @Support-Agent please help"}
        self.assertTrue(is_mention(comment, "@support-agent"))

    def test_no_mention(self) -> None:
        comment = {"is_public": False, "body": "This is a regular internal note"}
        self.assertFalse(is_mention(comment, "@support-agent"))

    def test_missing_is_public_treated_as_public(self) -> None:
        comment = {"body": "Hey @support-agent"}
        self.assertFalse(is_mention(comment, "@support-agent"))

    def test_empty_body(self) -> None:
        comment = {"is_public": False, "body": ""}
        self.assertFalse(is_mention(comment, "@support-agent"))


if __name__ == "__main__":
    unittest.main()
