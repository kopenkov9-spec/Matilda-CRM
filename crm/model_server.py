"""Read original safetensors in place. No downloads, conversion, disk offload or model copies."""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
os.environ['PYTHONDONTWRITEBYTECODE']='1'

def main():
    # A process lock prevents duplicate model loads when the launcher is clicked twice.
    import msvcrt
    lock_path=ROOT/'runtime/phi.lock'
    lock_path.parent.mkdir(exist_ok=True)
    process_lock=lock_path.open('a+b')
    if process_lock.tell()==0:
        process_lock.write(b'0');process_lock.flush()
    process_lock.seek(0)
    try:
        msvcrt.locking(process_lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        print('Phi is already loading or running.',flush=True)
        return
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    model_path=Path(config['model_path'])
    if not (model_path/'model.safetensors.index.json').exists():
        raise RuntimeError('Original Phi model not found at configured path')
    device = config.get('device','auto')
    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    if device not in ('cuda','cpu'):
        raise RuntimeError('device must be auto, cuda or cpu')
    if device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA is not available')
    print('Loading original Phi safetensors in place:',model_path,flush=True)
    torch.set_num_threads(4)
    tokenizer=AutoTokenizer.from_pretrained(str(model_path),local_files_only=True,trust_remote_code=False)
    model=AutoModelForCausalLM.from_pretrained(str(model_path),local_files_only=True,trust_remote_code=False,
            torch_dtype=torch.float16 if device=='cuda' else torch.float32,attn_implementation='sdpa',
            use_safetensors=True).to(device).eval()
    lock=threading.Lock()
    print('Phi ready on',device,'; allocated GPU MiB:',round(torch.cuda.memory_allocated()/2**20) if device=='cuda' else 0,flush=True)

    class Handler(BaseHTTPRequestHandler):
        timeout = 15

        def trusted_request(self):
            port = config['llm_port']
            return self.headers.get('Host') in {f'127.0.0.1:{port}', f'localhost:{port}'} and not self.headers.get('Origin')

        def response(self,status,data):
            raw=json.dumps(data,ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.trusted_request():
                return self.response(403,{'error':'Untrusted request'})
            if self.path=='/health':
                self.response(200,{'status':'ok','model':'Phi-4-mini-instruct','source':'original-safetensors'})
            else:
                self.response(404,{'error':'Not found'})

        def do_POST(self):
            if not self.trusted_request() or self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding'):
                return self.response(403,{'error':'Untrusted request'})
            if self.path!='/v1/chat/completions':
                return self.response(404,{'error':'Not found'})
            try:
                n=int(self.headers.get('Content-Length','0'))
            except ValueError:
                return self.response(400,{'error':'Invalid content length'})
            if not 0<n<=65536:
                return self.response(413,{'error':'Request too large'})
            if not lock.acquire(blocking=False):
                return self.response(429,{'error':'Model busy'})
            try:
                body=json.loads(self.rfile.read(n))
                messages=body['messages']
                schema=body.get('response_format',{}).get('schema')
                if schema:
                    messages[0]['content']+='\nReturn one JSON object conforming to this schema. No markdown.\n'+json.dumps(schema,separators=(',',':'))
                prompt=tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
                inputs=tokenizer(prompt,return_tensors='pt').to(device)
                input_len=inputs.input_ids.shape[-1]
                max_new=min(int(body.get('max_tokens',1200)),1800)
                if input_len>5000:
                    return self.response(400,{'error':'Context too long; shorten the message'})
                started=time.monotonic()
                with torch.inference_mode():
                    output=model.generate(**inputs,max_new_tokens=max_new,do_sample=False,use_cache=True,
                                          pad_token_id=tokenizer.eos_token_id)
                tokens=output[0,input_len:]
                text=tokenizer.decode(tokens,skip_special_tokens=True).strip()
                print('Generated',len(tokens),'tokens in',round(time.monotonic()-started,1),'s',flush=True)
                self.response(200,{'choices':[{'message':{'role':'assistant','content':text},
                                             'finish_reason':'length' if len(tokens)>=max_new else 'stop'}]})
                del output,inputs,tokens
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                self.response(503,{'error':'Not enough GPU memory'})
            except Exception as error:
                print(type(error).__name__,str(error),flush=True)
                self.response(500,{'error':'Local inference failed'})
            finally:
                lock.release()

        def log_message(self,format,*args):
            pass

    ThreadingHTTPServer(('127.0.0.1',int(config['llm_port'])),Handler).serve_forever()

if __name__=='__main__':
    main()
