"""Nonblocking process lock; closing the file releases it on every platform."""
import errno
import os


def acquire(file):
    if os.name == 'nt':
        import msvcrt
        # Lock byte zero, independent of append position or existing lock-file contents.
        file.seek(0)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise BlockingIOError('Another process holds this lock') from exc
            raise
    else:
        import fcntl
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
