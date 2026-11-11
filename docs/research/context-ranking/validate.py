"""Small independent checks for replay risks; synthetic data only."""
import json
import unittest
import experiment as x
from hark_client.context.agents import read_pi

class ReplayChecks(unittest.TestCase):
    def test_gold_english_and_opaque_boundaries(self):
        self.assertEqual(set(x.gold('Please fix the files and running tests.')) ,set())
        self.assertEqual(set(x.gold('useSessionStore --dry-run src/store.ts')), {'useSessionStore','--dry-run','src/store.ts'})
        self.assertEqual(set(x.gold('call_0123456789abcdef')),set())
        self.assertEqual(set(x.gold('Diktování')),set())  # no truncated ASCII fragments

    def test_branch_and_target_isolation_against_production_reader(self):
        s=x.Session('pi','pi')
        s.nodes={
            'u1':x.Event('user','PriorToken',[],None,1,'/synthetic'),
            'a1':x.Event('assistant','LatestToken',['src/store.ts'],'u1',2,'/synthetic'),
            'a2':x.Event('assistant','SiblingToken',[],'u1',3,'/synthetic'),
            'u2':x.Event('user','TargetToken',[],'a1',4,'/synthetic'),
        }
        h=x.history(s,'u2')
        self.assertEqual(h,[('LatestToken',['src/store.ts'],'PriorToken')])
        rows=[]
        for eid,e in s.nodes.items():
            blocks=[dict(type='text',text=e.text)]
            blocks += [dict(type='toolCall',arguments=dict(path=p)) for p in e.paths]
            rows.append(dict(type='message',id=eid,parentId=e.parent,message=dict(role=e.role,content=blocks)))
        fixture=x.OUT/'synthetic-reader-fixture.jsonl'
        try:
            fixture.write_text('\n'.join(map(json.dumps,rows)))
            self.assertEqual(read_pi(fixture),('LatestToken',['src/store.ts']))
        finally:
            fixture.unlink(missing_ok=True)
        cs=x.candidates(h,[],[],[],'/synthetic','')
        vocabulary={t for t,*_ in cs}
        self.assertIn('PriorToken',vocabulary)
        self.assertNotIn('SiblingToken',vocabulary)
        self.assertNotIn('TargetToken',vocabulary)

    def test_metric_zero_gold_and_distractors(self):
        m=x.Metric()
        sh=[('A',1,{'assistant'}),('B',.5,{'repo'})]
        m.add({'A','C'},sh,{'A'},set())
        m.add(set(),sh,set(),set())
        r=m.row()
        self.assertEqual(r['macro_recall'],.5)
        self.assertEqual(r['micro_recall'],.5)
        self.assertEqual(r['precision'],.25)
        self.assertEqual(r['mean_distractors'],1.5)
        self.assertEqual(r['targets'],2)
        self.assertEqual(r['gold_targets'],1)

    def test_real_aggregate_integrity(self):
        import csv
        with (x.OUT/'metrics.csv').open() as stream:
            rows=list(csv.DictReader(stream))
        test=[r for r in rows if r['split']=='test' and r['cohort']=='primary']
        self.assertTrue(test)
        for r in rows:
            self.assertLessEqual(float(r['mean_terms']),int(r['k']))
            for key in ('macro_recall','micro_recall','precision'):
                self.assertTrue(0<=float(r[key])<=1)
        meta=json.loads((x.OUT/'summary.json').read_text())
        self.assertEqual(meta['selection']['split'],'dev')
        self.assertFalse(meta['selection']['strategy'].startswith('selected_'))
        self.assertGreater(meta['primary_session_splits']['test'],0)

if __name__=='__main__': unittest.main()
