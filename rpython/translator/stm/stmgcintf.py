import os
from rpython.translator.tool.cbuild import ExternalCompilationInfo
from rpython.conftest import cdir as cdir2


cdir = os.path.abspath(os.path.join(cdir2, '..', 'stm'))

separate_source = '''
#include "src_stm/stmgc.c"
'''

eci = ExternalCompilationInfo(
    include_dirs = [cdir, cdir2],
    includes = ['src_stm/stmgc.h'],
    pre_include_bits = ['#define RPY_STM 1'],
    separate_module_sources = [separate_source],
)
