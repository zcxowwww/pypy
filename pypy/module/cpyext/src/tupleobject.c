
/* Tuple object implementation, stolen&adapted from CPython.  

   One important difference is that CPython caches the empty tuple separately
   and always return the very same object, while we always return a fresh one.
   The reasons is that space.newtuple([]) always return different objects, and
   we want to ensure that the following is always true:

       w_a != w_b ==> as_pyobj(w_b) != as_pyobj(w_b)
 */

#include "Python.h"

/* Speed optimization to avoid frequent malloc/free of small tuples */
#ifndef PyTuple_MAXSAVESIZE
#define PyTuple_MAXSAVESIZE     20  /* Largest tuple to save on free list */
#endif
#ifndef PyTuple_MAXFREELIST
#define PyTuple_MAXFREELIST  2000  /* Maximum number of tuples of each size to save */
#endif

#if PyTuple_MAXSAVESIZE > 0
static PyTupleObject *free_list[PyTuple_MAXSAVESIZE];
static int numfree[PyTuple_MAXSAVESIZE];
#endif

PyObject *
PyTuple_New(register Py_ssize_t size)
{
    register PyTupleObject *op;
    Py_ssize_t i;
    if (size < 0) {
        PyErr_BadInternalCall();
        return NULL;
    }
#if PyTuple_MAXSAVESIZE > 0
    if (size < PyTuple_MAXSAVESIZE && (op = free_list[size]) != NULL) {
        free_list[size] = (PyTupleObject *) op->ob_item[0];
        numfree[size]--;
        _Py_NewReference((PyObject *)op);
    }
    else
#endif
    {
        Py_ssize_t nbytes = size * sizeof(PyObject *);
        /* Check for overflow */
        if (nbytes / sizeof(PyObject *) != (size_t)size ||
            (nbytes > PY_SSIZE_T_MAX - sizeof(PyTupleObject) - sizeof(PyObject *)))
        {
            return PyErr_NoMemory();
        }

        op = PyObject_GC_NewVar(PyTupleObject, &PyTuple_Type, size);
        if (op == NULL)
            return NULL;
    }
    for (i=0; i < size; i++)
        op->ob_item[i] = NULL;
    _PyObject_GC_TRACK_Tuple(op);
    return (PyObject *) op;
}

/* this is CPython's tupledealloc */
void
_PyPy_tuple_dealloc(register PyObject *_op)
{
    register PyTupleObject *op = (PyTupleObject *)_op;
    register Py_ssize_t i;
    register Py_ssize_t len =  Py_SIZE(op);
    PyObject_GC_UnTrack(op);
    Py_TRASHCAN_SAFE_BEGIN(op)
    if (len >= 0) {
        i = len;
        while (--i >= 0)
            Py_XDECREF(op->ob_item[i]);
#if PyTuple_MAXSAVESIZE > 0
        if (len < PyTuple_MAXSAVESIZE &&
            numfree[len] < PyTuple_MAXFREELIST &&
            Py_TYPE(op) == &PyTuple_Type)
        {
            op->ob_item[0] = (PyObject *) free_list[len];
            numfree[len]++;
            free_list[len] = op;
            goto done; /* return */
        }
#endif
    }
    Py_TYPE(op)->tp_free((PyObject *)op);
done:
    Py_TRASHCAN_SAFE_END(op)
}

void
_PyPy_tuple_free(void *obj)
{
    PyObject_GC_Del(obj);
}

int
_PyPy_tuple_traverse(PyObject *ob, visitproc visit, void *arg)
{
    PyTupleObject *o = (PyTupleObject *)ob;
    Py_ssize_t i;

    for (i = Py_SIZE(o); --i >= 0; )
        Py_VISIT(o->ob_item[i]);
    return 0;
}

/* Return 0 if the tuple is untracked afterwards, return 1 if the tuple
   should always be kept tracked and return 2 if the tuple was not fully
   intialized yet. */
Py_ssize_t
_PyTuple_MaybeUntrack(PyObject *op)
{
    PyTupleObject *t;
    Py_ssize_t i, n;

    if (!PyTuple_CheckExact(op))
        return 1;
    if (!_PyGC_IS_TRACKED(op))
        return 0;
    t = (PyTupleObject *) op;
    n = Py_SIZE(t);
    for (i = 0; i < n; i++) {
        PyObject *elt = PyTuple_GET_ITEM(t, i);
        /* Tuple with NULL elements aren't
           fully constructed, don't untrack
           them yet. */
        if (!elt)
            return 2;
        if (_PyObject_GC_MAY_BE_TRACKED(elt))
            return 1;
    }
    _PyObject_GC_UNTRACK(op);
    return 0;
}