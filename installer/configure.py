"""Fresh installs only. Existing model weights are always referenced in place."""
import argparse
import json
from pathlib import Path
import sys
from huggingface_hub import snapshot_download
from transformers import AutoConfig, AutoTokenizer
import torch

parser = argparse.ArgumentParser()
parser.add_argument('--request', required=True)
parser.add_argument('--root', required=True)
args = parser.parse_args()
request = json.loads(Path(args.request).read_text(encoding='utf-8-sig'))
root = Path(args.root).resolve()
model = request.get('model')
if not model:
    if not request.get('downloadPhi'):
        raise RuntimeError('Select an existing model first.')
    model = snapshot_download('microsoft/Phi-4-mini-instruct', cache_dir=root/'models/hub',
                              allow_patterns=['*.json','*.safetensors','*.model','*.txt','*.tiktoken'])
model = Path(model).resolve()
config = AutoConfig.from_pretrained(model, local_files_only=True, trust_remote_code=False)
if config.model_type != 'phi3' or config.hidden_size != 3072:
    raise RuntimeError('Select the original Phi-4-mini-instruct model.')
index = json.loads((model/'model.safetensors.index.json').read_text())
for shard in set(index['weight_map'].values()):
    # HF cache snapshots normally point into the repository's sibling blobs directory.
    if Path(shard).name != shard or not shard.endswith('.safetensors'):
        raise RuntimeError('Invalid model shard name: '+shard)
    candidate = (model/shard).resolve()
    allowed = candidate.is_relative_to(model)
    if model.parent.name == 'snapshots' and model.parent.parent.name.startswith('models--'):
        allowed = allowed or candidate.is_relative_to(model.parent.parent/'blobs')
    if not allowed or not candidate.is_file():
        raise RuntimeError('Missing or invalid model shard: '+shard)
AutoTokenizer.from_pretrained(model, local_files_only=True, trust_remote_code=False)
if request.get('speech'):
    snapshot_download('Systran/faster-whisper-small', local_dir=root/'models/whisper-small',
                      allow_patterns=['config.json','model.bin','tokenizer.json','vocabulary.*','preprocessor_config.json'])
configuration = {'model_path':str(model),'model_python':sys.executable,'device':'auto',
                 'llm_port':8091,'crm_port':8090,'host':'127.0.0.1','timezone':'Europe/Sofia'}
(root/'config.json').write_text(json.dumps(configuration,ensure_ascii=False,indent=2),encoding='utf-8')
print('Configured', 'CUDA' if torch.cuda.is_available() else 'CPU', 'model in place:',model)
