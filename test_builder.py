import gzip,hashlib,json,unittest
from build_snapshot import pack,parse_export,reconcile_conflicts,iso
from snapshot_reader import decode

class BuilderTests(unittest.TestCase):
    def test_roundtrip_and_damage(self):
        now=1794510000
        value=pack([[now,now+60,'2','A programme','A description','',False,False]],{'2':{'name':'BBC Two','xmltv_id':'BBCTwo.uk'}},now,now,now+86400,now)
        body=json.dumps(value,separators=(',',':')).encode();raw=gzip.compress(body,mtime=0);digest=hashlib.sha256(raw).hexdigest()
        manifest=dict(schema=1,checked_at=now,window=value['window'],file='guide-'+digest+'.json.gz',sha256=digest,bytes=len(raw),json_bytes=len(body),programmes=1)
        self.assertEqual(decode(raw,manifest,now),value)
        with self.assertRaises(ValueError):decode(raw+b'bad',manifest,now)
    def test_coverage_regression_is_rejected(self):
        now=1794510000
        previous={'window':[now,now+86400],'rows':[[now+i*60,now+i*60+60,'2',0,0,'',False,False] for i in range(10)]}
        with self.assertRaises(ValueError):pack([],{},now,now,now+86400,now,previous)
    def test_overlap_is_flagged(self):
        now=1794510000
        value=pack([[now,now+100,'2','First','','',False,False],[now+20,now+50,'2','Second','','',False,False]],{},now,now,now+86400,now)
        self.assertTrue(all(p[7] for p in value['rows']))

class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.now=1794528000
        self.rows=[[self.now,self.now+900,'171','Current','','https://www.ontvnow.co.uk/programmes/1',False,False],
          [self.now+900,self.now+10800,'171','Shop on TV','','https://www.ontvnow.co.uk/programmes/2',False,False],
          [self.now+3300,self.now+10800,'171','Shop on TV','','https://www.ontvnow.co.uk/programmes/3',False,False]]
    def fetch(self,updated):
        def get(path,params):
            return json.dumps({'meta':{'generated_at':iso(self.now),'page':1,'last_page':1},'data':[
              dict(channel={'id':p[2]},url=p[5],starts_at=iso(p[0]),ends_at=iso(p[1]),title=p[3],description=p[4],updated_at=updated.get(p[5])) for p in self.rows]})
        return get
    def test_only_newer_revision_resolves_overlap(self):
        urls=[p[5] for p in self.rows]
        result,rev,count=reconcile_conflicts(self.rows,self.fetch({urls[1]:iso(self.now-100),urls[2]:iso(self.now-200)}),self.now)
        self.assertEqual({p[5] for p in result},set(urls[:2]));self.assertEqual(count,1)
        packed=pack(result,{},self.now,self.now,self.now+86400,self.now)
        self.assertFalse(any(p[7] for p in packed['rows']))
    def test_unknown_or_equal_revisions_remain_conflicted(self):
        for updated in ({},{p[5]:iso(self.now-100) for p in self.rows}):
            result,rev,count=reconcile_conflicts(self.rows,self.fetch(updated),self.now)
            self.assertEqual(len(result),3)
            self.assertEqual(sum(p[7] for p in pack(result,{},self.now,self.now,self.now+86400,self.now)['rows']),2)
    def test_changed_identity_and_future_revision_rejected(self):
        fetch=self.fetch({self.rows[1][5]:iso(self.now+100000000)})
        with self.assertRaises(ValueError):reconcile_conflicts(self.rows,fetch,self.now)
        def changed(path,params):
            d=json.loads(self.fetch({})(path,params));d['data'][1]['title']='Changed';return json.dumps(d)
        with self.assertRaises(ValueError):reconcile_conflicts(self.rows,changed,self.now)

if __name__=='__main__':unittest.main()
