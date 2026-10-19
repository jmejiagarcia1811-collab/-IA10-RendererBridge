import unittest
from app import EDITORIAL_QC_CHECKS, validate_editorial_qc

class EditorialQCTests(unittest.TestCase):
    def test_approval_requires_every_check_true(self):
        checklist = {key: True for key in EDITORIAL_QC_CHECKS}
        self.assertTrue(validate_editorial_qc("approve", checklist))
        checklist[EDITORIAL_QC_CHECKS[0]] = False
        self.assertFalse(validate_editorial_qc("approve", checklist))

    def test_missing_check_fails_closed(self):
        checklist = {key: True for key in EDITORIAL_QC_CHECKS[:-1]}
        self.assertFalse(validate_editorial_qc("approve", checklist))

    def test_reject_does_not_require_approval_checklist(self):
        self.assertTrue(validate_editorial_qc("reject", {}))

    def test_unknown_decision_rejected(self):
        self.assertFalse(validate_editorial_qc("publish", {key: True for key in EDITORIAL_QC_CHECKS}))

if __name__ == "__main__":
    unittest.main()
