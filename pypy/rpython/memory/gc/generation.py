import sys
from pypy.rpython.memory.gc.semispace import SemiSpaceGC
from pypy.rpython.memory.gc.semispace import GCFLAG_EXTERNAL, GCFLAG_FORWARDED
from pypy.rpython.memory.gc.semispace import GCFLAG_HASHTAKEN
from pypy.rpython.lltypesystem.llmemory import NULL, raw_malloc_usage
from pypy.rpython.lltypesystem import lltype, llmemory, llarena
from pypy.rpython.memory.support import DEFAULT_CHUNK_SIZE
from pypy.rlib.objectmodel import free_non_gc_object
from pypy.rlib.debug import ll_assert
from pypy.rpython.lltypesystem.lloperation import llop

# The following flag is never set on young objects, i.e. the ones living
# in the nursery.  It is initially set on all prebuilt and old objects,
# and gets cleared by the write_barrier() when we write in them a
# pointer to a young object.
GCFLAG_NO_YOUNG_PTRS = SemiSpaceGC.first_unused_gcflag << 0

# The following flag is set on some last-generation objects (== prebuilt
# objects for GenerationGC, but see also HybridGC).  The flag is set
# unless the object is already listed in 'last_generation_root_objects'.
# When a pointer is written inside an object with GCFLAG_NO_HEAP_PTRS
# set, the write_barrier clears the flag and adds the object to
# 'last_generation_root_objects'.
GCFLAG_NO_HEAP_PTRS = SemiSpaceGC.first_unused_gcflag << 1

class GenerationGC(SemiSpaceGC):
    """A basic generational GC: it's a SemiSpaceGC with an additional
    nursery for young objects.  A write barrier is used to ensure that
    old objects that contain pointers to young objects are recorded in
    a list.
    """
    inline_simple_malloc = True
    inline_simple_malloc_varsize = True
    needs_write_barrier = True
    prebuilt_gc_objects_are_static_roots = False
    first_unused_gcflag = SemiSpaceGC.first_unused_gcflag << 2

    # the following values override the default arguments of __init__ when
    # translating to a real backend.
    TRANSLATION_PARAMS = {'space_size': 8*1024*1024, # XXX adjust
                          'nursery_size': 896*1024,
                          'min_nursery_size': 48*1024,
                          'auto_nursery_size': True}

    def __init__(self, config, chunk_size=DEFAULT_CHUNK_SIZE,
                 nursery_size=128,
                 min_nursery_size=128,
                 auto_nursery_size=False,
                 space_size=4096,
                 max_space_size=sys.maxint//2+1):
        SemiSpaceGC.__init__(self, config, chunk_size = chunk_size,
                             space_size = space_size,
                             max_space_size = max_space_size)
        assert min_nursery_size <= nursery_size <= space_size // 2
        self.initial_nursery_size = nursery_size
        self.auto_nursery_size = auto_nursery_size
        self.min_nursery_size = min_nursery_size

        # define nursery fields
        self.reset_nursery()
        self._setup_wb()

        # compute the constant lower bounds for the attributes
        # largest_young_fixedsize and largest_young_var_basesize.
        # It is expected that most (or all) objects have a fixedsize
        # that is much lower anyway.
        sz = self.get_young_fixedsize(self.min_nursery_size)
        self.lb_young_fixedsize = sz
        sz = self.get_young_var_basesize(self.min_nursery_size)
        self.lb_young_var_basesize = sz

    def setup(self):
        self.old_objects_pointing_to_young = self.AddressStack()
        # ^^^ a list of addresses inside the old objects space; it
        # may contain static prebuilt objects as well.  More precisely,
        # it lists exactly the old and static objects whose
        # GCFLAG_NO_YOUNG_PTRS bit is not set.
        self.young_objects_with_weakrefs = self.AddressStack()

        self.last_generation_root_objects = self.AddressStack()
        self.young_objects_with_id = self.AddressDict()
        SemiSpaceGC.setup(self)
        self.set_nursery_size(self.initial_nursery_size)
        # the GC is fully setup now.  The rest can make use of it.
        if self.auto_nursery_size:
            newsize = nursery_size_from_env()
            if newsize <= 0:
                newsize = estimate_best_nursery_size(
                    self.config.gcconfig.debugprint)
            if newsize > 0:
                self.set_nursery_size(newsize)

        self.reset_nursery()

    def _teardown(self):
        self.collect() # should restore last gen objects flags
        SemiSpaceGC._teardown(self)

    def reset_nursery(self):
        self.nursery      = NULL
        self.nursery_top  = NULL
        self.nursery_free = NULL

    def set_nursery_size(self, newsize):
        if newsize < self.min_nursery_size:
            newsize = self.min_nursery_size
        if newsize > self.space_size // 2:
            newsize = self.space_size // 2

        # Compute the new bounds for how large young objects can be
        # (larger objects are allocated directly old).   XXX adjust
        self.nursery_size = newsize
        self.largest_young_fixedsize = self.get_young_fixedsize(newsize)
        self.largest_young_var_basesize = self.get_young_var_basesize(newsize)
        scale = 0
        while (self.min_nursery_size << (scale+1)) <= newsize:
            scale += 1
        self.nursery_scale = scale
        if self.config.gcconfig.debugprint:
            llop.debug_print(lltype.Void, "SSS  nursery_size =", newsize)
            llop.debug_print(lltype.Void, "SSS  largest_young_fixedsize =",
                             self.largest_young_fixedsize)
            llop.debug_print(lltype.Void, "SSS  largest_young_var_basesize =",
                             self.largest_young_var_basesize)
            llop.debug_print(lltype.Void, "SSS  nursery_scale =", scale)
        # we get the following invariant:
        assert self.nursery_size >= (self.min_nursery_size << scale)

        # Force a full collect to remove the current nursery whose size
        # no longer matches the bounds that we just computed.  This must
        # be done after changing the bounds, because it might re-create
        # a new nursery (e.g. if it invokes finalizers).
        self.semispace_collect()

    @staticmethod
    def get_young_fixedsize(nursery_size):
        return nursery_size // 2 - 1

    @staticmethod
    def get_young_var_basesize(nursery_size):
        return nursery_size // 4 - 1

    def is_in_nursery(self, addr):
        ll_assert(llmemory.cast_adr_to_int(addr) & 1 == 0,
                  "odd-valued (i.e. tagged) pointer unexpected here")
        return self.nursery <= addr < self.nursery_top

    def malloc_fixedsize_clear(self, typeid, size, can_collect,
                               has_finalizer=False, contains_weakptr=False):
        if (has_finalizer or not can_collect or
            (raw_malloc_usage(size) > self.lb_young_fixedsize and
             raw_malloc_usage(size) > self.largest_young_fixedsize)):
            # ^^^ we do two size comparisons; the first one appears redundant,
            #     but it can be constant-folded if 'size' is a constant; then
            #     it almost always folds down to False, which kills the
            #     second comparison as well.
            ll_assert(not contains_weakptr, "wrong case for mallocing weakref")
            # "non-simple" case or object too big: don't use the nursery
            return SemiSpaceGC.malloc_fixedsize_clear(self, typeid, size,
                                                      can_collect,
                                                      has_finalizer,
                                                      contains_weakptr)
        size_gc_header = self.gcheaderbuilder.size_gc_header
        totalsize = size_gc_header + size
        result = self.nursery_free
        if raw_malloc_usage(totalsize) > self.nursery_top - result:
            result = self.collect_nursery()
        llarena.arena_reserve(result, totalsize)
        # GCFLAG_NO_YOUNG_PTRS is never set on young objs
        self.init_gc_object(result, typeid, flags=0)
        self.nursery_free = result + totalsize
        if contains_weakptr:
            self.young_objects_with_weakrefs.append(result + size_gc_header)
        return llmemory.cast_adr_to_ptr(result+size_gc_header, llmemory.GCREF)

    def malloc_varsize_clear(self, typeid, length, size, itemsize,
                             offset_to_length, can_collect):
        # Only use the nursery if there are not too many items.
        if not raw_malloc_usage(itemsize):
            too_many_items = False
        else:
            # The following line is usually constant-folded because both
            # min_nursery_size and itemsize are constants (the latter
            # due to inlining).
            maxlength_for_minimal_nursery = (self.min_nursery_size // 4 //
                                             raw_malloc_usage(itemsize))
            
            # The actual maximum length for our nursery depends on how
            # many times our nursery is bigger than the minimal size.
            # The computation is done in this roundabout way so that
            # only the only remaining computation is the following
            # shift.
            maxlength = maxlength_for_minimal_nursery << self.nursery_scale
            too_many_items = length > maxlength

        if (not can_collect or
            too_many_items or
            (raw_malloc_usage(size) > self.lb_young_var_basesize and
             raw_malloc_usage(size) > self.largest_young_var_basesize)):
            # ^^^ we do two size comparisons; the first one appears redundant,
            #     but it can be constant-folded if 'size' is a constant; then
            #     it almost always folds down to False, which kills the
            #     second comparison as well.
            return SemiSpaceGC.malloc_varsize_clear(self, typeid, length, size,
                                                    itemsize, offset_to_length,
                                                    can_collect)
        # with the above checks we know now that totalsize cannot be more
        # than about half of the nursery size; in particular, the + and *
        # cannot overflow
        size_gc_header = self.gcheaderbuilder.size_gc_header
        totalsize = size_gc_header + size + itemsize * length
        result = self.nursery_free
        if raw_malloc_usage(totalsize) > self.nursery_top - result:
            result = self.collect_nursery()
        llarena.arena_reserve(result, totalsize)
        # GCFLAG_NO_YOUNG_PTRS is never set on young objs
        self.init_gc_object(result, typeid, flags=0)
        (result + size_gc_header + offset_to_length).signed[0] = length
        self.nursery_free = result + llarena.round_up_for_allocation(totalsize)
        return llmemory.cast_adr_to_ptr(result+size_gc_header, llmemory.GCREF)

    # override the init_gc_object methods to change the default value of 'flags',
    # used by objects that are directly created outside the nursery by the SemiSpaceGC.
    # These objects must have the GCFLAG_NO_YOUNG_PTRS flag set immediately.
    def init_gc_object(self, addr, typeid, flags=GCFLAG_NO_YOUNG_PTRS):
        SemiSpaceGC.init_gc_object(self, addr, typeid, flags)

    def init_gc_object_immortal(self, addr, typeid,
                                flags=GCFLAG_NO_YOUNG_PTRS|GCFLAG_NO_HEAP_PTRS):
        SemiSpaceGC.init_gc_object_immortal(self, addr, typeid, flags)

    # flags exposed for the HybridGC subclass
    GCFLAGS_FOR_NEW_YOUNG_OBJECTS = 0   # NO_YOUNG_PTRS never set on young objs
    GCFLAGS_FOR_NEW_EXTERNAL_OBJECTS = (GCFLAG_EXTERNAL | GCFLAG_FORWARDED |
                                        GCFLAG_NO_YOUNG_PTRS |
                                        GCFLAG_HASHTAKEN)

    # ____________________________________________________________
    # Support code for full collections

    def collect(self, gen=1):
        if gen == 0:
            self.collect_nursery()
        else:
            SemiSpaceGC.collect(self)

    def semispace_collect(self, size_changing=False):
        self.reset_young_gcflags() # we are doing a full collection anyway
        self.weakrefs_grow_older()
        self.ids_grow_older()
        self.reset_nursery()
        if self.config.gcconfig.debugprint:
            llop.debug_print(lltype.Void, "major collect, size changing", size_changing)
        SemiSpaceGC.semispace_collect(self, size_changing)
        if self.config.gcconfig.debugprint and not size_changing:
            llop.debug_print(lltype.Void, "percent survived", float(self.free - self.tospace) / self.space_size)

    def make_a_copy(self, obj, objsize):
        tid = self.header(obj).tid
        # During a full collect, all copied objects might implicitly come
        # from the nursery.  In case they do, we must add this flag:
        tid |= GCFLAG_NO_YOUNG_PTRS
        return self._make_a_copy_with_tid(obj, objsize, tid)
        # history: this was missing and caused an object to become old but without the
        # flag set.  Such an object is bogus in the sense that the write_barrier doesn't
        # work on it.  So it can eventually contain a ptr to a young object but we didn't
        # know about it.  That ptr was not updated in the next minor collect... boom at
        # the next usage.

    def reset_young_gcflags(self):
        # This empties self.old_objects_pointing_to_young, and puts the
        # GCFLAG_NO_YOUNG_PTRS back on all these objects.  We could put
        # the flag back more lazily but we expect this list to be short
        # anyway, and it's much saner to stick to the invariant:
        # non-young objects all have GCFLAG_NO_YOUNG_PTRS set unless
        # they are listed in old_objects_pointing_to_young.
        oldlist = self.old_objects_pointing_to_young
        while oldlist.non_empty():
            obj = oldlist.pop()
            hdr = self.header(obj)
            hdr.tid |= GCFLAG_NO_YOUNG_PTRS

    def weakrefs_grow_older(self):
        while self.young_objects_with_weakrefs.non_empty():
            obj = self.young_objects_with_weakrefs.pop()
            self.objects_with_weakrefs.append(obj)

    def collect_roots(self):
        """GenerationGC: collects all roots.
           HybridGC: collects all roots, excluding the generation 3 ones.
        """
        # Warning!  References from static (and possibly gen3) objects
        # are found by collect_last_generation_roots(), which must be
        # called *first*!  If it is called after walk_roots(), then the
        # HybridGC explodes if one of the _collect_root causes an object
        # to be added to self.last_generation_root_objects.  Indeed, in
        # this case, the newly added object is traced twice: once by
        # collect_last_generation_roots() and once because it was added
        # in self.rawmalloced_objects_to_trace.
        self.collect_last_generation_roots()
        self.root_walker.walk_roots(
            SemiSpaceGC._collect_root,  # stack roots
            SemiSpaceGC._collect_root,  # static in prebuilt non-gc structures
            None)   # we don't need the static in prebuilt gc objects

    def collect_last_generation_roots(self):
        stack = self.last_generation_root_objects
        self.last_generation_root_objects = self.AddressStack()
        while stack.non_empty():
            obj = stack.pop()
            self.header(obj).tid |= GCFLAG_NO_HEAP_PTRS
            # ^^^ the flag we just added will be removed immediately if
            # the object still contains pointers to younger objects
            self.trace(obj, self._trace_external_obj, obj)
        stack.delete()

    def _trace_external_obj(self, pointer, obj):
        addr = pointer.address[0]
        newaddr = self.copy(addr)
        pointer.address[0] = newaddr
        self.write_into_last_generation_obj(obj, newaddr)

    # ____________________________________________________________
    # Implementation of nursery-only collections

    def collect_nursery(self):
        if self.nursery_size > self.top_of_space - self.free:
            # the semispace is running out, do a full collect
            self.obtain_free_space(self.nursery_size)
            ll_assert(self.nursery_size <= self.top_of_space - self.free,
                         "obtain_free_space failed to do its job")
        if self.nursery:
            if self.config.gcconfig.debugprint:
                llop.debug_print(lltype.Void, "--- minor collect ---")
                llop.debug_print(lltype.Void, "nursery:",
                                 self.nursery, "to", self.nursery_top)
            # a nursery-only collection
            scan = beginning = self.free
            self.collect_oldrefs_to_nursery()
            self.collect_roots_in_nursery()
            scan = self.scan_objects_just_copied_out_of_nursery(scan)
            # at this point, all static and old objects have got their
            # GCFLAG_NO_YOUNG_PTRS set again by trace_and_drag_out_of_nursery
            if self.young_objects_with_weakrefs.non_empty():
                self.invalidate_young_weakrefs()
            if self.young_objects_with_id.length() > 0:
                self.update_young_objects_with_id()
            # mark the nursery as free and fill it with zeroes again
            llarena.arena_reset(self.nursery, self.nursery_size, 2)
            if self.config.gcconfig.debugprint:
                llop.debug_print(lltype.Void,
                                 "survived (fraction of the size):",
                                 float(scan - beginning) / self.nursery_size)
            #self.debug_check_consistency()   # -- quite expensive
        else:
            # no nursery - this occurs after a full collect, triggered either
            # just above or by some previous non-nursery-based allocation.
            # Grab a piece of the current space for the nursery.
            self.nursery = self.free
            self.nursery_top = self.nursery + self.nursery_size
            self.free = self.nursery_top
        self.nursery_free = self.nursery
        return self.nursery_free

    # NB. we can use self.copy() to move objects out of the nursery,
    # but only if the object was really in the nursery.

    def collect_oldrefs_to_nursery(self):
        # Follow the old_objects_pointing_to_young list and move the
        # young objects they point to out of the nursery.
        count = 0
        oldlist = self.old_objects_pointing_to_young
        while oldlist.non_empty():
            count += 1
            obj = oldlist.pop()
            hdr = self.header(obj)
            hdr.tid |= GCFLAG_NO_YOUNG_PTRS
            self.trace_and_drag_out_of_nursery(obj)
        if self.config.gcconfig.debugprint:
            llop.debug_print(lltype.Void, "collect_oldrefs_to_nursery", count)

    def collect_roots_in_nursery(self):
        # we don't need to trace prebuilt GcStructs during a minor collect:
        # if a prebuilt GcStruct contains a pointer to a young object,
        # then the write_barrier must have ensured that the prebuilt
        # GcStruct is in the list self.old_objects_pointing_to_young.
        self.root_walker.walk_roots(
            GenerationGC._collect_root_in_nursery,  # stack roots
            GenerationGC._collect_root_in_nursery,  # static in prebuilt non-gc
            None)                                   # static in prebuilt gc

    def _collect_root_in_nursery(self, root):
        obj = root.address[0]
        if self.is_in_nursery(obj):
            root.address[0] = self.copy(obj)

    def scan_objects_just_copied_out_of_nursery(self, scan):
        while scan < self.free:
            curr = scan + self.size_gc_header()
            self.trace_and_drag_out_of_nursery(curr)
            scan += self.size_gc_header() + self.get_size_incl_hash(curr)
        return scan

    def trace_and_drag_out_of_nursery(self, obj):
        """obj must not be in the nursery.  This copies all the
        young objects it references out of the nursery.
        """
        self.trace(obj, self._trace_drag_out, None)

    def _trace_drag_out(self, pointer, ignored):
        if self.is_in_nursery(pointer.address[0]):
            pointer.address[0] = self.copy(pointer.address[0])

    # The code relies on the fact that no weakref can be an old object
    # weakly pointing to a young object.  Indeed, weakrefs are immutable
    # so they cannot point to an object that was created after it.
    def invalidate_young_weakrefs(self):
        # walk over the list of objects that contain weakrefs and are in the
        # nursery.  if the object it references survives then update the
        # weakref; otherwise invalidate the weakref
        while self.young_objects_with_weakrefs.non_empty():
            obj = self.young_objects_with_weakrefs.pop()
            if not self.surviving(obj):
                continue # weakref itself dies
            obj = self.get_forwarding_address(obj)
            offset = self.weakpointer_offset(self.get_type_id(obj))
            pointing_to = (obj + offset).address[0]
            if self.is_in_nursery(pointing_to):
                if self.surviving(pointing_to):
                    (obj + offset).address[0] = self.get_forwarding_address(
                        pointing_to)
                else:
                    (obj + offset).address[0] = NULL
                    continue    # no need to remember this weakref any longer
            self.objects_with_weakrefs.append(obj)

    # for the JIT: a minimal description of the write_barrier() method
    # (the JIT assumes it is of the shape
    #  "if newvalue.int0 & JIT_WB_IF_FLAG: remember_young_pointer()")
    JIT_WB_IF_FLAG = GCFLAG_NO_YOUNG_PTRS

    def write_barrier(self, newvalue, addr_struct):
        if self.header(addr_struct).tid & GCFLAG_NO_YOUNG_PTRS:
            self.remember_young_pointer(addr_struct, newvalue)

    def _setup_wb(self):
        # The purpose of attaching remember_young_pointer to the instance
        # instead of keeping it as a regular method is to help the JIT call it.
        # Additionally, it makes the code in write_barrier() marginally smaller
        # (which is important because it is inlined *everywhere*).
        # For x86, there is also an extra requirement: when the JIT calls
        # remember_young_pointer(), it assumes that it will not touch the SSE
        # registers, so it does not save and restore them (that's a *hack*!).
        def remember_young_pointer(addr_struct, addr):
            #llop.debug_print(lltype.Void, "\tremember_young_pointer",
            #                 addr_struct, "<-", addr)
            ll_assert(not self.is_in_nursery(addr_struct),
                         "nursery object with GCFLAG_NO_YOUNG_PTRS")
            # if we have tagged pointers around, we first need to check whether
            # we have valid pointer here, otherwise we can do it after the
            # is_in_nursery check
            if (self.config.taggedpointers and
                not self.is_valid_gc_object(addr)):
                return
            if self.is_in_nursery(addr):
                self.old_objects_pointing_to_young.append(addr_struct)
                self.header(addr_struct).tid &= ~GCFLAG_NO_YOUNG_PTRS
            elif (not self.config.taggedpointers and
                  not self.is_valid_gc_object(addr)):
                return
            self.write_into_last_generation_obj(addr_struct, addr)
        remember_young_pointer._dont_inline_ = True
        self.remember_young_pointer = remember_young_pointer

    def assume_young_pointers(self, addr_struct):
        objhdr = self.header(addr_struct)
        if objhdr.tid & GCFLAG_NO_YOUNG_PTRS:
            self.old_objects_pointing_to_young.append(addr_struct)
            objhdr.tid &= ~GCFLAG_NO_YOUNG_PTRS
        if objhdr.tid & GCFLAG_NO_HEAP_PTRS:
            objhdr.tid &= ~GCFLAG_NO_HEAP_PTRS
            self.last_generation_root_objects.append(addr_struct)

    def write_into_last_generation_obj(self, addr_struct, addr):
        objhdr = self.header(addr_struct)
        if objhdr.tid & GCFLAG_NO_HEAP_PTRS:
            if not self.is_last_generation(addr):
                objhdr.tid &= ~GCFLAG_NO_HEAP_PTRS
                self.last_generation_root_objects.append(addr_struct)

    def is_last_generation(self, obj):
        # overridden by HybridGC
        return (self.header(obj).tid & GCFLAG_EXTERNAL) != 0

    def _compute_id(self, obj):
        if self.is_in_nursery(obj):
            result = self.young_objects_with_id.get(obj)
            if not result:
                result = self._next_id()
                self.young_objects_with_id.setitem(obj, result)
            return result
        else:
            return SemiSpaceGC._compute_id(self, obj)

    def update_young_objects_with_id(self):
        self.young_objects_with_id.foreach(self._update_object_id,
                                           self.objects_with_id)
        self.young_objects_with_id.clear()
        # NB. the clear() also makes the dictionary shrink back to its
        # minimal size, which is actually a good idea: a large, mostly-empty
        # table is bad for the next call to 'foreach'.

    def ids_grow_older(self):
        self.young_objects_with_id.foreach(self._id_grow_older, None)
        self.young_objects_with_id.clear()

    def _id_grow_older(self, obj, id, ignored):
        self.objects_with_id.setitem(obj, id)

    def dump_heap_walk_roots(self):
        self.last_generation_root_objects.foreach(
            self._track_heap_ext, None)
        self.root_walker.walk_roots(
            SemiSpaceGC._track_heap_root,
            SemiSpaceGC._track_heap_root,
            SemiSpaceGC._track_heap_root)

    def _track_heap_ext(self, adr, ignored):
        self.trace(adr, self.track_heap_parent, adr)

    def debug_check_object(self, obj):
        """Check the invariants about 'obj' that should be true
        between collections."""
        SemiSpaceGC.debug_check_object(self, obj)
        tid = self.header(obj).tid
        if tid & GCFLAG_NO_YOUNG_PTRS:
            ll_assert(not self.is_in_nursery(obj),
                      "nursery object with GCFLAG_NO_YOUNG_PTRS")
            self.trace(obj, self._debug_no_nursery_pointer, None)
        elif not self.is_in_nursery(obj):
            ll_assert(self._d_oopty.contains(obj),
                      "missing from old_objects_pointing_to_young")
        if tid & GCFLAG_NO_HEAP_PTRS:
            ll_assert(self.is_last_generation(obj),
                      "GCFLAG_NO_HEAP_PTRS on non-3rd-generation object")
            self.trace(obj, self._debug_no_gen1or2_pointer, None)
        elif self.is_last_generation(obj):
            ll_assert(self._d_lgro.contains(obj),
                      "missing from last_generation_root_objects")

    def _debug_no_nursery_pointer(self, root, ignored):
        ll_assert(not self.is_in_nursery(root.address[0]),
                  "GCFLAG_NO_YOUNG_PTRS but found a young pointer")
    def _debug_no_gen1or2_pointer(self, root, ignored):
        target = root.address[0]
        ll_assert(not target or self.is_last_generation(target),
                  "GCFLAG_NO_HEAP_PTRS but found a pointer to gen1or2")

    def debug_check_consistency(self):
        if self.DEBUG:
            self._d_oopty = self.old_objects_pointing_to_young.stack2dict()
            self._d_lgro = self.last_generation_root_objects.stack2dict()
            SemiSpaceGC.debug_check_consistency(self)
            self._d_oopty.delete()
            self._d_lgro.delete()
            self.old_objects_pointing_to_young.foreach(
                self._debug_check_flag_1, None)
            self.last_generation_root_objects.foreach(
                self._debug_check_flag_2, None)

    def _debug_check_flag_1(self, obj, ignored):
        ll_assert(not (self.header(obj).tid & GCFLAG_NO_YOUNG_PTRS),
                  "unexpected GCFLAG_NO_YOUNG_PTRS")
    def _debug_check_flag_2(self, obj, ignored):
        ll_assert(not (self.header(obj).tid & GCFLAG_NO_HEAP_PTRS),
                  "unexpected GCFLAG_NO_HEAP_PTRS")

    def debug_check_can_copy(self, obj):
        if self.is_in_nursery(obj):
            pass    # it's ok to copy an object out of the nursery
        else:
            SemiSpaceGC.debug_check_can_copy(self, obj)


# ____________________________________________________________

import os

def nursery_size_from_env():
    value = os.environ.get('PYPY_GENERATIONGC_NURSERY')
    if value:
        if value[-1] in 'kK':
            factor = 1024
            value = value[:-1]
        else:
            factor = 1
        try:
            return int(value) * factor
        except ValueError:
            pass
    return -1

def best_nursery_size_for_L2cache(L2cache, debugprint=False):
    if debugprint:
        llop.debug_print(lltype.Void, "CCC  L2cache =", L2cache)
    # Heuristically, the best nursery size to choose is about half
    # of the L2 cache.  XXX benchmark some more.
    return L2cache // 2


if sys.platform == 'linux2':
    def estimate_best_nursery_size(debugprint=False):
        """Try to estimate the best nursery size at run-time, depending
        on the machine we are running on.
        """
        L2cache = sys.maxint
        try:
            fd = os.open('/proc/cpuinfo', os.O_RDONLY, 0644)
            try:
                data = []
                while True:
                    buf = os.read(fd, 4096)
                    if not buf:
                        break
                    data.append(buf)
            finally:
                os.close(fd)
        except OSError:
            pass
        else:
            data = ''.join(data)
            linepos = 0
            while True:
                start = findend(data, '\ncache size', linepos)
                if start < 0:
                    break    # done
                linepos = findend(data, '\n', start)
                if linepos < 0:
                    break    # no end-of-line??
                # *** data[start:linepos] == "   : 2048 KB\n"
                start = skipspace(data, start)
                if data[start] != ':':
                    continue
                # *** data[start:linepos] == ": 2048 KB\n"
                start = skipspace(data, start + 1)
                # *** data[start:linepos] == "2048 KB\n"
                end = start
                while '0' <= data[end] <= '9':
                    end += 1
                # *** data[start:end] == "2048"
                if start == end:
                    continue
                number = int(data[start:end])
                # *** data[end:linepos] == " KB\n"
                end = skipspace(data, end)
                if data[end] not in ('K', 'k'):    # assume kilobytes for now
                    continue
                number = number * 1024
                # for now we look for the smallest of the L2 caches of the CPUs
                if number < L2cache:
                    L2cache = number

        if L2cache < sys.maxint:
            return best_nursery_size_for_L2cache(L2cache, debugprint)
        else:
            # Print a warning even in non-debug builds
            llop.debug_print(lltype.Void,
                "Warning: cannot find your CPU L2 cache size in /proc/cpuinfo")
            return -1

    def findend(data, pattern, pos):
        pos = data.find(pattern, pos)
        if pos < 0:
            return -1
        return pos + len(pattern)

    def skipspace(data, pos):
        while data[pos] in (' ', '\t'):
            pos += 1
        return pos

elif sys.platform == 'darwin':
    from pypy.rpython.lltypesystem import lltype, rffi

    sysctlbyname = rffi.llexternal('sysctlbyname',
                                   [rffi.CCHARP, rffi.VOIDP, rffi.SIZE_TP,
                                    rffi.VOIDP, rffi.SIZE_T],
                                   rffi.INT,
                                   sandboxsafe=True)

    def estimate_best_nursery_size(debugprint=False):
        """Try to estimate the best nursery size at run-time, depending
        on the machine we are running on.
        """
        L2cache = 0
        l2cache_p = lltype.malloc(rffi.LONGLONGP.TO, 1, flavor='raw')
        try:
            len_p = lltype.malloc(rffi.SIZE_TP.TO, 1, flavor='raw')
            try:
                size = rffi.sizeof(rffi.LONGLONG)
                l2cache_p[0] = rffi.cast(rffi.LONGLONG, 0)
                len_p[0] = rffi.cast(rffi.SIZE_T, size)
                # XXX a hack for llhelper not being robust-enough
                result = sysctlbyname("hw.l2cachesize",
                                      rffi.cast(rffi.VOIDP, l2cache_p),
                                      len_p,
                                      lltype.nullptr(rffi.VOIDP.TO), 
                                      rffi.cast(rffi.SIZE_T, 0))
                if (rffi.cast(lltype.Signed, result) == 0 and
                    rffi.cast(lltype.Signed, len_p[0]) == size):
                    L2cache = rffi.cast(lltype.Signed, l2cache_p[0])
                    if rffi.cast(rffi.LONGLONG, L2cache) != l2cache_p[0]:
                        L2cache = 0    # overflow!
            finally:
                lltype.free(len_p, flavor='raw')
        finally:
            lltype.free(l2cache_p, flavor='raw')
        if L2cache > 0:
            return best_nursery_size_for_L2cache(L2cache, debugprint)
        else:
            # Print a warning even in non-debug builds
            llop.debug_print(lltype.Void,
                "Warning: cannot find your CPU L2 cache size with sysctl()")
            return -1

else:
    def estimate_best_nursery_size(debugprint=False):
        return -1     # XXX implement me for other platforms
