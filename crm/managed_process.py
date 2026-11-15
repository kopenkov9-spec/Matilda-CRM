"""Private child entry point; never starts a workload outside the launcher's job."""
import os
from pathlib import Path
import runpy
import sys

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
name, module, *arguments = sys.argv[1:]
sys.stdout = (root/'runtime'/f'{name}.stdout.log').open('a', encoding='utf-8', buffering=1)
sys.stderr = (root/'runtime'/f'{name}.stderr.log').open('a', encoding='utf-8', buffering=1)
try:
    from crm.process_group import join_group
    join_group()
    sys.argv = [module, *arguments]
    runpy.run_module(module, run_name='__main__')
except BaseException:
    import traceback
    traceback.print_exc()
    raise
