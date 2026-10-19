"""Bounded single-producer cache claims shared by threads and processes."""
from contextlib import ExitStack,contextmanager
import time


@contextmanager
def writer(paths,*,wait_seconds=120):
    from . import control,file_use
    deadline=time.monotonic()+wait_seconds
    with ExitStack() as held:
        while True:
            control.check()
            try:held.enter_context(file_use.pin(paths,exclusive=True))
            except file_use.FileBusy:
                if time.monotonic()>=deadline:raise
                time.sleep(min(.1,max(0,deadline-time.monotonic())))
            else:break
        # Do not catch a FileBusy from the consumer body and repeat its work.
        file_use.assert_current()
        yield
