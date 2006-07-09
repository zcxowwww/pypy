from pypy.rpython.extregistry import ExtRegistryEntry
from pypy.rpython.lltypesystem import lltype, llmemory
from pypy.rpython.lltypesystem.lloperation import llop
from pypy.rpython.objectmodel import CDefinedIntSymbolic
from pypy.objspace.flow.model import Constant, Variable
from pypy.objspace.flow.model import FunctionGraph, Block, Link


class CPyTypeInterface(object):

    def __init__(self, name, objects={}):

        # the exported name of the type
        self.name = name

        # a dict {name: pyobjectptr()} for general class attributes
        # (not for special methods!)
        self.objects = objects.copy()

    def _freeze_(self):
        return True

    def emulate(self, original_class):
        "Build a type object that emulates 'self'."
        d = {'__slots__': [], '_rpython_class_': original_class}
        for name, value in self.objects.items():
            assert lltype.typeOf(value) == PyObjPtr
            assert isinstance(value._obj, lltype._pyobject)
            d[name] = value._obj.value
        t = type(self.name, (rpython_object,), d)
        return t


def cpy_export(cpytype, obj):
    raise NotImplementedError("only works in translated versions")

def cpy_import(rpytype, obj):
    raise NotImplementedError("only works in translated versions")

def cpy_typeobject(cpytype, cls):
    raise NotImplementedError("only works in translated versions")


# ____________________________________________________________
# Implementation

class Entry(ExtRegistryEntry):
    _about_ = cpy_export

    def compute_result_annotation(self, s_cpytype, s_obj):
        from pypy.annotation.model import SomeObject
        from pypy.annotation.model import SomeInstance
        assert isinstance(s_obj, SomeInstance)
        assert s_cpytype.is_constant()
        cpytype = s_cpytype.const
        attach_cpy_flavor(s_obj.classdef, cpytype)
        return SomeObject()

    def specialize_call(self, hop):
        from pypy.rpython.lltypesystem import lltype
        s_obj = hop.args_s[1]
        r_inst = hop.args_r[1]
        v_inst = hop.inputarg(r_inst, arg=1)
        return hop.genop('cast_pointer', [v_inst],
                         resulttype = lltype.Ptr(lltype.PyObject))


class Entry(ExtRegistryEntry):
    _about_ = cpy_import

    def compute_result_annotation(self, s_rpytype, s_obj):
        from pypy.annotation.bookkeeper import getbookkeeper
        from pypy.annotation.model import SomeInstance
        assert s_rpytype.is_constant()
        rpytype = s_rpytype.const
        bk = getbookkeeper()
        return SomeInstance(bk.getuniqueclassdef(rpytype))

    def specialize_call(self, hop):
        from pypy.annotation.model import SomeInstance
        from pypy.rpython.robject import pyobj_repr
        s_rpytype = hop.args_s[0]
        assert s_rpytype.is_constant()
        rpytype = s_rpytype.const
        classdef = hop.rtyper.annotator.bookkeeper.getuniqueclassdef(rpytype)
        s_inst = SomeInstance(classdef)
        r_inst = hop.rtyper.getrepr(s_inst)
        assert r_inst.lowleveltype.TO._gckind == 'cpy'
        v_obj = hop.inputarg(pyobj_repr, arg=1)
        return hop.genop('cast_pointer', [v_obj],
                         resulttype = r_inst.lowleveltype)


class Entry(ExtRegistryEntry):
    _about_ = cpy_typeobject

    def compute_result_annotation(self, s_cpytype, s_cls):
        from pypy.annotation.model import SomeObject
        assert s_cls.is_constant()
        assert s_cpytype.is_constant()
        cpytype = s_cpytype.const
        [classdesc] = s_cls.descriptions
        classdef = classdesc.getuniqueclassdef()
        attach_cpy_flavor(classdef, cpytype)
        return SomeObject()

    def specialize_call(self, hop):
        from pypy.rpython.rclass import getinstancerepr
        s_cls = hop.args_s[1]
        assert s_cls.is_constant()
        [classdesc] = s_cls.descriptions
        classdef = classdesc.getuniqueclassdef()
        r_inst = getinstancerepr(hop.rtyper, classdef)
        cpytype = build_pytypeobject(r_inst)
        return hop.inputconst(PyObjPtr, cpytype)


def attach_cpy_flavor(classdef, cpytype):
    for parentdef in classdef.getmro():
        if not hasattr(parentdef, '_cpy_exported_type_'):
            parentdef._cpy_exported_type_ = None
    if classdef._cpy_exported_type_ is None:
        classdef._cpy_exported_type_ = cpytype
    else:
        assert classdef._cpy_exported_type_ == cpytype


PyObjPtr = lltype.Ptr(lltype.PyObject)

PY_TYPE_OBJECT = lltype.PyForwardReference()
PY_TYPE_OBJECT.become(lltype.PyStruct(
    'PyTypeObject',
    ('head',           lltype.PyObject),
    ('c_ob_size',      lltype.Signed),
    ('c_tp_name',      lltype.Ptr(lltype.FixedSizeArray(lltype.Char, 1))),
    ('c_tp_basicsize', lltype.Signed),
    ('c_tp_itemsize',  lltype.Signed),
    ('c_tp_dealloc',   lltype.Ptr(lltype.FuncType([PyObjPtr],
                                                  lltype.Void))),
    ('c_tp_print',     lltype.Signed),
    ('c_tp_getattr',   lltype.Signed),
    ('c_tp_setattr',   lltype.Signed),   # in
    ('c_tp_compare',   lltype.Signed),
    ('c_tp_repr',      lltype.Signed),   # progress
    ('c_tp_as_number', lltype.Signed),
    ('c_tp_as_sequence',lltype.Signed),
    ('c_tp_as_mapping',lltype.Signed),
    ('c_tp_hash',      lltype.Signed),
    ('c_tp_call',      lltype.Signed),
    ('c_tp_str',       lltype.Signed),
    ('c_tp_getattro',  lltype.Signed),
    ('c_tp_setattro',  lltype.Signed),
    ('c_tp_as_buffer', lltype.Signed),
    ('c_tp_flags',     lltype.Signed),
    ('c_tp_doc',       lltype.Signed),
    ('c_tp_traverse',  lltype.Signed),
    ('c_tp_clear',     lltype.Signed),
    ('c_tp_richcompare',lltype.Signed),
    ('c_tp_weaklistoffset',lltype.Signed),
    ('c_tp_iter',      lltype.Signed),
    ('c_tp_iternext',  lltype.Signed),
    ('c_tp_methods',   lltype.Signed),
    ('c_tp_members',   lltype.Signed),
    ('c_tp_getset',    lltype.Signed),
    ('c_tp_base',      lltype.Signed),
    ('c_tp_dict',      PyObjPtr),
    ('c_tp_descr_get', lltype.Signed),
    ('c_tp_descr_set', lltype.Signed),
    ('c_tp_dictoffset',lltype.Signed),
    ('c_tp_init',      lltype.Signed),
    ('c_tp_alloc',     lltype.Signed),
                       #lltype.Ptr(lltype.FuncType([lltype.Ptr(PY_TYPE_OBJECT),
                       #                            lltype.Signed],
                       #                           PyObjPtr))),
    ('c_tp_new',       lltype.Ptr(lltype.FuncType([lltype.Ptr(PY_TYPE_OBJECT),
                                                   PyObjPtr,
                                                   PyObjPtr],
                                                  PyObjPtr))),
    ('c_tp_free',      lltype.Signed),
                       #lltype.Ptr(lltype.FuncType([llmemory.Address],
                       #                           lltype.Void))),

    hints={'c_name': '_typeobject', 'external': True, 'inline_head': True}))
# XXX 'c_name' should be 'PyTypeObject' but genc inserts 'struct' :-(

def ll_tp_dealloc(p):
    addr = llmemory.cast_ptr_to_adr(p)
    # Warning: this relies on an optimization in gctransformer, which will
    # not insert any incref/decref for 'p'.  That would lead to infinite
    # recursion, as the refcnt of 'p' is already zero!
    from pypy.rpython.lltypesystem.rclass import CPYOBJECT
    llop.gc_deallocate(lltype.Void, CPYOBJECT, addr)

def build_pytypeobject(r_inst):
    rtyper = r_inst.rtyper
    cache = rtyper.classdef_to_pytypeobject
    try:
        return cache[r_inst.classdef]
    except KeyError:
        for parentdef in r_inst.classdef.getmro():
            cpytype = parentdef._cpy_exported_type_
            if cpytype is not None:
                break
        else:
            # for classes that cannot be exported at all
            return lltype.nullptr(lltype.PyObject)

        from pypy.rpython.lltypesystem.rclass import CPYOBJECTPTR
        from pypy.rpython.rtyper import LowLevelOpList
        typetype = lltype.pyobjectptr(type)

        # make the graph of tp_new manually    
        v1 = Variable('tp');   v1.concretetype = lltype.Ptr(PY_TYPE_OBJECT)
        v2 = Variable('args'); v2.concretetype = PyObjPtr
        v3 = Variable('kwds'); v3.concretetype = PyObjPtr
        block = Block([v1, v2, v3])
        llops = LowLevelOpList(None)
        v4 = r_inst.new_instance(llops, v_cpytype = v1)
        v5 = llops.genop('cast_pointer', [v4], resulttype = PyObjPtr)
        block.operations = list(llops)
        tp_new_graph = FunctionGraph('ll_tp_new', block)
        block.closeblock(Link([v5], tp_new_graph.returnblock))
        tp_new_graph.getreturnvar().concretetype = v5.concretetype

        # build the PyTypeObject structure
        pytypeobj = lltype.malloc(PY_TYPE_OBJECT, flavor='cpy',
                                  extra_args=(typetype,))
        name = cpytype.name
        T = lltype.FixedSizeArray(lltype.Char, len(name)+1)
        p = lltype.malloc(T, immortal=True)
        for i in range(len(name)):
            p[i] = name[i]
        p[len(name)] = '\x00'
        pytypeobj.c_tp_name = lltype.direct_arrayitems(p)
        pytypeobj.c_tp_basicsize = llmemory.sizeof(r_inst.lowleveltype.TO)
        pytypeobj.c_tp_flags = CDefinedIntSymbolic('Py_TPFLAGS_DEFAULT')
        pytypeobj.c_tp_new = rtyper.type_system.getcallable(tp_new_graph)
        pytypeobj.c_tp_dealloc = rtyper.annotate_helper_fn(ll_tp_dealloc,
                                                           [PyObjPtr])
        result =  lltype.cast_pointer(PyObjPtr, pytypeobj)

        # the llsetup function that will store the 'objects' into the
        # type's tp_dict
        if cpytype.objects:
            objects = [(lltype.pyobjectptr(name), value)
                       for name, value in cpytype.objects.items()]

            def ll_type_setup(p):
                tp = lltype.cast_pointer(lltype.Ptr(PY_TYPE_OBJECT), p)
                tp_dict = tp.c_tp_dict
                for name, value in objects:
                    llop.setitem(PyObjPtr, tp_dict, name, value)
            result._obj.setup_fnptr = rtyper.annotate_helper_fn(ll_type_setup,
                                                                [PyObjPtr])

        cache[r_inst.classdef] = result
        return result

# To make this a Py_TPFLAGS_BASETYPE, we need to have a tp_new that does
# something different for subclasses: it needs to allocate a bit more
# for CPython's GC (see PyObject_GC_Malloc); it needs to Py_INCREF the
# type if it's a heap type; and it needs to PyObject_GC_Track() the object.
# Also, tp_dealloc needs to untrack the object.


# ____________________________________________________________
# Emulation support, to have user-defined classes and instances
# work nicely on top of CPython running the CPyObjSpace

class rpython_meta(type):
    pass

class rpython_object(object):
    "NOT_RPYTHON"
    __metaclass__ = rpython_meta
    __slots__ = ('data',)
rpython_data = rpython_object.data
del rpython_object.data

def init_rpython_data(w_object, value):
    "NOT_RPYTHON"
    rpython_data.__set__(w_object.value, value)
    value.__cpy_wrapper__ = w_object

def get_rpython_data(w_object):
    "NOT_RPYTHON"
    return rpython_data.__get__(w_object.value)


class Entry(ExtRegistryEntry):
    """Support for translating prebuilt emulated type objects."""
    _type_ = rpython_meta

    def get_ll_pyobjectptr(self, rtyper):
        from pypy.rpython.rclass import getinstancerepr
        emulated_cls = self.instance
        rpython_cls = emulated_cls._rpython_class_
        classdef = rtyper.annotator.bookkeeper.getuniqueclassdef(rpython_cls)
        r_inst = getinstancerepr(rtyper, classdef)
        return build_pytypeobject(r_inst)
