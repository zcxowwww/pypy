"""
Implementation of a part of the standard Python opcodes.
The rest, dealing with variables in optimized ways, is in
pyfastscope.py and pynestedscope.py.
"""

from pypy.interpreter.error import OperationError
from pypy.interpreter.baseobjspace import UnpackValueError
from pypy.interpreter import gateway, function, eval
from pypy.interpreter import pyframe, pytraceback
from pypy.interpreter.miscutils import InitializedClass
from pypy.interpreter.argument import Arguments, ArgumentsFromValuestack
from pypy.interpreter.pycode import PyCode
from pypy.interpreter.opcodeorder import opcodeorder
from pypy.tool.sourcetools import func_with_new_name
from pypy.rlib.objectmodel import we_are_translated
from pypy.rlib.rarithmetic import intmask
from pypy.tool import stdlib_opcode as pythonopcode
from pypy.rlib import rstack # for resume points

def unaryoperation(operationname):
    """NOT_RPYTHON"""
    def opimpl(f):
        operation = getattr(f.space, operationname)
        w_1 = f.valuestack.pop()
        w_result = operation(w_1)
        f.valuestack.push(w_result)

    return func_with_new_name(opimpl, "opcode_impl_for_%s" % operationname)

def binaryoperation(operationname):
    """NOT_RPYTHON"""    
    def opimpl(f):
        operation = getattr(f.space, operationname)
        w_2 = f.valuestack.pop()
        w_1 = f.valuestack.pop()
        w_result = operation(w_1, w_2)
        f.valuestack.push(w_result)

    return func_with_new_name(opimpl, "opcode_impl_for_%s" % operationname)        


class PyInterpFrame(pyframe.PyFrame):
    """A PyFrame that knows about interpretation of standard Python opcodes
    minus the ones related to nested scopes."""
    
    ### opcode dispatch ###
 
    # 'opcode_has_arg' is a class attribute: list of True/False whether opcode takes arg
    # 'dispatch_table_no_arg: list of functions/None
    # 'dispatch_table_w_arg: list of functions/None 
    # Currently, they are always setup in pyopcode.py
    # but it could be a custom table.

    def dispatch(self, ec):
        while True:
            self.last_instr = intmask(self.next_instr)
            ec.bytecode_trace(self)
            self.next_instr = self.last_instr
            opcode = self.nextop()
            if self.space.config.objspace.logbytecodes:
                self.space.bytecodecounts[opcode] = self.space.bytecodecounts.get(opcode, 0) + 1
            if opcode >= pythonopcode.HAVE_ARGUMENT:
                oparg = self.nextarg()
                while True:
                    if opcode == pythonopcode.EXTENDED_ARG:
                        opcode = self.nextop()
                        oparg = oparg<<16 | self.nextarg()
                        if opcode < pythonopcode.HAVE_ARGUMENT:
                            raise pyframe.BytecodeCorruption
                        continue
                    else:
                        fn = self.dispatch_table_w_arg[opcode]
                        fn(self, oparg)                    
                    break
            else:
                fn = self.dispatch_table_no_arg[opcode] 
                fn(self)

    def nextop(self):
        c = self.pycode.co_code[self.next_instr]
        self.next_instr += 1
        return ord(c)

    def nextarg(self):
        lo = self.nextop()
        hi = self.nextop()
        return (hi<<8) + lo

    ### accessor functions ###

    def getlocalvarname(self, index):
        return self.pycode.co_varnames[index]

    def getconstant_w(self, index):
        return self.pycode.co_consts_w[index]

    def getname_u(self, index):
        return self.space.str_w(self.pycode.co_names_w[index])

    def getname_w(self, index):
        return self.pycode.co_names_w[index]


    ################################################################
    ##  Implementation of the "operational" opcodes
    ##  See also pyfastscope.py and pynestedscope.py for the rest.
    ##
    
    #  the 'self' argument of opcode implementations is called 'f'
    #  for historical reasons

    def NOP(f):
        pass

    def LOAD_FAST(f, varindex):
        # access a local variable directly
        w_value = f.fastlocals_w[varindex]
        if w_value is None:
            varname = f.getlocalvarname(varindex)
            message = "local variable '%s' referenced before assignment" % varname
            raise OperationError(f.space.w_UnboundLocalError, f.space.wrap(message))
        f.valuestack.push(w_value)

    def LOAD_CONST(f, constindex):
        w_const = f.getconstant_w(constindex)
        f.valuestack.push(w_const)

    def STORE_FAST(f, varindex):
        w_newvalue = f.valuestack.pop()
        f.fastlocals_w[varindex] = w_newvalue
        #except:
        #    print "exception: got index error"
        #    print " varindex:", varindex
        #    print " len(locals_w)", len(f.locals_w)
        #    import dis
        #    print dis.dis(f.pycode)
        #    print "co_varnames", f.pycode.co_varnames
        #    print "co_nlocals", f.pycode.co_nlocals
        #    raise

    def POP_TOP(f):
        f.valuestack.pop()

    def ROT_TWO(f):
        w_1 = f.valuestack.pop()
        w_2 = f.valuestack.pop()
        f.valuestack.push(w_1)
        f.valuestack.push(w_2)

    def ROT_THREE(f):
        w_1 = f.valuestack.pop()
        w_2 = f.valuestack.pop()
        w_3 = f.valuestack.pop()
        f.valuestack.push(w_1)
        f.valuestack.push(w_3)
        f.valuestack.push(w_2)

    def ROT_FOUR(f):
        w_1 = f.valuestack.pop()
        w_2 = f.valuestack.pop()
        w_3 = f.valuestack.pop()
        w_4 = f.valuestack.pop()
        f.valuestack.push(w_1)
        f.valuestack.push(w_4)
        f.valuestack.push(w_3)
        f.valuestack.push(w_2)

    def DUP_TOP(f):
        w_1 = f.valuestack.top()
        f.valuestack.push(w_1)

    def DUP_TOPX(f, itemcount):
        assert 1 <= itemcount <= 5, "limitation of the current interpreter"
        for i in range(itemcount):
            w_1 = f.valuestack.top(itemcount-1)
            f.valuestack.push(w_1)

    UNARY_POSITIVE = unaryoperation("pos")
    UNARY_NEGATIVE = unaryoperation("neg")
    UNARY_NOT      = unaryoperation("not_")
    UNARY_CONVERT  = unaryoperation("repr")
    UNARY_INVERT   = unaryoperation("invert")

    def BINARY_POWER(f):
        w_2 = f.valuestack.pop()
        w_1 = f.valuestack.pop()
        w_result = f.space.pow(w_1, w_2, f.space.w_None)
        f.valuestack.push(w_result)

    BINARY_MULTIPLY = binaryoperation("mul")
    BINARY_TRUE_DIVIDE  = binaryoperation("truediv")
    BINARY_FLOOR_DIVIDE = binaryoperation("floordiv")
    BINARY_DIVIDE       = binaryoperation("div")
    # XXX BINARY_DIVIDE must fall back to BINARY_TRUE_DIVIDE with -Qnew
    BINARY_MODULO       = binaryoperation("mod")
    BINARY_ADD      = binaryoperation("add")
    BINARY_SUBTRACT = binaryoperation("sub")
    BINARY_SUBSCR   = binaryoperation("getitem")
    BINARY_LSHIFT   = binaryoperation("lshift")
    BINARY_RSHIFT   = binaryoperation("rshift")
    BINARY_AND = binaryoperation("and_")
    BINARY_XOR = binaryoperation("xor")
    BINARY_OR  = binaryoperation("or_")

    def INPLACE_POWER(f):
        w_2 = f.valuestack.pop()
        w_1 = f.valuestack.pop()
        w_result = f.space.inplace_pow(w_1, w_2)
        f.valuestack.push(w_result)

    INPLACE_MULTIPLY = binaryoperation("inplace_mul")
    INPLACE_TRUE_DIVIDE  = binaryoperation("inplace_truediv")
    INPLACE_FLOOR_DIVIDE = binaryoperation("inplace_floordiv")
    INPLACE_DIVIDE       = binaryoperation("inplace_div")
    # XXX INPLACE_DIVIDE must fall back to INPLACE_TRUE_DIVIDE with -Qnew
    INPLACE_MODULO       = binaryoperation("inplace_mod")
    INPLACE_ADD      = binaryoperation("inplace_add")
    INPLACE_SUBTRACT = binaryoperation("inplace_sub")
    INPLACE_LSHIFT   = binaryoperation("inplace_lshift")
    INPLACE_RSHIFT   = binaryoperation("inplace_rshift")
    INPLACE_AND = binaryoperation("inplace_and")
    INPLACE_XOR = binaryoperation("inplace_xor")
    INPLACE_OR  = binaryoperation("inplace_or")

    def slice(f, w_start, w_end):
        w_obj = f.valuestack.pop()
        w_result = f.space.getslice(w_obj, w_start, w_end)
        f.valuestack.push(w_result)

    def SLICE_0(f):
        f.slice(f.space.w_None, f.space.w_None)

    def SLICE_1(f):
        w_start = f.valuestack.pop()
        f.slice(w_start, f.space.w_None)

    def SLICE_2(f):
        w_end = f.valuestack.pop()
        f.slice(f.space.w_None, w_end)

    def SLICE_3(f):
        w_end = f.valuestack.pop()
        w_start = f.valuestack.pop()
        f.slice(w_start, w_end)

    def storeslice(f, w_start, w_end):
        w_obj = f.valuestack.pop()
        w_newvalue = f.valuestack.pop()
        f.space.setslice(w_obj, w_start, w_end, w_newvalue)

    def STORE_SLICE_0(f):
        f.storeslice(f.space.w_None, f.space.w_None)

    def STORE_SLICE_1(f):
        w_start = f.valuestack.pop()
        f.storeslice(w_start, f.space.w_None)

    def STORE_SLICE_2(f):
        w_end = f.valuestack.pop()
        f.storeslice(f.space.w_None, w_end)

    def STORE_SLICE_3(f):
        w_end = f.valuestack.pop()
        w_start = f.valuestack.pop()
        f.storeslice(w_start, w_end)

    def deleteslice(f, w_start, w_end):
        w_obj = f.valuestack.pop()
        f.space.delslice(w_obj, w_start, w_end)

    def DELETE_SLICE_0(f):
        f.deleteslice(f.space.w_None, f.space.w_None)

    def DELETE_SLICE_1(f):
        w_start = f.valuestack.pop()
        f.deleteslice(w_start, f.space.w_None)

    def DELETE_SLICE_2(f):
        w_end = f.valuestack.pop()
        f.deleteslice(f.space.w_None, w_end)

    def DELETE_SLICE_3(f):
        w_end = f.valuestack.pop()
        w_start = f.valuestack.pop()
        f.deleteslice(w_start, w_end)

    def STORE_SUBSCR(f):
        "obj[subscr] = newvalue"
        w_subscr = f.valuestack.pop()
        w_obj = f.valuestack.pop()
        w_newvalue = f.valuestack.pop()
        f.space.setitem(w_obj, w_subscr, w_newvalue)

    def DELETE_SUBSCR(f):
        "del obj[subscr]"
        w_subscr = f.valuestack.pop()
        w_obj = f.valuestack.pop()
        f.space.delitem(w_obj, w_subscr)

    def PRINT_EXPR(f):
        w_expr = f.valuestack.pop()
        print_expr(f.space, w_expr)

    def PRINT_ITEM_TO(f):
        w_stream = f.valuestack.pop()
        w_item = f.valuestack.pop()
        if f.space.is_w(w_stream, f.space.w_None):
            w_stream = sys_stdout(f.space)   # grumble grumble special cases
        print_item_to(f.space, w_item, w_stream)

    def PRINT_ITEM(f):
        w_item = f.valuestack.pop()
        print_item(f.space, w_item)

    def PRINT_NEWLINE_TO(f):
        w_stream = f.valuestack.pop()
        if f.space.is_w(w_stream, f.space.w_None):
            w_stream = sys_stdout(f.space)   # grumble grumble special cases
        print_newline_to(f.space, w_stream)

    def PRINT_NEWLINE(f):
        print_newline(f.space)

    def BREAK_LOOP(f):
        raise pyframe.SBreakLoop

    def CONTINUE_LOOP(f, startofloop):
        raise pyframe.SContinueLoop(startofloop)

    def RAISE_VARARGS(f, nbargs):
        # we use the .app.py file to prepare the exception/value/traceback
        # but not to actually raise it, because we cannot use the 'raise'
        # statement to implement RAISE_VARARGS
        space = f.space
        if nbargs == 0:
            operror = space.getexecutioncontext().sys_exc_info()
            if operror is None:
                raise OperationError(space.w_TypeError,
                    space.wrap("raise: no active exception to re-raise"))
            # re-raise, no new traceback obj will be attached
            raise pyframe.SApplicationException(operror)
        w_value = w_traceback = space.w_None
        if nbargs >= 3: w_traceback = f.valuestack.pop()
        if nbargs >= 2: w_value     = f.valuestack.pop()
        if 1:           w_type      = f.valuestack.pop()
        operror = OperationError(w_type, w_value)
        operror.normalize_exception(space)
        if not space.full_exceptions or space.is_w(w_traceback, space.w_None):
            # common case
            raise operror
        else:
            tb = space.interpclass_w(w_traceback)
            if tb is None or not space.is_true(space.isinstance(tb, 
                space.gettypeobject(pytraceback.PyTraceback.typedef))):
                raise OperationError(space.w_TypeError,
                      space.wrap("raise: arg 3 must be a traceback or None"))
            operror.application_traceback = tb
            # re-raise, no new traceback obj will be attached
            raise pyframe.SApplicationException(operror) 

    def LOAD_LOCALS(f):
        f.valuestack.push(f.w_locals)

    def RETURN_VALUE(f):
        w_returnvalue = f.valuestack.pop()
        raise pyframe.SReturnValue(w_returnvalue)

    def EXEC_STMT(f):
        w_locals  = f.valuestack.pop()
        w_globals = f.valuestack.pop()
        w_prog    = f.valuestack.pop()
        flags = f.space.getexecutioncontext().compiler.getcodeflags(f.pycode)
        w_compile_flags = f.space.wrap(flags)
        w_resulttuple = prepare_exec(f.space, f.space.wrap(f), w_prog,
                                     w_globals, w_locals,
                                     w_compile_flags, f.space.wrap(f.builtin),
                                     f.space.gettypeobject(PyCode.typedef))
        w_prog, w_globals, w_locals = f.space.unpacktuple(w_resulttuple, 3)

        plain = f.w_locals is not None and f.space.is_w(w_locals, f.w_locals)
        if plain:
            w_locals = f.getdictscope()
        co = f.space.interp_w(eval.Code, w_prog)
        co.exec_code(f.space, w_globals, w_locals)
        if plain:
            f.setdictscope(w_locals)

    def POP_BLOCK(f):
        block = f.blockstack.pop()
        block.cleanup(f)  # the block knows how to clean up the value stack

    def END_FINALLY(f):
        # unlike CPython, when we reach this opcode the value stack has
        # always been set up as follows (topmost first):
        #   [exception type  or None]
        #   [exception value or None]
        #   [wrapped stack unroller ]
        f.valuestack.pop()   # ignore the exception type
        f.valuestack.pop()   # ignore the exception value
        w_unroller = f.valuestack.pop()
        unroller = f.space.interpclass_w(w_unroller)
        if isinstance(unroller, pyframe.SuspendedUnroller):
            # re-raise the unroller, if any
            raise unroller.flowexc

    def BUILD_CLASS(f):
        w_methodsdict = f.valuestack.pop()
        w_bases       = f.valuestack.pop()
        w_name        = f.valuestack.pop()
        w_metaclass = find_metaclass(f.space, w_bases,
                                     w_methodsdict, f.w_globals,
                                     f.space.wrap(f.builtin)) 
        w_newclass = f.space.call_function(w_metaclass, w_name,
                                           w_bases, w_methodsdict)
        f.valuestack.push(w_newclass)

    def STORE_NAME(f, varindex):
        w_varname = f.getname_w(varindex)
        w_newvalue = f.valuestack.pop()
        f.space.set_str_keyed_item(f.w_locals, w_varname, w_newvalue)

    def DELETE_NAME(f, varindex):
        w_varname = f.getname_w(varindex)
        try:
            f.space.delitem(f.w_locals, w_varname)
        except OperationError, e:
            # catch KeyErrors and turn them into NameErrors
            if not e.match(f.space, f.space.w_KeyError):
                raise
            message = "name '%s' is not defined" % f.space.str_w(w_varname)
            raise OperationError(f.space.w_NameError, f.space.wrap(message))

    def UNPACK_SEQUENCE(f, itemcount):
        w_iterable = f.valuestack.pop()
        try:
            items = f.space.unpackiterable(w_iterable, itemcount)
        except UnpackValueError, e:
            raise OperationError(f.space.w_ValueError, f.space.wrap(e.msg))
        items.reverse()
        for item in items:
            f.valuestack.push(item)

    def STORE_ATTR(f, nameindex):
        "obj.attributename = newvalue"
        w_attributename = f.getname_w(nameindex)
        w_obj = f.valuestack.pop()
        w_newvalue = f.valuestack.pop()
        f.space.setattr(w_obj, w_attributename, w_newvalue)

    def DELETE_ATTR(f, nameindex):
        "del obj.attributename"
        w_attributename = f.getname_w(nameindex)
        w_obj = f.valuestack.pop()
        f.space.delattr(w_obj, w_attributename)

    def STORE_GLOBAL(f, nameindex):
        w_varname = f.getname_w(nameindex)
        w_newvalue = f.valuestack.pop()
        f.space.set_str_keyed_item(f.w_globals, w_varname, w_newvalue)

    def DELETE_GLOBAL(f, nameindex):
        w_varname = f.getname_w(nameindex)
        f.space.delitem(f.w_globals, w_varname)

    def LOAD_NAME(f, nameindex):
        if f.w_locals is not f.w_globals:
            w_varname = f.getname_w(nameindex)
            w_value = f.space.finditem(f.w_locals, w_varname)
            if w_value is not None:
                f.valuestack.push(w_value)
                return
        f.LOAD_GLOBAL(nameindex)    # fall-back

    def LOAD_GLOBAL(f, nameindex):
        w_varname = f.getname_w(nameindex)
        w_value = f.space.finditem(f.w_globals, w_varname)
        if w_value is None:
            # not in the globals, now look in the built-ins
            w_value = f.builtin.getdictvalue(f.space, w_varname)
            if w_value is None:
                varname = f.getname_u(nameindex)
                message = "global name '%s' is not defined" % varname
                raise OperationError(f.space.w_NameError,
                                     f.space.wrap(message))
        f.valuestack.push(w_value)

    def DELETE_FAST(f, varindex):
        if f.fastlocals_w[varindex] is None:
            varname = f.getlocalvarname(varindex)
            message = "local variable '%s' referenced before assignment" % varname
            raise OperationError(f.space.w_UnboundLocalError, f.space.wrap(message))
        f.fastlocals_w[varindex] = None
        

    def BUILD_TUPLE(f, itemcount):
        items = [f.valuestack.pop() for i in range(itemcount)]
        items.reverse()
        w_tuple = f.space.newtuple(items)
        f.valuestack.push(w_tuple)

    def BUILD_LIST(f, itemcount):
        items = [f.valuestack.pop() for i in range(itemcount)]
        items.reverse()
        w_list = f.space.newlist(items)
        f.valuestack.push(w_list)

    def BUILD_MAP(f, zero):
        if zero != 0:
            raise pyframe.BytecodeCorruption
        w_dict = f.space.newdict()
        f.valuestack.push(w_dict)

    def LOAD_ATTR(f, nameindex):
        "obj.attributename"
        w_attributename = f.getname_w(nameindex)
        w_obj = f.valuestack.pop()
        w_value = f.space.getattr(w_obj, w_attributename)
        f.valuestack.push(w_value)

    def cmp_lt(f, w_1, w_2):  return f.space.lt(w_1, w_2)
    def cmp_le(f, w_1, w_2):  return f.space.le(w_1, w_2)
    def cmp_eq(f, w_1, w_2):  return f.space.eq(w_1, w_2)
    def cmp_ne(f, w_1, w_2):  return f.space.ne(w_1, w_2)
    def cmp_gt(f, w_1, w_2):  return f.space.gt(w_1, w_2)
    def cmp_ge(f, w_1, w_2):  return f.space.ge(w_1, w_2)

    def cmp_in(f, w_1, w_2):
        return f.space.contains(w_2, w_1)
    def cmp_not_in(f, w_1, w_2):
        return f.space.not_(f.space.contains(w_2, w_1))
    def cmp_is(f, w_1, w_2):
        return f.space.is_(w_1, w_2)
    def cmp_is_not(f, w_1, w_2):
        return f.space.not_(f.space.is_(w_1, w_2))
    def cmp_exc_match(f, w_1, w_2):
        return f.space.newbool(f.space.exception_match(w_1, w_2))

    compare_dispatch_table = [
        cmp_lt,   # "<"
        cmp_le,   # "<="
        cmp_eq,   # "=="
        cmp_ne,   # "!="
        cmp_gt,   # ">"
        cmp_ge,   # ">="
        cmp_in,
        cmp_not_in,
        cmp_is,
        cmp_is_not,
        cmp_exc_match,
        ]
    def COMPARE_OP(f, testnum):
        w_2 = f.valuestack.pop()
        w_1 = f.valuestack.pop()
        try:
            testfn = f.compare_dispatch_table[testnum]
        except IndexError:
            raise pyframe.BytecodeCorruption, "bad COMPARE_OP oparg"
        w_result = testfn(f, w_1, w_2)
        f.valuestack.push(w_result)

    def IMPORT_NAME(f, nameindex):
        space = f.space
        w_modulename = f.getname_w(nameindex)
        modulename = f.space.str_w(w_modulename)
        w_fromlist = f.valuestack.pop()
        w_import = f.builtin.getdictvalue_w(f.space, '__import__')
        if w_import is None:
            raise OperationError(space.w_ImportError,
                                 space.wrap("__import__ not found"))
        w_locals = f.w_locals
        if w_locals is None:            # CPython does this
            w_locals = space.w_None
        w_obj = space.call_function(w_import, space.wrap(modulename),
                                    f.w_globals, w_locals, w_fromlist)
        f.valuestack.push(w_obj)

    def IMPORT_STAR(f):
        w_module = f.valuestack.pop()
        w_locals = f.getdictscope()
        import_all_from(f.space, w_module, w_locals)
        f.setdictscope(w_locals)

    def IMPORT_FROM(f, nameindex):
        w_name = f.getname_w(nameindex)
        w_module = f.valuestack.top()
        try:
            w_obj = f.space.getattr(w_module, w_name)
        except OperationError, e:
            if not e.match(f.space, f.space.w_AttributeError):
                raise
            raise OperationError(f.space.w_ImportError,
                             f.space.wrap("cannot import name '%s'" % f.space.str_w(w_name) ))
        f.valuestack.push(w_obj)

    def JUMP_FORWARD(f, stepby):
        f.next_instr += stepby

    def JUMP_IF_FALSE(f, stepby):
        w_cond = f.valuestack.top()
        if not f.space.is_true(w_cond):
            f.next_instr += stepby

    def JUMP_IF_TRUE(f, stepby):
        w_cond = f.valuestack.top()
        if f.space.is_true(w_cond):
            f.next_instr += stepby

    def JUMP_ABSOLUTE(f, jumpto):
        f.next_instr = jumpto

    def GET_ITER(f):
        w_iterable = f.valuestack.pop()
        w_iterator = f.space.iter(w_iterable)
        f.valuestack.push(w_iterator)

    def FOR_ITER(f, jumpby):
        w_iterator = f.valuestack.top()
        try:
            w_nextitem = f.space.next(w_iterator)
        except OperationError, e:
            if not e.match(f.space, f.space.w_StopIteration):
                raise 
            # iterator exhausted
            f.valuestack.pop()
            f.next_instr += jumpby
        else:
            f.valuestack.push(w_nextitem)

    def FOR_LOOP(f, oparg):
        raise pyframe.BytecodeCorruption, "old opcode, no longer in use"

    def SETUP_LOOP(f, offsettoend):
        block = pyframe.LoopBlock(f, f.next_instr + offsettoend)
        f.blockstack.push(block)

    def SETUP_EXCEPT(f, offsettoend):
        block = pyframe.ExceptBlock(f, f.next_instr + offsettoend)
        f.blockstack.push(block)

    def SETUP_FINALLY(f, offsettoend):
        block = pyframe.FinallyBlock(f, f.next_instr + offsettoend)
        f.blockstack.push(block)

    def WITH_CLEANUP(f):
        # see comment in END_FINALLY for stack state
        w_exitfunc = f.valuestack.pop()
        w_unroller = f.valuestack.top(2)
        unroller = f.space.interpclass_w(w_unroller)
        if (isinstance(unroller, pyframe.SuspendedUnroller)
            and isinstance(unroller.flowexc, pyframe.SApplicationException)):
            operr = unroller.flowexc.operr
            w_result = f.space.call_function(w_exitfunc,
                                             operr.w_type,
                                             operr.w_value,
                                             operr.application_traceback)
            if f.space.is_true(w_result):
                # __exit__() returned True -> Swallow the exception.
                f.valuestack.set_top(f.space.w_None, 2)
        else:
            f.space.call_function(w_exitfunc,
                                  f.space.w_None,
                                  f.space.w_None,
                                  f.space.w_None)
                      
    def call_function(f, oparg, w_star=None, w_starstar=None):
        n_arguments = oparg & 0xff
        n_keywords = (oparg>>8) & 0xff
        keywords = None
        if n_keywords:
            keywords = {}
            for i in range(n_keywords):
                w_value = f.valuestack.pop()
                w_key   = f.valuestack.pop()
                key = f.space.str_w(w_key)
                keywords[key] = w_value
        arguments = [None] * n_arguments
        for i in range(n_arguments - 1, -1, -1):
            arguments[i] = f.valuestack.pop()
        args = Arguments(f.space, arguments, keywords, w_star, w_starstar)
        w_function  = f.valuestack.pop()
        w_result = f.space.call_args(w_function, args)
        rstack.resume_point("call_function", f, returns=w_result)
        f.valuestack.push(w_result)
        
    def CALL_FUNCTION(f, oparg):
        # XXX start of hack for performance
        if (oparg >> 8) & 0xff == 0:
            # Only positional arguments
            nargs = oparg & 0xff
            w_function = f.valuestack.top(nargs)
            try:
                w_result = f.space.call_valuestack(w_function, nargs, f.valuestack)
                rstack.resume_point("CALL_FUNCTION", f, nargs, returns=w_result)
            finally:
                f.valuestack.drop(nargs + 1)
            f.valuestack.push(w_result)
        # XXX end of hack for performance
        else:
            # general case
            f.call_function(oparg)

    def CALL_FUNCTION_VAR(f, oparg):
        w_varargs = f.valuestack.pop()
        f.call_function(oparg, w_varargs)

    def CALL_FUNCTION_KW(f, oparg):
        w_varkw = f.valuestack.pop()
        f.call_function(oparg, None, w_varkw)

    def CALL_FUNCTION_VAR_KW(f, oparg):
        w_varkw = f.valuestack.pop()
        w_varargs = f.valuestack.pop()
        f.call_function(oparg, w_varargs, w_varkw)

    def MAKE_FUNCTION(f, numdefaults):
        w_codeobj = f.valuestack.pop()
        codeobj = f.space.interp_w(PyCode, w_codeobj)
        defaultarguments = [f.valuestack.pop() for i in range(numdefaults)]
        defaultarguments.reverse()
        fn = function.Function(f.space, codeobj, f.w_globals, defaultarguments)
        f.valuestack.push(f.space.wrap(fn))

    def BUILD_SLICE(f, numargs):
        if numargs == 3:
            w_step = f.valuestack.pop()
        elif numargs == 2:
            w_step = f.space.w_None
        else:
            raise pyframe.BytecodeCorruption
        w_end   = f.valuestack.pop()
        w_start = f.valuestack.pop()
        w_slice = f.space.newslice(w_start, w_end, w_step)
        f.valuestack.push(w_slice)

    def LIST_APPEND(f):
        w = f.valuestack.pop()
        v = f.valuestack.pop()
        f.space.call_method(v, 'append', w)

    def SET_LINENO(f, lineno):
        pass

##     def EXTENDED_ARG(f, oparg):
##         opcode = f.nextop()
##         oparg = oparg<<16 | f.nextarg()
##         fn = f.dispatch_table_w_arg[opcode]
##         if fn is None:
##             raise pyframe.BytecodeCorruption
##         fn(f, oparg)

    def MISSING_OPCODE(f):
        ofs = f.next_instr - 1
        c = f.pycode.co_code[ofs]
        name = f.pycode.co_name
        raise pyframe.BytecodeCorruption("unknown opcode, ofs=%d, code=%d, name=%s" %
                                           (ofs, ord(c), name) )

    def MISSING_OPCODE_W_ARG(f, oparg):
        ofs = f.next_instr - 3
        c = f.pycode.co_code[ofs]
        name = f.pycode.co_name
        raise pyframe.BytecodeCorruption("unknown opcode, ofs=%d, code=%d, name=%s" %
                                           (ofs, ord(c), name) )

    STOP_CODE = MISSING_OPCODE

    ### dispatch_table ###

    # 'opcode_has_arg' is a class attribute: list of True/False whether opcode takes arg
    # 'dispatch_table_no_arg: list of functions/None
    # 'dispatch_table_w_arg: list of functions/None

    __metaclass__ = InitializedClass
    def __initclass__(cls):
        "NOT_RPYTHON"
        # create the 'cls.dispatch_table' attribute
        opcode_has_arg = []
        dispatch_table_no_arg = []
        dispatch_table_w_arg = []
        missing_opcode = cls.MISSING_OPCODE.im_func
        missing_opcode_w_arg = cls.MISSING_OPCODE_W_ARG.im_func
        for i in range(256):
            opname = pythonopcode.opname[i].replace('+', '_')
            fn = getattr(cls, opname, None)
            fn = getattr(fn, 'im_func',fn)
            has_arg = i >= pythonopcode.HAVE_ARGUMENT
            #if fn is missing_opcode and not opname.startswith('<') and i>0:
            #    import warnings
            #    warnings.warn("* Warning, missing opcode %s" % opname)
            opcode_has_arg.append(has_arg)
            if has_arg:
                fn = fn or missing_opcode_w_arg
                dispatch_table_w_arg.append(fn)
                dispatch_table_no_arg.append(None)
            else:
                fn = fn or missing_opcode
                dispatch_table_no_arg.append(fn)
                dispatch_table_w_arg.append(None)

        cls.opcode_has_arg = opcode_has_arg
        cls.dispatch_table_no_arg = dispatch_table_no_arg
        cls.dispatch_table_w_arg = dispatch_table_w_arg

        #XXX performance hack!
        ### Create dispatch with a lot of if,elifs ###
        ### (this gets optimized for translated pypy by the merge_if_blocks transformation) ###
        if cls.__name__ != 'PyInterpFrame':
            return
        import py
        
        dispatch_code  = '''
def dispatch_translated(self, ec):
    code = self.pycode.co_code
    while True:
        self.last_instr = intmask(self.next_instr)
        ec.bytecode_trace(self)
        self.next_instr = self.last_instr
        opcode = ord(code[self.next_instr])
        if self.space.config.objspace.logbytecodes:
            self.space.bytecodecounts[opcode] = self.space.bytecodecounts.get(opcode, 0) + 1
        self.next_instr += 1
        if opcode >= %s:
            oparg = ord(code[self.next_instr]) | ord(code[self.next_instr + 1]) << 8
            self.next_instr += 2
            while True:
                if opcode == %s:
                    opcode = ord(code[self.next_instr])
                    oparg = oparg << 16 | ord(code[self.next_instr + 1]) | ord(code[self.next_instr + 2]) << 8
                    self.next_instr += 3
                    if opcode < %s:
                        raise pyframe.BytecodeCorruption
                    continue
''' % (pythonopcode.HAVE_ARGUMENT,
        pythonopcode.EXTENDED_ARG,
        pythonopcode.HAVE_ARGUMENT)

        def sortkey(opcode, opcodeorder=opcodeorder, ValueError=ValueError):
            try:
                index = opcodeorder.index(opcode)
            except ValueError:
                index = 1000000
            return index, opcode
        opcases = [(sortkey(i), i, opname)
                   for opname, i in pythonopcode.opmap.iteritems()]
        opcases.sort()    # for predictable results

        for _, i, opname in opcases:
            if i == pythonopcode.EXTENDED_ARG or i < pythonopcode.HAVE_ARGUMENT:
                continue
            opname         = opname.replace('+', '_')
            dispatch_code += '                elif opcode == %d:\n' % i
            dispatch_code += '                    self.%s(oparg)\n'  % opname
            if opname == 'CALL_FUNCTION':
                dispatch_code += '                    rstack.resume_point("dispatch_call", self, code, ec)\n'
        dispatch_code +=     '                else:\n'
        dispatch_code +=     '                    self.MISSING_OPCODE_W_ARG(oparg)\n'
        dispatch_code +=     '                break\n'

        for _, i, opname in opcases:
            if i >= pythonopcode.HAVE_ARGUMENT:
                continue
            opname         = opname.replace('+', '_')
            dispatch_code += '        elif opcode == %d:\n' % i
            dispatch_code += '            self.%s()\n'  % opname
        dispatch_code +=     '        else:\n'
        dispatch_code +=     '            self.MISSING_OPCODE()\n'
        exec py.code.Source(dispatch_code).compile()

        cls.dispatch_translated = dispatch_translated        
    

### helpers written at the application-level ###
# Some of these functions are expected to be generally useful if other
# parts of the code need to do the same thing as a non-trivial opcode,
# like finding out which metaclass a new class should have.
# This is why they are not methods of PyInterpFrame.
# There are also a couple of helpers that are methods, defined in the
# class above.

app = gateway.applevel(r'''
    """ applevel implementation of certain system properties, imports
    and other helpers"""
    import sys
    
    def sys_stdout():
        try: 
            return sys.stdout
        except AttributeError:
            raise RuntimeError("lost sys.stdout")

    def print_expr(obj):
        try:
            displayhook = sys.displayhook
        except AttributeError:
            raise RuntimeError("lost sys.displayhook")
        displayhook(obj)

    def print_item_to(x, stream):
        if file_softspace(stream, False):
           stream.write(" ")
        stream.write(str(x))

        # add a softspace unless we just printed a string which ends in a '\t'
        # or '\n' -- or more generally any whitespace character but ' '
        if isinstance(x, str) and x and x[-1].isspace() and x[-1]!=' ':
            return 
        # XXX add unicode handling
        file_softspace(stream, True)
    print_item_to._annspecialcase_ = "specialize:argtype(0)"

    def print_item(x):
        print_item_to(x, sys_stdout())
    print_item._annspecialcase_ = "flowspace:print_item"

    def print_newline_to(stream):
        stream.write("\n")
        file_softspace(stream, False)

    def print_newline():
        print_newline_to(sys_stdout())
    print_newline._annspecialcase_ = "flowspace:print_newline"

    def file_softspace(file, newflag):
        try:
            softspace = file.softspace
        except AttributeError:
            softspace = 0
        try:
            file.softspace = newflag
        except AttributeError:
            pass
        return softspace
''', filename=__file__)

sys_stdout      = app.interphook('sys_stdout')
print_expr      = app.interphook('print_expr')
print_item      = app.interphook('print_item')
print_item_to   = app.interphook('print_item_to')
print_newline   = app.interphook('print_newline')
print_newline_to= app.interphook('print_newline_to')
file_softspace  = app.interphook('file_softspace')

app = gateway.applevel(r'''
    def find_metaclass(bases, namespace, globals, builtin):
        if '__metaclass__' in namespace:
            return namespace['__metaclass__']
        elif len(bases) > 0:
            base = bases[0]
            if hasattr(base, '__class__'):
                return base.__class__
            else:
                return type(base)
        elif '__metaclass__' in globals:
            return globals['__metaclass__']
        else: 
            try: 
                return builtin.__metaclass__ 
            except AttributeError: 
                return type
''', filename=__file__)

find_metaclass  = app.interphook('find_metaclass')

app = gateway.applevel(r'''
    def import_all_from(module, into_locals):
        try:
            all = module.__all__
        except AttributeError:
            try:
                dict = module.__dict__
            except AttributeError:
                raise ImportError("from-import-* object has no __dict__ "
                                  "and no __all__")
            all = dict.keys()
            skip_leading_underscores = True
        else:
            skip_leading_underscores = False
        for name in all:
            if skip_leading_underscores and name[0]=='_':
                continue
            into_locals[name] = getattr(module, name)
''', filename=__file__)

import_all_from = app.interphook('import_all_from')

app = gateway.applevel(r'''
    def prepare_exec(f, prog, globals, locals, compile_flags, builtin, codetype):
        """Manipulate parameters to exec statement to (codeobject, dict, dict).
        """
        if (globals is None and locals is None and
            isinstance(prog, tuple) and
            (len(prog) == 2 or len(prog) == 3)):
            globals = prog[1]
            if len(prog) == 3:
                locals = prog[2]
            prog = prog[0]
        if globals is None:
            globals = f.f_globals
            if locals is None:
                locals = f.f_locals
        if locals is None:
            locals = globals

        if not isinstance(globals, dict):
            if not hasattr(globals, '__getitem__'):
                raise TypeError("exec: arg 2 must be a dictionary or None")
        try:
            globals['__builtins__']
        except KeyError:
            globals['__builtins__'] = builtin
        if not isinstance(locals, dict):
            if not hasattr(locals, '__getitem__'):
                raise TypeError("exec: arg 3 must be a dictionary or None")

        if not isinstance(prog, codetype):
            filename = '<string>'
            if not isinstance(prog, str):
                if isinstance(prog, basestring):
                    prog = str(prog)
                elif isinstance(prog, file):
                    filename = prog.name
                    prog = prog.read()
                else:
                    raise TypeError("exec: arg 1 must be a string, file, "
                                    "or code object")
            try:
                prog = compile(prog, filename, 'exec', compile_flags, 1)
            except SyntaxError, e: # exec SyntaxErrors have filename==None
               if len(e.args) == 2:
                   msg, loc = e.args
                   loc1 = (None,) + loc[1:]
                   e.args = msg, loc1
                   e.filename = None
               raise e
        return (prog, globals, locals)
''', filename=__file__)

prepare_exec    = app.interphook('prepare_exec')
