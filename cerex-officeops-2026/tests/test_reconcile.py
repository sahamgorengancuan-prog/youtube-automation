import unittest,tempfile,json,csv,hashlib
from pathlib import Path
from reconcile import validate,reconcile,audit,ROOT

class ReconcileTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=str(ROOT))
  self.d=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def test_end_to_end(self):
  a=validate(self.d);b=reconcile(self.d);c=audit(self.d)
  self.assertEqual((a["orders"],a["invoices"]),(6,7))
  self.assertEqual(b["matched_rows"],2)
  self.assertEqual(b["total_findings"],8)
  self.assertEqual(b["exceptions"],{
      "DUPLICATE_INVOICE_PO":2,"MISSING_INVOICE":1,
      "MISSING_PURCHASE_ORDER":1,"PRICE_MISMATCH":1,"QUANTITY_MISMATCH":1})
  self.assertEqual(c["approval_status"],"NOT_APPROVED")
  self.assertEqual(c["external_system_changes"],0)
 def test_broken_evidence_fails(self):
  validate(self.d);reconcile(self.d)
  with (self.d/"findings.csv").open("a") as f:f.write("\ntampering")
  with self.assertRaises(ValueError):audit(self.d)
 def test_dependency_validation_required(self):
  with self.assertRaises(FileNotFoundError):reconcile(self.d)
 def test_audit_dependency_required(self):
  with self.assertRaises(FileNotFoundError):audit(self.d)
 def test_only_synthetic_data(self):
  o=validate(self.d)
  self.assertEqual(o["status"],"VALIDATED")
  self.assertTrue((ROOT/"fixtures/orders.csv").exists())
 def test_no_hidden_approval(self):
  validate(self.d);reconcile(self.d);c=audit(self.d)
  self.assertNotEqual(c["status"],"APPROVED")
if __name__=="__main__":unittest.main()
