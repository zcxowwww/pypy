import threading, pdb
import types
from pypy.objspace.flow.model import FunctionGraph

class _EnableGraphic:
    def __init__(self, port=None):
        self.port = port

class PdbPlusShow(pdb.Pdb):

    def __init__(self, translator):
        pdb.Pdb.__init__(self)
        self.translator = translator
        self.exposed = {}

    def post_mortem(self, t):
        self.reset()
        while t.tb_next is not None:
            t = t.tb_next
        self.interaction(t.tb_frame, t)        

    def expose(self, d):
        self.exposed.update(d)

    show = None

    def install_show(self, show):
        self.show = show

    def _show(self, page):
        if not self.show:
            print "*** No display"
            return
        self.show(page)

    def _importobj(self, fullname):
        obj = None
        name = ''
        for comp in fullname.split('.'):
            name += comp
            obj = getattr(obj, comp, None)
            if obj is None:
                try:
                    obj = __import__(name, {}, {}, ['*'])
                except ImportError:
                    raise NameError
            name += '.'
        return obj

    TRYPREFIXES = ['','pypy.','pypy.objspace.','pypy.interpreter.', 'pypy.objspace.std.' ]

    def _mygetval(self, arg, errmsg):
        try:
            return eval(arg, self.curframe.f_globals,
                    self.curframe.f_locals)
        except:
            t, v = sys.exc_info()[:2]
            if isinstance(t, str):
                exc_type_name = t
            else: exc_type_name = t.__name__
            if not isinstance(arg, str):
                print '*** %s' % errmsg, "\t[%s: %s]" % (exc_type_name, v)
            else:
                print '*** %s:' % errmsg, arg, "\t[%s: %s]" % (exc_type_name, v)
            raise

    def _getobj(self, name):
        if '.' in name:
            for pfx in self.TRYPREFIXES:
                try:
                    return self._importobj(pfx+name)
                except NameError:
                    pass
        try:
            return self._mygetval(name, "Not found")
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except:
            pass
        return None

    def do_find(self, arg):
        """find obj [as var]
find dotted named obj, possibly using prefixing with some packages 
in pypy (see help pypyprefixes); the result is assigned to var or _."""
        objarg, var = self._parse_modif(arg)
        obj = self._getobj(objarg)
        if obj is None:
            return
        print obj
        self._setvar(var, obj)

    def _parse_modif(self, arg, modif='as'):
        var = '_'
        aspos = arg.rfind(modif+' ')
        if aspos != -1:
            objarg = arg[:aspos].strip()
            var = arg[aspos+(1+len(modif)):].strip()
        else:
            objarg = arg
        return objarg, var

    def _setvar(self, var, obj):
        self.curframe.f_locals[var] = obj

    class GiveUp(Exception):
        pass

    def _make_flt(self, expr):
        try:
            expr = compile(expr, '<filter>', 'eval')
        except SyntaxError:
            print "*** syntax: %s" % expr
            return None
        def flt(c):
            marker = object()
            try:
                old = self.curframe.f_locals.get('cand', marker)
                self.curframe.f_locals['cand'] = c
                try:
                    return self._mygetval(expr, "oops")
                except (KeyboardInterrupt, SystemExit, MemoryError):
                    raise
                except:
                    raise self.GiveUp
            finally:
                if old is not marker:
                    self.curframe.f_locals['cand'] = old
                else:
                    del self.curframe.f_locals['cand']
        return flt

    def do_findclasses(self, arg):
        """findclasses expr [as var]
find annotated classes for which expr is true, cand in it referes to
the candidate class; the result list is assigned to var or _."""
        expr, var = self._parse_modif(arg)
        flt = self._make_flt(expr)
        if flt is None:
            return
        cls = []
        try:
            for c in self.translator.annotator.getuserclasses():
                if flt(c):
                    cls.append(c)
        except self.GiveUp:
            return
        self._setvar(var, cls)

    def do_findfuncs(self, arg):
        """findfuncs expr [as var]
find flow-graphed functions for which expr is true, cand in it referes to
the candidate function; the result list is assigned to var or _."""
        expr, var = self._parse_modif(arg)
        flt = self._make_flt(expr)
        if flt is None:
            return
        funcs = []
        try:
            for f in self.translator.flowgraphs:
                if flt(f):
                    funcs.append(f)
        except self.GiveUp:
            return
        self._setvar(var, funcs)

    def do_showg(self, arg):
        """showg obj
show graph for obj, obj can be an expression or a dotted name
(in which case prefixing with some packages in pypy is tried (see help pypyprefixes)).
if obj is a function or method, the localized call graph is shown;
if obj is a class or ClassDef the class definition graph is shown"""            
        from pypy.annotation.classdef import ClassDef
        from pypy.translator.tool import graphpage
        translator = self.translator
        obj = self._getobj(arg)
        if obj is None:
            return
        if hasattr(obj, 'im_func'):
            obj = obj.im_func
        if isinstance(obj, types.FunctionType):
            page = graphpage.LocalizedCallGraphPage(translator, self._allgraphs(obj))
        elif isinstance(obj, FunctionGraph):
            page = graphpage.FlowGraphPage(translator, [obj])
        elif isinstance(obj, (type, types.ClassType)):
            classdef = translator.annotator.bookkeeper.getuniqueclassdef(obj)
            page = graphpage.ClassDefPage(translator, classdef)
        elif isinstance(obj, ClassDef):
            page = graphpage.ClassDefPage(translator, obj)
        else:
            print "*** Nothing to do"
            return
        self._show(page)

    def _attrs(self, arg, pr):
        arg, expr = self._parse_modif(arg, 'match')
        if expr == '_':
            expr = 'True'
        obj = self._getobj(arg)
        if obj is None:
            return
        try:
            obj = list(obj)
        except:
            obj = [obj]
        getcdef = self.translator.annotator.bookkeeper.getuniqueclassdef
        clsdefs = []
        for x in obj:
            if isinstance(x, (type, types.ClassType)):
                clsdefs.append(getcdef(x))
            else:
                clsdefs.append(x)

        def longname(c):
            return c.name
        clsdefs.sort(lambda x,y: cmp(longname(x), longname(y)))
        flt = self._make_flt(expr)
        if flt is None:
            return
        for cdef in clsdefs:
            try:
                attrs = [a for a in cdef.attrs.itervalues() if flt(a)]
            except self.GiveUp:
                return
            if attrs:
                print "%s:" % cdef.name
                pr(attrs)

    def do_attrs(self, arg):
        """attrs obj [match expr]
list annotated attrs of class|def obj or list of classe(def)s obj,
obj can be an expression or a dotted name
(in which case prefixing with some packages in pypy is tried (see help pypyprefixes));
expr is an optional filtering expression; cand in it refer to the candidate Attribute
information object, which has a .name and .s_value."""
        def pr(attrs):
            print " " + ' '.join([a.name for a in attrs])
        self._attrs(arg, pr)

    def do_attrsann(self, arg):
        """attrsann obj [match expr]
list with their annotation annotated attrs of class|def obj or list of classe(def)s obj,
obj can be an expression or a dotted name
(in which case prefixing with some packages in pypy is tried (see help pypyprefixes));
expr is an optional filtering expression; cand in it refer to the candidate Attribute
information object, which has a .name and .s_value."""
        def pr(attrs):
            for a in attrs:
                print ' %s %s' % (a.name, a.s_value)
        self._attrs(arg, pr)

    def do_readpos(self, arg):
        """readpos obj attrname [match expr] [as var]
list the read positions of annotated attr with attrname of class or classdef obj,
obj can be an expression or a dotted name
(in which case prefixing with some packages in pypy is tried (see help pypyprefixes));
expr is an optional filtering expression; cand in it refer to the candidate read
position information, which has a .func (which can be None), a .graph  and .block and .i;
the list of the read positions functions is set to var or _."""
        class Pos:
            def __init__(self, graph, func, block, i):
                self.graph = graph
                self.func = func
                self.block = block
                self.i = i
        arg, var = self._parse_modif(arg, 'as')
        arg, expr = self._parse_modif(arg, 'match')
        if expr == '_':
            expr = 'True'
        args = arg.split()
        if len(args) != 2:
            print "*** expected obj attrname:", arg
            return
        arg, attrname = args
        # allow quotes around attrname
        if (attrname.startswith("'") and attrname.endswith("'")
            or attrname.startswith('"') and attrname.endswith('"')):
            attrname = attrname[1:-1]

        obj = self._getobj(arg)
        if obj is None:
            return
        if isinstance(obj, (type, types.ClassType)):
            obj = self.translator.annotator.bookkeeper.getuniqueclassdef(obj)
        attrs = obj.attrs
        if attrname not in attrs:
            print "*** bogus:", attrname
            return
        pos = attrs[attrname].read_locations
        if not pos:
            return
        flt = self._make_flt(expr)
        if flt is None:
            return
        r = {}
        try:
            for p in pos:
                graph, block, i = p
                if hasattr(graph, 'func'):
                    func = graph.func
                else:
                    func = None
                if flt(Pos(graph, func, block, i)):
                    if func is not None:
                        print func.__module__ or '?', func.__name__, block, i
                    else:
                        print graph, block, i
                    if i >= 0:
                        op = block.operations[i]
                        print " ", op
                        print " ",
                        for arg in op.args:
                            print "%s: %s" % (arg, self.translator.annotator.binding(arg)),
                        print
                        
                    r[func] = True
        except self.GiveUp:
            return
        self._setvar(var, r.keys())
            

    def do_flowg(self, arg):
        """callg obj
show flow graph for function obj, obj can be an expression or a dotted name
(in which case prefixing with some packages in pypy is tried (see help pypyprefixes))"""            
        from pypy.translator.tool import graphpage                        
        obj = self._getobj(arg)
        if obj is None:
            return
        if hasattr(obj, 'im_func'):
            obj = obj.im_func
        if isinstance(obj, types.FunctionType):
            graphs = self._allgraphs(obj)
        elif isinstance(obj, FunctionGraph):
            graphs = [obj]
        else:
            print "*** Not a function"
            return
        self._show(graphpage.FlowGraphPage(self.translator, graphs))

    def _allgraphs(self, func):
        graphs = {}
        funcdesc = self.translator.annotator.bookkeeper.getdesc(func)
        for graph in funcdesc._cache.itervalues():
            graphs[graph] = True
        for graph in self.translator.graphs:
            if getattr(graph, 'func', None) is func:
                graphs[graph] = True
        return graphs.keys()


    def do_callg(self, arg):
        """callg obj
show localized call-graph for function obj, obj can be an expression or a dotted name
(in which case prefixing with some packages in pypy is tried (see help pypyprefixes))"""
        from pypy.translator.tool import graphpage                        
        obj = self._getobj(arg)
        if obj is None:
            return
        if hasattr(obj, 'im_func'):
            obj = obj.im_func
        if isinstance(obj, types.FunctionType):
            graphs = self._allgraphs(obj)
        elif isinstance(obj, FunctionGraph):
            graphs = [obj]
        else:
            print "*** Not a function"
            return
        self._show(graphpage.LocalizedCallGraphPage(self.translator, graphs))

    def do_classhier(self, arg):
        """classhier
show class hierarchy graph"""
        from pypy.translator.tool import graphpage           
        self._show(graphpage.ClassHierarchyPage(self.translator))

    def do_enable_graphic(self, arg):
        """enable_graphic
enable pygame graph display even from non-graphic mode"""
        if self.show:
            print "*** display already there"
            return
        raise _EnableGraphic

    def do_graphserve(self, arg):
        """graphserve <port>
start serving graphs on <port>    
"""
        if self.show:
            print "*** display already there"
            return
        raise _EnableGraphic(int(arg))

    def help_graphs(self):
        print "graph commands are: showg, flowg, callg, classhier, enable_graphic"

    def help_ann_other(self):
        print "other annotation related commands are: find, findclasses, findfuncs, attrs, attrsann, readpos"

    def help_pypyprefixes(self):
        print "these prefixes are tried for dotted names in graph commands:"
        print self.TRYPREFIXES

    # start helpers
    def _run_debugger(self, tb):
        if tb is None:
            fn, args = self.set_trace, ()
        else:
            fn, args = self.post_mortem, (tb,)
        try:
            t = self.translator # define enviroments, xxx more stuff
            exec ""
            locals().update(self.exposed)
            fn(*args)
            pass # for debugger to land
        except pdb.bdb.BdbQuit:
            pass    

    def _run_debugger_in_thread(self, tb, cleanup=None, cleanup_args=()):
        def _run_in_thread():
            try:
                self._run_debugger(tb)
            finally:
                if cleanup is not None:
                    cleanup(*cleanup_args)
        return threading.Thread(target=_run_in_thread, args=())

    def start(self, tb, server_setup, graphic=False):
        port = None
        if not graphic:
            try:
                self._run_debugger(tb)
            except _EnableGraphic, engraph:
                port = engraph.port
            else:
                return
        start, show, stop = server_setup(port)
        self.install_show(show)
        debugger = self._run_debugger_in_thread(tb, stop)
        debugger.start()
        start()
        debugger.join()
