from rpython.jit.backend.arm.test.support import skip_unless_run_slow_tests
skip_unless_run_slow_tests()
from rpython.jit.backend.llsupport.test.zrpy_gc_boehm_test import test_compile_boehm
