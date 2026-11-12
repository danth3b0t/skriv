import unittest
from checker import invalid_blocks,repair,gold_lexical_ok,keys,punctuation,insertion_only
class CheckerTests(unittest.TestCase):
 def test_copy_and_punctuation(self):
  self.assertFalse(invalid_blocks('The fix is important','The fix—is important.'))
 def test_added_reordered_or_negated_words(self):
  for out in ['This is very safe.','Safe is this.','This is not safe.','This is.']:
   self.assertTrue(invalid_blocks('This is safe.',out),out)
 def test_contextual_like(self):
  self.assertTrue(invalid_blocks('I like Rust.','I Rust.'))
  self.assertFalse(invalid_blocks('I want to like test.','I want to test.'))
 def test_correction_and_literal_cue(self):
  self.assertFalse(invalid_blocks('I want it written in C, uh no, in Rust.','I want it written in Rust.'))
  self.assertTrue(invalid_blocks('There is no wait time.','There is time.'))
  self.assertTrue(invalid_blocks('I mean it.','It.'))
 def test_itn_values_and_known_merges(self):
  self.assertFalse(invalid_blocks('two hundred and fifty dollars','$250'))
  self.assertFalse(invalid_blocks('twenty three and ten percent','23 and 10%'))
  self.assertTrue(invalid_blocks('twenty three','24'))
  self.assertTrue(invalid_blocks('$1,250.00','$1,250.01'))
  self.assertFalse(invalid_blocks('super whispered','Superwhisper'))
 def test_span_fallback(self):
  result,n=repair('um keep my words now','Keep my lovely words now.')
  self.assertEqual(n,1)
  self.assertFalse(invalid_blocks('um keep my words now',result))
  self.assertEqual(keys(result),['keep','my','words','now'])
 def test_punctuation_pass_cannot_change_case_or_lexicon(self):
  self.assertFalse(invalid_blocks('We need logs','We need: logs.',True))
  for out in ['we need logs','We need good logs','We logs']:
   self.assertTrue(invalid_blocks('We need logs',out,True))
 def test_gold_audit_catches_overdeletion(self):
  self.assertFalse(gold_lexical_ok('Keep red no wait blue','Blue',['Keep blue']))
 def test_insert_only_keeps_original_marks(self):
  self.assertTrue(insertion_only('We need logs', 'We need: logs.'))
  self.assertFalse(insertion_only('We need logs.', 'We need logs!'))
  self.assertFalse(insertion_only('We need logs', 'We need more logs.'))
 def test_mark_locations(self):
  self.assertIn((2,'—'),punctuation('The fix—is important.'))
  self.assertIn((4,'.'),punctuation('The fix—is important.'))
if __name__=='__main__':unittest.main()
