import unittest

from degent.adjudication_packets import SALARY_INTRO, reconstruct, split_text


class LosslessReadingPacketTests(unittest.TestCase):
    def test_distinct_headers_and_pay_reconstruct_all_original_characters(self):
        for title, city, salary in [
            ("Manager IC", "McLean", "$100,000 - $120,000"),
            ("Engineer", "Richmond", "$90,000 - $110,000"),
        ]:
            text = (
                f"Capital One\n{title}\nLocation: {city}, Virginia, United States\n{title}\n"
                "Lead model-platform projects, not direct reports.\nBachelor and four years.\n"
                f"{SALARY_INTRO}\n{city}, VA: {salary} for Engineer\n"
                "Candidates hired to work in other locations retain the applicable range."
            )
            common, variations = split_text(text, title)
            self.assertEqual(reconstruct(common, variations), text)
            self.assertIn("Lead model-platform projects, not direct reports.", common)
            self.assertIn("Bachelor and four years.", common)
            self.assertEqual([v["kind"] for v in variations], ["header", "header", "salary"])

    def test_mixed_salary_requirements_are_never_neutralized(self):
        text = (
            f"Engineer\n{SALARY_INTRO}\nMcLean, VA: $100,000 - $120,000 for Engineer\n"
            "Must supervise three direct reports.\n"
            "Candidates hired to work in other locations retain the applicable range."
        )
        common, variations = split_text(text, "Engineer")
        self.assertIn("Must supervise three direct reports.", common)
        self.assertFalse(any(v["kind"] == "salary" for v in variations))

    def test_first_lines_that_are_duties_are_not_titles(self):
        text = "Capital One\nOwn fraud models\nBachelor and five years\nEngineer\n"
        common, variations = split_text(text, "Engineer")
        self.assertIn("Own fraud models", common)
        self.assertIn("Bachelor and five years", common)
        self.assertEqual(len(variations), 1)

    def test_missing_variant_and_changed_offsets_fail(self):
        common, variations = split_text("Engineer\nDuties", "Engineer")
        with self.assertRaises(ValueError):
            reconstruct(common, [])
        variations[0]["start"] = 1
        with self.assertRaises(ValueError):
            reconstruct(common, variations)

    def test_source_cannot_inject_packet_markers(self):
        with self.assertRaises(ValueError):
            split_text("[header VARIATION]\n", "Engineer")
