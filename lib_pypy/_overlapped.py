"""
Support routines for overlapping io.
Currently, this extension module is only required when using the
modules on Windows.
"""

import sys
if sys.platform != 'win32':
    raise ImportError("The '_overlapped' module is only available on Windows")

# Declare external Win32 functions

from _pypy_winbase_cffi import ffi as _ffi
_kernel32 = _ffi.dlopen('kernel32')

_winsock2 = _ffi.dlopen('Ws2_32')

GetVersion = _kernel32.GetVersion
NULL = _ffi.NULL

from _winapi import INVALID_HANDLE_VALUE, _MAX_PATH , _Z
import _winapi

#
# Error Codes
#
ERROR_IO_PENDING = 997
ERROR_PIPE_BUSY = 231
ERROR_NETNAME_DELETED = 64

SOCKET_ERROR = -1

#
# Status Codes
#
STATUS_PENDING = 0x00000103

DisconnectEx = _ffi.NULL

def _int2intptr(int2cast):
    return _ffi.cast("ULONG_PTR", int2cast)

def _int2dword(int2cast):
    return _ffi.cast("DWORD", int2cast)

def _int2handle(val):
    return _ffi.cast("HANDLE", val)


def _handle2int(handle):
    return int(_ffi.cast("intptr_t", handle))

from enum import Enum
class OverlappedType(Enum):
    TYPE_NONE = 0 
    TYPE_NOT_STARTED = 1
    TYPE_READ = 2 
    TYPE_READINTO = 3
    TYPE_WRITE = 4
    TYPE_ACCEPT = 5
    TYPE_CONNECT = 6
    TYPE_DISCONNECT = 7
    TYPE_CONNECT_NAMED_PIPE = 8
    TYPE_WAIT_NAMED_PIPE_AND_CONNECT = 9
    TYPE_TRANSMIT_FILE = 10

class Overlapped(object):
    def __init__(self, event=_ffi.NULL):
        self.overlapped = _ffi.new('OVERLAPPED[1]')
        self.handle = _ffi.NULL
        self.read_buffer = None
        self.write_buffer = None
        self.error = 0

        self.type = OverlappedType.TYPE_NONE
        if event == _int2handle(INVALID_HANDLE_VALUE) or not event: 
            event = _kernel32.CreateEventW(NULL, True, False, NULL)
            if event == _winapi.NULL:
                raise _winapi._WinError()
        
        if event:
            self.overlapped[0].hEvent = event
        else:
            raise _winapi._WinError()
                
        if self.overlapped[0].hEvent == _ffi.NULL: 
             raise _winapi._WinError()

    def __del__(self):
        bytes = _ffi.new("DWORD[1]",[0])
        olderr = _kernel32.GetLastError()
        hascompletedio = HasOverlappedIoCompleted(self.overlapped[0])      
        if not hascompletedio and self.type != OverlappedType.TYPE_NOT_STARTED:
            
            wait = _kernel32.CancelIoEx(self.handle, self.overlapped)
            ret = self.GetOverlappedResult(wait)
            err = _winapi.ERROR_SUCCESS
            if not ret:
                err = _kernel32.GetLastError()
                self.error = err
            if err != _winapi.ERROR_SUCCESS and \
               err != _winapi.ERROR_NOT_FOUND and \
               err != _winapi.ERROR_OPERATION_ABORTED:
               raise _winapi._WinError()
        if self.overlapped[0].hEvent != 0:
            _winapi.CloseHandle(self.overlapped[0].hEvent)

    @property
    def event(self):
        return self.overlapped[0].hEvent

    def GetOverlappedResult(self, wait):
        transferred = _ffi.new('DWORD[1]', [0])
        
        if self.type == OverlappedType.TYPE_NONE:
            return _ffi.NULL
        
        if self.type == OverlappedType.TYPE_NOT_STARTED:
            return _ffi.NULL

        res = _kernel32.GetOverlappedResult(self.handle, self.overlapped, transferred, wait != 0)
        if res:
            err = _winapi.ERROR_SUCCESS
        else:
            err = _kernel32.GetLastError()
            self.error = err

        if err != _winapi.ERROR_SUCCESS and err != _winapi.ERROR_MORE_DATA:
            if not (err == _winapi.ERROR_BROKEN_PIPE and (self.type == TYPE_READ or self.type == TYPE_READINTO)):
                raise _winapi._WinError()

        if self.type == OverlappedType.TYPE_READ:
            return _ffi.unpack(self.read_buffer, transferred[0])
        else:
            return transferred[0]

    def getbuffer(self):
        xxx
        return None

    def cancel(self):
        result = True
        if self.type == OverlappedType.TYPE_NOT_STARTED or self.type == OverlappedType.TYPE_WAIT_NAMED_PIPE_AND_CONNECT:
            return None
        if not HasOverlappedIoCompleted(self.overlapped[0]):
            ### If we are to support xp we will need to dynamically load the below method
            result = _kernel32.CancelIoEx(self.handle, self.overlapped)
        if (not result and _winapi.GetLastError() != _winapi.ERROR_NOT_FOUND):
            raise _winapi._WinError()
     
    def WSARecv(self ,handle, size, flags):
        handle = _int2handle(handle)
        flags = _int2dword(flags)
        if self.type != OverlappedType.TYPE_NONE:
            raise _winapi._WinError()
        
        self.type = OverlappedType.TYPE_READ
        self.handle = _int2handle(handle)
        self.read_buffer = _ffi.new("CHAR[]", max(1,size))
        return self.do_WSARecv(handle, self.read_buffer, size, flags)

    def do_WSARecv(self, handle, allocatedbuffer, size, flags):
        nread = _ffi.new("LPDWORD")
        wsabuff = _ffi.new("WSABUF[1]")
        buffercount = _ffi.new("DWORD[1]", [1])
        pflags = _ffi.new("LPDWORD")
        pflags[0] = flags
        
        wsabuff[0].len = size        
        wsabuff[0].buf = allocatedbuffer

        result = _winsock2.WSARecv(handle, wsabuff, _int2dword(1), nread, pflags, self.overlapped, _ffi.NULL)
        if result == SOCKET_ERROR:
            self.error = _kernel32.GetLastError()
        else:
            self.error = _winapi.ERROR_SUCCESS            
        
        if self.error == _winapi.ERROR_BROKEN_PIPE:
            mark_as_completed(self.overlapped)
            raise _winapi._WinError()
        elif self.error in [_winapi.ERROR_SUCCESS, _winapi.ERROR_MORE_DATA, _winapi.ERROR_IO_PENDING] :
            return None
        else:
            self.type = OverlappedType.TYPE_NOT_STARTED
            raise _winapi._WinError()

    def WSASend(self ,handle, bufobj, flags):
        handle = _int2handle(handle)

        if self.type != OverlappedType.TYPE_NONE:
            raise _winapi._WinError() 
        self.write_buffer = bufobj
        self.type = OverlappedType.TYPE_WRITE
        self.handle = handle

        wsabuff = _ffi.new("WSABUF[1]")
        wsabuff[0].len = len(bufobj)
        wsabuff[0].buf = _ffi.new("CHAR[]", bufobj)
        nwritten = _ffi.new("LPDWORD")
        

        result = _winsock2.WSASend(handle, wsabuff, _int2dword(1), nwritten, flags, self.overlapped, _ffi.NULL)
        
        if result == SOCKET_ERROR:
            self.error = _kernel32.GetLastError()
        else:
            self.error = _winapi.ERROR_SUCCESS

        if self.error in [_winapi.ERROR_SUCCESS, _winapi.ERROR_IO_PENDING]:
            return None
        else:
            self.type = OverlappedType.TYPE_NOT_STARTED
            raise _winapi._WinError()

    def getresult(self, wait=False):
        return self.GetOverlappedResult(wait)
    
    def ConnectNamedPipe(self, handle):
        if self.type != OverlappedType.TYPE_NONE:
            raise _winapi._WinError()
        self.type  = OverlappedType.TYPE_CONNECT_NAMED_PIPE
        self.handle = _int2handle(handle)
        success = _kernel32.ConnectNamedPipe(self.handle, self.overlapped)
        
        if success:
            err = _winapi.ERROR_SUCCESS
        else:
            err = _kernel32.GetLastError()    
            self.error = err
        
        if err == _winapi.ERROR_IO_PENDING | _winapi.ERROR_SUCCESS:
            return False
        elif err == _winapi.ERROR_PIPE_CONNECTED:
            mark_as_completed(self.overlapped)
            return True
        else:
            raise _winapi._WinError()
    
    def ReadFile(self, handle, size):
        self.type = OverlappedType.TYPE_READ
        self.handle = _int2handle(handle)
        self.read_buffer = _ffi.new("CHAR[]", max(1,size))
        return self.do_ReadFile(self.handle, self.read_buffer, size)
    
    def do_ReadFile(self, handle, buf, size):
        nread = _ffi.new('DWORD[1]', [0])
        ret = _kernel32.ReadFile(handle, buf, size, nread, self.overlapped)
        if ret:
             err = _winapi.ERROR_SUCCESS
        else:
             err = _kernel32.GetLastError()
             self.error = err
           
        if err == _winapi.ERROR_BROKEN_PIPE:
            mark_as_completed(self.overlapped)
            raise _winapi._WinError()
        elif err in [_winapi.ERROR_SUCCESS, _winapi.ERROR_MORE_DATA, _winapi.ERROR_IO_PENDING]:
           return None
        else:
           self.type = OverlappedType.TYPE_NOT_STARTED
           raise _winapi._WinError()

    def WriteFile(self, handle, buffer):
        self.handle = _int2handle(handle)
        self.write_buffer = buffer
        written = _ffi.new('DWORD[1]', [0])

        # Check if we have already performed some IO
        if self.type != OverlappedType.TYPE_NONE:
            raise _winapi._WinError()

        self.type = OverlappedType.TYPE_WRITE
        
        ret = _kernel32.WriteFile(self.handle, self.write_buffer, len(self.write_buffer), written, self.overlapped)
        
        if ret:
            err = _winapi.ERROR_SUCCESS
        else:
            err = _winapi.GetLastError()

        self.error = err

        if err == _winapi.ERROR_SUCCESS or err == _winapi.ERROR_IO_PENDING:
            return None
        else:
            self.type = OverlappedType.TYPE_NOT_STARTED
            raise _winapi.WinError()
    
    @property
    def pending(self):
        return (not HasOverlappedIoCompleted(self.overlapped[0]) and
                self.type != OverlappedType.TYPE_NOT_STARTED)
    
    @property
    def address(self):
        return self.overlapped


def SetEvent(handle):
    ret = _kernel32.SetEvent(handle)
    if not ret:
       raise _winapi._WinError()

def mark_as_completed(overlapped):
    overlapped[0].Internal = 0
    if overlapped[0].hEvent != _ffi.NULL:
        SetEvent(overlapped[0].hEvent) 

def CreateEvent(eventattributes, manualreset, initialstate, name):
    event = _kernel32.CreateEventW(NULL, manualreset, initialstate, _Z(name))
    if not event:
        raise _winapi._WinError()
    return event

def CreateIoCompletionPort(handle, existingcompletionport, completionkey, numberofconcurrentthreads):
    completionkey = _int2intptr(completionkey)
    existingcompletionport = _int2handle(existingcompletionport)
    numberofconcurrentthreads = _int2dword(numberofconcurrentthreads)
    handle = _int2handle(handle)
    result = _kernel32.CreateIoCompletionPort(handle,
                                              existingcompletionport,
                                              completionkey, 
                                              numberofconcurrentthreads)
    if result == _ffi.NULL:
        raise _winapi._WinError()
    
    return result

def PostQueuedCompletionStatus(completionport, ms):
    raise _winapi._WinError()

def GetQueuedCompletionStatus(completionport, milliseconds):
    numberofbytes = _ffi.new('DWORD[1]', [0])
    completionkey  = _ffi.new('ULONG**')

    if completionport is None:
        raise _winapi._WinError()
    overlapped = _ffi.new("OVERLAPPED**")
    overlapped[0] = _ffi.NULL
    result = _kernel32.GetQueuedCompletionStatus(completionport, 
                                                 numberofbytes,
                                                 completionkey,
                                                 overlapped,
                                                 milliseconds)
    if result:
        err = _winapi.ERROR_SUCCESS 
    else:
        err = _kernel32.GetLastError()
    
    if overlapped[0] == _ffi.NULL:
        if err == _winapi.WAIT_TIMEOUT:
            return None
        return SetFromWindowsErr(err)

    return (err, numberofbytes, _handle2int(completionkey[0]), _ffi.addressof(overlapped[0][0]))

@_ffi.callback("void(void*, int)")
def post_to_queue_callback(lpparameter, timerorwaitfired):
    pdata = _ffi.cast("PostCallbackData *", lpparameter)
    ret = _kernel32.PostQueuedCompletionStatus(pdata.hCompletionPort, timerorwaitfired, _ffi.cast("ULONG_PTR",0), pdata.Overlapped)
    result = False
#    if not ret:
#         err = _winapi._WinError()


def RegisterWaitWithQueue(object, completionport, ovaddress, miliseconds):
    data = _ffi.new('PostCallbackData*')
    newwaitobject = _ffi.new("HANDLE*")
    data[0].hCompletionPort = completionport
    data[0].Overlapped = ovaddress
    ret = _kernel32.RegisterWaitForSingleObject(newwaitobject,
                                                object,
                                                _ffi.cast("WAITORTIMERCALLBACK",post_to_queue_callback),
                                                data,
                                                miliseconds, 
                                                _kernel32.WT_EXECUTEINWAITTHREAD | _kernel32.WT_EXECUTEONLYONCE)
    if not ret:
        raise _winapi._WinError()
    
    return _handle2int(newwaitobject[0])

def ConnectPipe(address):
    err = _winapi.ERROR_PIPE_BUSY
    waddress = _ffi.new("wchar_t[]", address)
    handle = _kernel32.CreateFileW(waddress, 
                            _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                            0,
                            _ffi.NULL,
                            _winapi.OPEN_EXISTING,
                            _winapi.FILE_FLAG_OVERLAPPED,
                            _ffi.NULL)
    err = _kernel32.GetLastError()
    
    if handle == INVALID_HANDLE_VALUE or err == _winapi.ERROR_PIPE_BUSY:
        raise _winapi._WinError()
        
    return _handle2int(handle)

def UnregisterWaitEx(handle, event):
    waithandle = _int2handle(handle)
    waitevent = _int2handle(event)
    
    ret = _kernel32.UnregisterWaitEx(waithandle, waitevent)
    
    if not ret:
        raise _winapi._WinError()

def UnregisterWait(handle):
    handle = _int2handle(handle)
    
    ret = _kernel32.UnregisterWait(handle)
    
    if not ret:
        raise _winapi._WinError()

# In CPython this function converts a windows error into a python object
# Not sure what we should do here.
def SetFromWindowsErr(error):
    return error


def HasOverlappedIoCompleted(overlapped):
    return (overlapped.Internal != STATUS_PENDING)


