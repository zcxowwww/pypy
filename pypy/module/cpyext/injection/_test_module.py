from rpython.rtyper.lltypesystem import lltype, rffi
from pypy.interpreter.gateway import interp2app
from pypy.interpreter.typedef import unwrap_spec, TypeDef
from pypy.module.cpyext.pyobject import as_pyobj, make_typedescr, track_reference
from pypy.module.cpyext.api import PyObjectFields
from pypy.interpreter import typedef
from pypy.objspace.std.intobject import W_IntObject
from pypy.module.cpyext.api import bootstrap_function


mytype_object = lltype.Ptr(lltype.Struct(
    'mytype_object',
    *(PyObjectFields + (("foo", rffi.INT),))))


class Original:
    def __init__(self, space):
        pass


@unwrap_spec(index=int)
def injected_getitem(space, w_self, index):
    if index > 0:
        py_obj = as_pyobj(space, w_self)
        py_obj = rffi.cast(mytype_object, py_obj)
        return space.wrap(index * rffi.getintfield(py_obj, "foo"))
    else:
        org = space.fromcache(Original)
        return space.call_function(org.w_original_getitem, w_self,
                                   space.wrap(index))

class W_MyItem(W_IntObject):
    pass

W_MyItem.typedef = TypeDef("mything", __doc__ = "foo")

def mything_attach(space, py_obj, w_obj):
    py_mything = rffi.cast(mytype_object, py_obj)
    rffi.setintfield(py_mything, 'foo', space.int_w(w_obj))

def mything_realize(space, obj):
    intval = rffi.cast(lltype.Signed, rffi.cast(mytype_object, obj).foo)
    w_obj = W_MyItem(intval)
    track_reference(space, obj, w_obj)
    return w_obj

@bootstrap_function
def init_mything(space):
    make_typedescr(W_MyItem.typedef,
                   basestruct=mytype_object.TO,
                   attach=mything_attach,
                   realize=mything_realize)

@unwrap_spec(arg=int)
def injected_make(space, arg):
    if arg == 15:
        org = space.fromcache(Original)
        return space.call_function(org.w_original_make, space.wrap(arg))
    if arg == 25:
        org = space.fromcache(Original)
        return space.wrap(W_MyItem(2))
    return space.w_Ellipsis


injected_methods = {
    '__getitem__': interp2app(injected_getitem),
}
interp_injected_make = interp2app(injected_make)

def inject(space, name, dict_w, pto):
    assert name == 'test_module.test_mytype'
    org = space.fromcache(Original)
    org.w_original_getitem = dict_w['__getitem__']
    for key, value in injected_methods.items():
        dict_w[key] = space.wrap(value)

def inject_global(space, w_func, name):
    assert name == 'make'
    org = space.fromcache(Original)
    org.w_original_make = w_func
    return space.wrap(interp_injected_make)
