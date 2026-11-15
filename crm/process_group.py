"""Windows Job Object: owns only this launch's processes and all their children."""
import ctypes
from ctypes import wintypes as w
import os


class BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', w.DWORD), ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t),
                ('active', w.DWORD), ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]


class IOCounts(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', BasicLimits), ('io', IOCounts), ('process_memory', ctypes.c_size_t),
                ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]


def api():
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]; k.CreateJobObjectW.restype = w.HANDLE
    k.OpenJobObjectW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]; k.OpenJobObjectW.restype = w.HANDLE
    k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    k.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)]
    k.GetCurrentProcess.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    return k


class ProcessGroup:
    def __init__(self, name):
        self.k = api()
        self.handle = self.k.CreateJobObjectW(None, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.k.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, proc):
        process = int(proc._handle)
        inside = w.BOOL()
        self.k.IsProcessInJob(process, self.handle, ctypes.byref(inside))
        if not inside.value and not self.k.AssignProcessToJobObject(self.handle, process):
            # The wrapper can join the same job between the check and assignment.
            self.k.IsProcessInJob(process, self.handle, ctypes.byref(inside))
            if not inside.value:
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None


def join_group():
    """The venv redirector's child joins too, before it starts any workload."""
    k = api()
    handle = k.OpenJobObjectW(0x0001 | 0x0004, False, os.environ['MATILDA_JOB'])
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        inside = w.BOOL()
        if not k.IsProcessInJob(k.GetCurrentProcess(), handle, ctypes.byref(inside)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not inside.value and not k.AssignProcessToJobObject(handle, k.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        # Only the supervisor retains a job handle. Its exit releases all children.
        k.CloseHandle(handle)
