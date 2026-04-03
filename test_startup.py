#!/usr/bin/env python3
"""GenieCLI startup — no output redirect. Output goes to tmux pane for capture."""
import sys, os, time

LOG = '/tmp/genie-startup-log.txt'

# Patch print to also write log
_real_print = print
def log_print(*args, **kwargs):
    kwargs['flush'] = True
    _real_print(*args, **kwargs)
    with open(LOG, 'a') as f:
        _real_print(*args, file=f, **kwargs)

import builtins
builtins.print = log_print

with open(LOG, 'w') as f:
    f.write('')

log_print('STEP1 - starting')

from genie.cli import app
log_print('STEP2 - app imported')

sys.argv = ['genie', '-s']
log_print(f'STEP3 - argv={sys.argv}')

try:
    app()
    log_print('APP_RETURNED normally')
except Exception as e:
    import traceback
    traceback.print_exc(file=open(LOG, 'a'))
    log_print(f'APP_ERROR: {e}')
log_print('DONE')
