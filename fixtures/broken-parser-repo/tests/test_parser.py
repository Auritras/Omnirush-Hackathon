import unittest

from parser import ParseError, parse, parse_assignment


class ParserTests(unittest.TestCase):
    def test_simple_assignment(self):
        self.assertEqual(parse_assignment("mode=fast"), ("mode", "fast"))

    def test_comments_and_blank_lines_are_ignored(self):
        self.assertEqual(parse("\n# nearby comment\nmode=fast\n"), {"mode": "fast"})

    def test_values_may_contain_equals(self):
        self.assertEqual(parse("query=https://example.test/?mode=fast"), {"query": "https://example.test/?mode=fast"})

    def test_missing_equals_is_a_parse_error(self):
        with self.assertRaises(ParseError):
            parse("not-an-assignment")


if __name__ == "__main__":
    unittest.main()
