import unittest
import terminology

class TerminologyTests(unittest.TestCase):
 def test_cross_context_and_exceptions(self):
  glossary={'terms':[{'id':'ore','source_forms':['Sanguinite'],'preferred':'血晶石','allowed_variants':[]}]}
  rows=[{'id':'item','source':'Sanguinite ingot','translation':'血晶石锭'},
        {'id':'quest','source':'Find Sanguinite','translation':'寻找鲜血矿石'},
        {'id':'title','source':'Sanguinite the Wise','translation':'智者桑格尼特','term_exceptions':['ore']},
        {'id':'other','source':'SanguinitePlus feature','translation':'扩展功能'}]
  result=terminology.check(glossary,rows)
  self.assertEqual([issue['item_id'] for issue in result['candidate_issues']],['quest'])
  self.assertTrue(result['semantic_review_required'])
 def test_duplicate_term_ids_rejected(self):
  entry={'id':'same','source_forms':['x'],'preferred':'甲'}
  with self.assertRaises(ValueError):terminology.check({'terms':[entry,entry]},[])
if __name__=='__main__':unittest.main(verbosity=2)
