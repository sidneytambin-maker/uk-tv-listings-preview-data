import gzip,hashlib,json,unittest
from build_snapshot import pack,parse_export
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

if __name__=='__main__':unittest.main()
