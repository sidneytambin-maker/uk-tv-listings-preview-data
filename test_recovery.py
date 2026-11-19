import gzip,hashlib,io,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from urllib.error import HTTPError,URLError
from http.client import IncompleteRead
import build_snapshot as builder
import snapshot_reader as reader
from publication_check import wait_for_artifact,verify_publication

def http_error(code):return HTTPError('https://example.test',code,'test failure',{},None)

class PreviousGuideRecoveryTests(unittest.TestCase):
    def test_temporary_failures_retry_complete_pair(self):
        for error in (http_error(503),http_error(404),URLError('temporary'),TimeoutError(),IncompleteRead(b'x',2)):
            with self.subTest(error=type(error).__name__),patch.object(reader,'_download_previous',side_effect=[error,{'valid':True}]) as get,patch.object(reader.time,'sleep') as sleep:
                self.assertEqual(reader.download_previous('https://example.test'),{'valid':True})
                self.assertEqual(get.call_count,2);sleep.assert_called_once_with(5)

    def test_repeated_failure_is_not_silent_success(self):
        with patch.object(reader,'_download_previous',side_effect=http_error(503)) as get,patch.object(reader.time,'sleep') as sleep:
            with self.assertRaises(HTTPError):reader.download_previous('https://example.test')
            self.assertEqual(get.call_count,3)
            self.assertEqual([c.args[0] for c in sleep.call_args_list],[5,15])

    def test_invalid_data_and_permission_errors_are_not_retried(self):
        for error in (ValueError('checksum mismatch'),http_error(401),http_error(403)):
            with patch.object(reader,'_download_previous',side_effect=error) as get,patch.object(reader.time,'sleep') as sleep:
                with self.assertRaises(type(error)):reader.download_previous('https://example.test')
                self.assertEqual(get.call_count,1);sleep.assert_not_called()

    def test_missing_snapshot_does_not_remove_previous_coverage_checks(self):
        with patch.object(reader,'download',side_effect=http_error(404)):
            with self.assertRaises(HTTPError):reader._download_previous('https://example.test')

class SourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now=int(time.time());self.known={'BBC2.uk':'2'}
        start=builder.dt.datetime.fromtimestamp(self.now,builder.dt.timezone.utc).strftime('%Y%m%d%H%M%S %z')
        end=builder.dt.datetime.fromtimestamp(self.now+900,builder.dt.timezone.utc).strftime('%Y%m%d%H%M%S %z')
        self.xml=f'<tv><channel id="BBC2.uk"><display-name>BBC Two</display-name></channel><programme channel="BBC2.uk" start="{start}" stop="{end}"><title>Programme</title></programme></tv>'.encode()
    def meta(self,total=1):return json.dumps({'meta':{'total':total,'generated_at':builder.iso(self.now)}})

    def test_count_race_recollects_metadata_and_xml(self):
        fetch=Mock(side_effect=[self.meta(2),self.xml,self.meta(),self.xml])
        with patch.object(builder.time,'sleep') as sleep:
            rows,missing,stamp,requests=builder.fetch_export(self.now,self.now+3600,self.known,fetch)
        self.assertEqual(len(rows),1);self.assertEqual(requests,4)
        self.assertEqual([c.args[0] for c in fetch.call_args_list],['listings','listings.xml']*2)
        sleep.assert_called_once_with(5)

    def test_consistently_incomplete_export_is_rejected(self):
        fetch=Mock(side_effect=[self.meta(2),self.xml]*3)
        with patch.object(builder.time,'sleep'):
            with self.assertRaises(builder.SourceChanged):builder.fetch_export(self.now,self.now+3600,self.known,fetch)
        self.assertEqual(fetch.call_count,6)

    def test_invalid_xml_is_not_accepted_or_retried(self):
        fetch=Mock(side_effect=[self.meta(),b'<!DOCTYPE tv><tv/>'])
        with patch.object(builder.time,'sleep') as sleep:
            with self.assertRaisesRegex(ValueError,'Unsafe XML'):builder.fetch_export(self.now,self.now+3600,self.known,fetch)
        self.assertEqual(fetch.call_count,2);sleep.assert_not_called()

    def test_empty_window_needs_fresh_metadata(self):
        fetch=Mock(return_value=self.meta(0))
        self.assertEqual(builder.fetch_export(self.now,self.now+3600,self.known,fetch)[0],[])
        fetch.assert_called_once()
        old=json.loads(self.meta(0));old['meta']['generated_at']=builder.iso(self.now-7200)
        with self.assertRaises(ValueError):builder.fetch_export(self.now,self.now+3600,self.known,Mock(return_value=json.dumps(old)))

    def test_revision_race_restarts_whole_collection_once(self):
        previous={'last':'good'}
        with patch.object(builder,'_build',side_effect=[builder.RevisionChanged('changed'),{'valid':True}]) as build,patch.object(builder.time,'sleep'):
            self.assertEqual(builder.build({},Path('unused'),previous),{'valid':True})
            self.assertEqual(build.call_count,2)
            self.assertTrue(all(c.args[2] is previous for c in build.call_args_list))

    def test_persistent_revision_race_still_fails(self):
        with patch.object(builder,'_build',side_effect=builder.RevisionChanged('changed')) as build,patch.object(builder.time,'sleep'):
            with self.assertRaises(builder.RevisionChanged):builder.build({},Path('unused'))
            self.assertEqual(build.call_count,2)

    def test_other_validation_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory);(output/'current.json').write_text('last good guide')
            with patch.object(builder,'_build',side_effect=ValueError('coverage regression')) as build:
                with self.assertRaises(ValueError):builder.build({},output)
                self.assertEqual(build.call_count,1)
            self.assertEqual((output/'current.json').read_text(),'last good guide')

class PublicationTests(unittest.TestCase):
    def ready(self,**changes):
        item=dict(id=123,name='github-pages',expired=False,size_in_bytes=100);item.update(changes)
        return {'artifacts':[item]}

    def test_waits_for_exact_uploaded_artifact(self):
        get=Mock(side_effect=[{'artifacts':[]},self.ready(id=99),self.ready()]);sleep=Mock()
        wait_for_artifact(get,'123',sleep)
        self.assertEqual(get.call_count,3);self.assertEqual(sleep.call_count,2)

    def test_expired_empty_or_wrong_name_cannot_pass(self):
        for item in (self.ready(expired=True),self.ready(size_in_bytes=0),self.ready(name='other')):
            with self.assertRaises(RuntimeError):wait_for_artifact(Mock(return_value=item),'123',Mock(),attempts=2)

    def test_recovers_from_api_service_failure(self):
        get=Mock(side_effect=[http_error(503),self.ready()])
        wait_for_artifact(get,123,Mock());self.assertEqual(get.call_count,2)

    def test_permission_failure_is_not_retried(self):
        get=Mock(side_effect=http_error(403))
        with self.assertRaises(HTTPError):wait_for_artifact(get,123,Mock())
        self.assertEqual(get.call_count,1)

    def guide(self):
        now=int(time.time());value=builder.pack([[now,now+900,'2','A programme','','',False,False]],
            {'2':{'name':'BBC Two','xmltv_id':'BBC2.uk'}},now,now,now+86400,now)
        body=json.dumps(value).encode();raw=gzip.compress(body);digest=hashlib.sha256(raw).hexdigest()
        manifest=dict(schema=1,checked_at=now,window=value['window'],file='guide-'+digest+'.json.gz',
            sha256=digest,bytes=len(raw),json_bytes=len(body),programmes=1,channels=1)
        return manifest,raw

    def test_public_readback_waits_for_new_manifest_and_checks_bytes(self):
        manifest,raw=self.guide();get=Mock(side_effect=[b'{}',json.dumps(manifest).encode(),raw]);sleep=Mock()
        verify_publication('https://example.test',manifest,get,sleep)
        self.assertEqual(get.call_count,3);sleep.assert_called_once_with(5)

    def test_public_readback_retries_transient_404(self):
        manifest,raw=self.guide();get=Mock(side_effect=[http_error(404),json.dumps(manifest).encode(),raw])
        verify_publication('https://example.test',manifest,get,Mock())
        self.assertEqual(get.call_count,3)

    def test_corrupt_public_bytes_are_rejected(self):
        manifest,raw=self.guide();get=Mock(side_effect=[json.dumps(manifest).encode(),raw+b'bad'])
        with self.assertRaises(ValueError):verify_publication('https://example.test',manifest,get,Mock())

    def test_old_manifest_cannot_claim_publication_success(self):
        manifest,raw=self.guide();get=Mock(return_value=b'{}')
        with self.assertRaises(RuntimeError):verify_publication('https://example.test',manifest,get,Mock(),attempts=2)
        self.assertEqual(get.call_count,2)

if __name__=='__main__':unittest.main()
