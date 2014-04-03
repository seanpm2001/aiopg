import asyncio

import psycopg2
from psycopg2.extensions import (
    POLL_OK, POLL_READ, POLL_WRITE, POLL_ERROR)
from .exceptions import UnknownPollError, ConnectionClosedError


__all__ = ('connect',)


ALLOWED_ARGS = {'host', 'hostaddr', 'port', 'dbname', 'user',
                'password', 'connect_timeout', 'client_encoding',
                'options', 'application_name',
                'fallback_application_name', 'keepalives',
                'keepalives_idle', 'keepalives_interval',
                'keepalives_count', 'tty', 'sslmode', 'requiressl',
                'sslcompression', 'sslcert', 'sslkey', 'sslrootcert',
                'sslcrl', 'requirepeer', 'krbsrvname', 'gsslib',
                'service', 'database', 'connection_factory', 'cursor_factory'}


@asyncio.coroutine
def connect(dsn=None, *,
            loop=None, **kwargs):
    """XXX"""

    if loop is None:
        loop = asyncio.get_event_loop()

    for k in kwargs:
        if k not in ALLOWED_ARGS:
            raise TypeError("connect() got unexpected keyword argument '{}'"
                            .format(k))

    waiter = asyncio.Future(loop=loop)
    conn = Connection(dsn, loop, waiter, **kwargs)
    yield from waiter
    return conn


class Connection:
    """Psycopg connection wrapper class.
    :param string dsn:
    :param psycopg2.extensions.cursor cursor_factory: argument can be used
        to create non-standard cursors
    :param psycopg2.extensions.connection connection_factory: class is usually
        sub-classed only to provide an easy way to create customized cursors
        but other uses are possible
    :param asyncio.EventLoop loop: A list or tuple with query parameters.
            Defaults to an empty tuple."""
    def __init__(self, dsn, loop, waiter,
                 **kwargs):

        self._loop = loop

        self._conn = psycopg2.connect(
            dsn,
            async=True,
            **kwargs)
        self._fileno = self._conn.fileno()
        self._waiter = waiter
        self._ready(None)

    def _ready(self, action):
        assert self._waiter is not None, "BAD STATE"
        print('READY', action)

        if action == None:
            pass
        elif action == 'writing':
            self._loop.remove_writer(self._fileno)
        elif action == 'reading':
            self._loop.remove_reader(self._fileno)
        else:
            self._fatal_error(RuntimeError("Unknown action {!r}"
                                           .format(action)))

        try:
            state = self._conn.poll()
            print('READY STATE', state)
        except (psycopg2.Warning, psycopg2.Error) as error:
            self._waiter.set_exception(error)
            self._waiter = None
        else:
            if state == POLL_OK:
                print('READY DONE')
                self._waiter.set_result(None)
                self._waiter = None
            elif state == POLL_READ:
                print('READY READ')
                self._loop.add_reader(self._fileno, self._ready,
                                      'reading')
            elif state == POLL_WRITE:
                print('READY WRITE')
                self._loop.add_writer(self._fileno, self._ready,
                                      'writing')
            elif state == POLL_ERROR:
                print('READY ERROR')
                self._fatal_error(psycopg2.OperationalError(
                    "aiopg poll() returned {}".format(state)))
            else:
                self._fatal_error(UnknownPollError())

    def _fatal_error(self, exc, message='Fatal error on aiopg connetion'):
        # Should be called from exception handler only.
        self._loop.call_exception_handler({
            'message': message,
            'exception': exc,
            'transport': self,
            'protocol': self._protocol,
            })
        self._force_close(exc)

    @asyncio.coroutine
    def _poll(self):
        assert self._waiter is not None
        assert self._conn.isexecuted(), ("Underlying connection "
                                         "is not executing, is it async?")
        self._ready(None)
        try:
            yield from self._waiter
        finally:
            self._waiter = None

    def _create_waiter(self, func_name):
        if not self._conn:
            raise ConnectionClosedError()
        if self._waiter is not None:
            raise RuntimeError('%s() called while another coroutine is '
                               'already waiting for incoming data' % func_name)
        self._waiter = asyncio.Future(loop=self._loop)

    @asyncio.coroutine
    def cursor(self, name=None, cursor_factory=None,
               scrollable=None, withhold=False):
        """XXX"""
        self._create_waiter('cursor')
        impl = self._conn.cursor(name=name, cursor_factory=cursor_factory,
                                 scrollable=scrollable, withhold=withhold)
        yield from self._poll()
        return Cursor(self, impl)

    # FIXME: add transaction and TPC methods

    def close(self):
        """Remove the connection from the event_loop and close it."""
        if self._conn is None:
            return
        self._loop.remove_reader(self._fileno)
        self._loop.remove_writer(self._fileno)
        self._conn.close()
        self._conn = None
