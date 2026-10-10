import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import import_dataset, context_for, build_request, classify, parse_prediction, Handler

FUTURE = 'DO_NOT_LEAK_FUTURE_ASSISTANT_9aef'
LATER = 'DO_NOT_LEAK_FUTURE_USER_b42d'
SESSION = {'session_id': 'test', 'turns': [
    {'turn_id': '0', 'role': 'user', 'content': 'What does this do?'},
    {'turn_id': '1', 'role': 'assistant', 'content': 'PRIOR_ASSISTANT_KEEP'},
    {'turn_id': '2', 'role': 'user', 'content': 'is there any other go pkg to do so?'},
    {'turn_id': '3', 'role': 'assistant', 'content': FUTURE},
    {'turn_id': '4', 'role': 'user', 'content': LATER}]}
PREDICTION = {'primary':'D','secondary':[],'queued':[],'rationale':'compare alternatives','needs_review':False}

class MockAPI(BaseHTTPRequestHandler):
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.received.append((self.path, dict(self.headers), data))
        output=json.dumps(PREDICTION)
        if self.path.endswith('/responses'):
            result={'output':[{'type':'reasoning','summary':[{'text':FUTURE}]},{'type':'message','content':[{'type':'output_text','text':output}]}]}
        elif self.path.endswith('/messages'):
            result={'content':[{'type':'thinking','thinking':FUTURE},{'type':'text','text':output}]}
        elif ':generateContent' in self.path:
            result={'candidates':[{'content':{'parts':[{'thought':True,'text':FUTURE},{'text':output}]}}]}
        else:
            result={'choices':[{'message':{'content':output}}]}
        body=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass

class IntentTests(unittest.TestCase):
    def request(self, protocol='chat'):
        return {'session':SESSION,'target_index':2,'system_prompt':'Classify only; return JSON.','config':{'protocol':protocol,'base_url':'http://127.0.0.1:1234/v1','model':'test-model','api_key':'memory-only-key','max_tokens':4096}}

    def test_all_protocols_cut_off_before_future(self):
        for protocol in ['chat','responses','anthropic','gemini']:
            with self.subTest(protocol=protocol):
                url,headers,body,audit=build_request(self.request(protocol))
                serialized=json.dumps(body)
                self.assertNotIn(FUTURE,serialized);self.assertNotIn(LATER,serialized)
                self.assertIn('PRIOR_ASSISTANT_KEEP',serialized)
                self.assertIn('is there any other',serialized)
                self.assertNotIn('memory-only-key',serialized)
                self.assertEqual(audit['history_messages'],2)
                self.assertNotIn('api_key',json.dumps(audit))

    def test_first_turn_has_no_history(self):
        context=context_for(SESSION,0)
        self.assertEqual(context['prior_context'],[])
        self.assertEqual(context['target_user_turn']['content'],'What does this do?')

    def test_invalid_target(self):
        for index in [-1,1,9,True]:
            with self.assertRaises(ValueError):context_for(SESSION,index)

    def test_no_alternate_state_in_extra(self):
        for forbidden in ['messages','input','previous_response_id','tools','instructions','system','contents','api_key']:
            data=self.request();data['config']['extra']={forbidden:'canary'}
            with self.assertRaises(ValueError):build_request(data)

    def test_import_does_not_execute_js(self):
        with self.assertRaises(ValueError):import_dataset('window.CLEANED_SESSIONS = []; process.exit(1);','bad.js')
        data=import_dataset('window.CLEANED_SESSIONS = '+json.dumps([SESSION])+';','safe.js')
        self.assertEqual(data['report']['user_prompts'],3)

    def test_raw_claude_and_codex(self):
        rows=[{'type':'file-history-snapshot'},{'sessionId':'a','type':'user','uuid':'u','message':{'role':'user','content':'real user'}}, {'sessionId':'a','type':'assistant','message':{'role':'assistant','content':[{'type':'thinking','thinking':'PRIVATE'},{'type':'text','text':'PUBLIC'}]}}, {'sessionId':'a','type':'user','message':{'role':'user','content':[{'type':'tool_result','content':'TOOLS'}]}}]
        data=import_dataset(json.dumps(rows),'claude.json');self.assertEqual(data['report']['user_prompts'],1)
        content=json.dumps(data);self.assertIn('PUBLIC',content);self.assertNotIn('PRIVATE',content);self.assertNotIn('TOOLS',content)
        rows=[{'type':'session_meta','payload':{'id':'c'}},{'type':'event_msg','payload':{'type':'user_message','message':'duplicate'}},{'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'duplicate'}]}},{'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'<environment_context>injected</environment_context>'}]}}, {'type':'event_msg','payload':{'type':'agent_message','message':'event-only assistant'}}]
        data=import_dataset('\n'.join(map(json.dumps,rows)),'codex.jsonl');self.assertEqual(data['report']['user_prompts'],1);self.assertEqual(data['sessions'][0]['turns'][1]['content'],'event-only assistant')

    def test_parse_output(self):
        self.assertEqual(parse_prediction('```json\n'+json.dumps(PREDICTION)+'\n```')['primary'],'D')
        self.assertEqual(parse_prediction('{"primary":"decide"}')['primary'],'D')
        for raw in ['nonsense','{"primary":"X"}','{"primary":"U","needs_review":"false"}','{"primary":"U","secondary":"E"}']:
            with self.assertRaises((ValueError,json.JSONDecodeError)):parse_prediction(raw)

    def test_mock_upstream_all_protocols(self):
        mock=ThreadingHTTPServer(('127.0.0.1',0),MockAPI);mock.received=[]
        thread=threading.Thread(target=mock.serve_forever,daemon=True);thread.start()
        try:
            for protocol in ['chat','responses','anthropic','gemini']:
                data=self.request(protocol);data['config']['base_url']=f'http://127.0.0.1:{mock.server_port}/v1'
                answer=classify(data)
                self.assertEqual(answer['prediction']['primary'],'D')
                self.assertEqual(answer['prediction']['turn_id'],'2')
                self.assertNotIn(FUTURE,answer['raw_output'])
                body=json.dumps(mock.received[-1][2]);self.assertNotIn(FUTURE,body);self.assertNotIn(LATER,body)
            self.assertEqual(len(mock.received),4)
        finally:mock.shutdown();mock.server_close();thread.join()

    def test_reference_and_user_files_when_available(self):
        folder=Path('/Users/yutianya/Downloads')
        expected={'trial.json':15,'001ff8d2-ee5a-4976-9052-45f61230e52d.jsonl':12,'019d8ec5-8a79-7c52-8c0a-12b4ae22e19f.jsonl':48}
        for filename,count in expected.items():
            path=folder/filename
            if not path.exists():continue
            data=import_dataset(path.read_text(),filename);self.assertEqual(data['report']['user_prompts'],count)
            for session in data['sessions']:
                for i,t in enumerate(session['turns']):
                    if t['role']=='user':
                        context=context_for(session,i);self.assertEqual(len(context['prior_context']),i)
        path=folder/'task1009-main/portable_ir02_annotator/data/cleaned/sessions_sample100.js'
        if path.exists():
            data=import_dataset(path.read_text(),path.name);self.assertEqual(data['report']['sessions'],100);self.assertEqual(data['report']['user_prompts'],752)

if __name__=='__main__':unittest.main()
