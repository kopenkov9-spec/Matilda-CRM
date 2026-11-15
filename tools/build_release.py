"""Build a source/installer ZIP from an explicit allowlist; never include user data."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent
files = {}
for directory, patterns in {'crm':['*.py'], 'static':['*.js','*.css','*.html','*.svg','*.png','*.webmanifest'],
                             'installer':['*.py','*.ps1'], 'tools':['build_release.py'],
                             'docs':['*.md'], 'docs/images':['*.jpg','*.png'], 'tests':['*.py'],
                             '.github/workflows':['*.yml']}.items():
    for pattern in patterns:
        for path in (ROOT/directory).glob(pattern):
            if path.is_symlink():
                raise RuntimeError('Unexpected symlink: '+str(path))
            files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
for name in ['launcher.py','requirements.txt','requirements-model.txt','INSTALL.vbs','START_CRM.vbs',
             'START_CRM.cmd','START_PHONE_ACCESS.cmd','SETUP_PHONE.cmd','.gitignore','LICENSE','CONTRIBUTING.md','SECURITY.md','requirements-test.txt']:
    files[name] = (ROOT/name).read_bytes()
readme = ROOT/'README-GITHUB.md'
if not readme.exists():
    readme = ROOT/'README.md'
files['README.md'] = readme.read_bytes()
files['config.example.json'] = json.dumps({'model_path':'C:/Models/Phi-4-mini-instruct',
    'model_python':'.venv/Scripts/python.exe','device':'auto','llm_port':8091,'crm_port':8090,
    'host':'127.0.0.1','timezone':'Europe/Sofia'},indent=2).encode()
files['SHA256SUMS.txt'] = ''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n'
                                for name,data in sorted(files.items())).encode()
out = ROOT/'dist'
out.mkdir(exist_ok=True)
archive = out/'Matilda-CRM-Setup.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
    for name,data in sorted(files.items()):
        bundle.writestr('Matilda-CRM/'+name,data)
(out/'Matilda-CRM-Setup.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
print(archive)
print(len(files),'files;',archive.stat().st_size,'bytes')
