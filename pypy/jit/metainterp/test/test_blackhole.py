from pypy.rlib.jit import JitDriver
from pypy.jit.metainterp.test.test_basic import LLJitMixin, OOJitMixin
from pypy.jit.metainterp.blackhole import BlackholeInterpBuilder
from pypy.jit.codewriter.assembler import JitCode
from pypy.rpython.lltypesystem import lltype, llmemory


class FakeCodeWriter:
    pass
class FakeAssembler:
    pass
class FakeCPU:
    def bh_call_i(self, func, calldescr, args_i, args_r, args_f):
        assert func == 321
        assert calldescr == "<calldescr>"
        if args_i[0] < 0:
            raise KeyError
        return args_i[0] * 2

def getblackholeinterp(insns, descrs=[]):
    cw = FakeCodeWriter()
    cw.cpu = FakeCPU()
    cw.assembler = FakeAssembler()
    cw.assembler.insns = insns
    cw.assembler.descrs = descrs
    builder = BlackholeInterpBuilder(cw)
    return builder.acquire_interp()

def test_simple():
    jitcode = JitCode("test")
    jitcode.setup("\x00\x00\x01\x02"
                  "\x01\x02",
                  [])
    blackholeinterp = getblackholeinterp({'int_add/iii': 0,
                                          'int_return/i': 1})
    blackholeinterp.setarg_i(0, 40)
    blackholeinterp.setarg_i(1, 2)
    blackholeinterp.run(jitcode, 0)
    assert blackholeinterp.result_i == 42

def test_simple_const():
    jitcode = JitCode("test")
    jitcode.setup("\x00\x30\x01\x02"
                  "\x01\x02",
                  [])
    blackholeinterp = getblackholeinterp({'int_sub/cii': 0,
                                          'int_return/i': 1})
    blackholeinterp.setarg_i(1, 6)
    blackholeinterp.run(jitcode, 0)
    assert blackholeinterp.result_i == 42

def test_simple_bigconst():
    jitcode = JitCode("test")
    jitcode.setup("\x00\xFD\x01\x02"
                  "\x01\x02",
                  [666, 666, 10042, 666])
    blackholeinterp = getblackholeinterp({'int_sub/iii': 0,
                                          'int_return/i': 1})
    blackholeinterp.setarg_i(1, 10000)
    blackholeinterp.run(jitcode, 0)
    assert blackholeinterp.result_i == 42

def test_simple_loop():
    jitcode = JitCode("test")
    jitcode.setup("\x00\x10\x00\x16\x02"  # L1: goto_if_not_int_gt L2, %i0, 2
                  "\x01\x17\x16\x17"      #     int_add %i1, %i0, %i1
                  "\x02\x16\x01\x16"      #     int_sub %i0, $1, %i0
                  "\x03\x00\x00"          #     goto L1
                  "\x04\x17",             # L2: int_return %i1
                  [])
    blackholeinterp = getblackholeinterp({'goto_if_not_int_gt/Lic': 0,
                                          'int_add/iii': 1,
                                          'int_sub/ici': 2,
                                          'goto/L': 3,
                                          'int_return/i': 4})
    blackholeinterp.setarg_i(0x16, 6)    # %i0
    blackholeinterp.setarg_i(0x17, 100)  # %i1
    blackholeinterp.run(jitcode, 0)
    assert blackholeinterp.result_i == 100+6+5+4+3

def test_simple_exception():
    jitcode = JitCode("test")
    jitcode.setup(    # residual_call_ir_i $<* fn g>, <Descr>, I[%i9], R[], %i8
                  "\x01\xFF\x00\x00\x01\x09\x00\x08"
                  "\x00\x0D\x00"          #     catch_exception L1
                  "\x02\x08"              #     int_return %i8
                  "\x03\x2A",             # L1: int_return $42
                  [321])   # <-- address of the function g
    blackholeinterp = getblackholeinterp({'catch_exception/L': 0,
                                          'residual_call_ir_i/idIRi': 1,
                                          'int_return/i': 2,
                                          'int_return/c': 3},
                                         ["<calldescr>"])
    #
    blackholeinterp.setarg_i(0x9, 100)
    blackholeinterp.run(jitcode, 0)
    assert blackholeinterp.result_i == 200
    #
    blackholeinterp.setarg_i(0x9, -100)
    blackholeinterp.run(jitcode, 0)
    assert blackholeinterp.result_i == 42

# ____________________________________________________________

class BlackholeTests(object):

    def meta_interp(self, *args):
        def counting_init(frame, metainterp, jitcode, greenkey=None):
            previnit(frame, metainterp, jitcode, greenkey)
            self.seen_frames.append(jitcode.name)
        #
        from pypy.jit.metainterp import pyjitpl
        previnit = pyjitpl.MIFrame.__init__.im_func
        try:
            self.seen_frames = []
            pyjitpl.MIFrame.__init__ = counting_init
            return super(BlackholeTests, self).meta_interp(*args)
        finally:
            pyjitpl.MIFrame.__init__ = previnit

    def test_calls_not_followed(self):
        myjitdriver = JitDriver(greens = [], reds = ['n'])
        def h():
            return 42
        def g():
            return h()
        def f(n):
            while n > 0:
                myjitdriver.can_enter_jit(n=n)
                myjitdriver.jit_merge_point(n=n)
                n -= 1
            return g()
        res = self.meta_interp(f, [7])
        assert res == 42
        assert self.seen_frames == ['f', 'f']

    def test_indirect_calls_not_followed(self):
        myjitdriver = JitDriver(greens = [], reds = ['n'])
        def h():
            return 42
        def g():
            return h()
        def f(n):
            while n > 0:
                myjitdriver.can_enter_jit(n=n)
                myjitdriver.jit_merge_point(n=n)
                n -= 1
            if n < 0:
                call = h
            else:
                call = g
            return call()
        res = self.meta_interp(f, [7])
        assert res == 42
        assert self.seen_frames == ['f', 'f']

    def test_oosends_not_followed(self):
        myjitdriver = JitDriver(greens = [], reds = ['n'])
        class A:
            def meth(self):
                return 42
        class B(A):
            def meth(self):
                return 45
        class C(A):
            def meth(self):
                return 64
        def f(n):
            while n > 0:
                myjitdriver.can_enter_jit(n=n)
                myjitdriver.jit_merge_point(n=n)
                n -= 1
            if n < 0:
                x = B()
            else:
                x = C()
            return x.meth()
        res = self.meta_interp(f, [7])
        assert res == 64
        assert self.seen_frames == ['f', 'f']


class TestLLtype(BlackholeTests, LLJitMixin):
    pass

class TestOOtype(BlackholeTests, OOJitMixin):
    pass
