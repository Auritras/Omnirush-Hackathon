import unittest

from proofix.schemas import SchemaError, validate_hypothesis, validate_patch_proposal, validate_triage


EVIDENCE = [{"kind": "file", "path": "src/parser.py", "line": 42, "summary": "Parser branch"}]


class SchemaTests(unittest.TestCase):
    def test_validates_triage_and_hypothesis_records(self):
        triage = validate_triage(
            {
                "schema_version": "1.0",
                "category": "test_assertion",
                "suspected_subsystem": "parser",
                "relevant_files": ["src/parser.py"],
                "command_assumptions": ["Python 3.12 is available"],
                "confidence": 0.8,
                "evidence": EVIDENCE,
            }
        )
        self.assertEqual(triage.category, "test_assertion")
        hypothesis = validate_hypothesis(
            {
                "schema_version": "1.0",
                "hypothesis": "The parser drops a required token",
                "supporting_evidence": EVIDENCE,
                "contradicting_evidence": [],
                "confidence": 0.75,
                "experiment": "Run the focused parser test with token tracing",
                "role": "stack-trace-analyst",
                "session_id": "session-1",
            }
        )
        self.assertEqual(hypothesis.role, "stack-trace-analyst")
        self.assertEqual(hypothesis.session_id, "session-1")

    def test_rejects_missing_or_unsupported_schema_and_evidence(self):
        valid = {
            "schema_version": "1.0",
            "category": "unknown",
            "suspected_subsystem": "unknown",
            "relevant_files": ["src/parser.py"],
            "command_assumptions": [],
            "confidence": 0.1,
            "evidence": EVIDENCE,
        }
        for mutation in (
            {"schema_version": "2.0"},
            {"schema_version": "1.0", "evidence": []},
            {"schema_version": "1.0", "evidence": [{"kind": "mystery", "summary": "no"}]},
            {"schema_version": "1.0", "evidence": [{"kind": "file", "path": "../outside.py"}]},
            {"schema_version": "1.0", "evidence": [{"kind": "file", "path": "/tmp/outside.py"}]},
        ):
            candidate = dict(valid)
            candidate.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(SchemaError):
                validate_triage(candidate)

    def test_rejects_bad_confidence_contradictions_and_experiments(self):
        candidate = {
            "schema_version": "1.0",
            "hypothesis": "A cause",
            "supporting_evidence": EVIDENCE,
            "contradicting_evidence": [],
            "confidence": 0.5,
            "experiment": "Run a focused check",
        }
        for confidence in (-0.01, 1.01, True, "0.5"):
            with self.subTest(confidence=confidence), self.assertRaises(SchemaError):
                validate_hypothesis({**candidate, "confidence": confidence})
        with self.assertRaises(SchemaError):
            validate_hypothesis({**candidate, "contradicting_evidence": [{"kind": "file", "path": "bad\\path.py"}]})
        with self.assertRaises(SchemaError):
            validate_hypothesis({**candidate, "experiment": ""})

    def test_validates_patch_fields_and_rejects_out_of_scope_paths(self):
        proposal = validate_patch_proposal(
            {
                "schema_version": "1.0",
                "digest": "sha256:abc",
                "diff_ref": "artifacts/patch.diff",
                "files": ["src/parser.py", "tests/test_parser.py"],
                "regression_test": "pytest tests/test_parser.py",
                "risk_summary": "Low risk",
                "verification_plan": ["pytest tests/test_parser.py"],
                "evidence": EVIDENCE,
            }
        )
        self.assertEqual(proposal.files[0], "src/parser.py")
        with self.assertRaises(SchemaError):
            validate_patch_proposal(
                {
                    "schema_version": "1.0",
                    "digest": "sha256:abc",
                    "files": ["/etc/passwd"],
                    "regression_test": "pytest",
                    "risk_summary": "risk",
                    "verification_plan": ["pytest"],
                    "evidence": EVIDENCE,
                }
            )
        with self.assertRaises(SchemaError):
            validate_patch_proposal(
                {
                    "schema_version": "1.0",
                    "digest": "sha256:abc",
                    "files": ["src/parser.py"],
                    "regression_test": "pytest",
                    "risk_summary": "risk",
                    "verification_plan": [],
                    "evidence": [],
                }
            )


if __name__ == "__main__":
    unittest.main()
