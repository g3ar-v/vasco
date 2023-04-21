""" Message bus service for mycroft-core

The message bus facilitates inter-process communication between mycroft-core
processes. It implements a websocket server so can also be used by external
systems to integrate with the Mycroft system.
"""
import sys

from tornado import autoreload, web, ioloop

from core.lock import Lock  # creates/supports PID locking file
from core.messagebus.load_config import load_message_bus_config
from core.messagebus.service.event_handler import MessageBusEventHandler
from core.util import (
    reset_sigint_handler,
    create_daemon,
    wait_for_exit_signal
)
from core.util.log import LOG


def on_ready():
    LOG.info('Message bus service started!')


def on_error(e='Unknown'):
    LOG.info('Message bus failed to start ({})'.format(repr(e)))


def on_stopping():
    LOG.info('Message bus is shutting down...')


def main(ready_hook=on_ready, error_hook=on_error, stopping_hook=on_stopping):
    import tornado.options
    LOG.info('Starting message bus service...')
    reset_sigint_handler()
    lock = Lock("service")
    # Disable all tornado logging so mycroft loglevel isn't overridden
    tornado.options.parse_command_line(sys.argv + ['--logging=None'])

    def reload_hook():
        """ Hook to release lock when auto reload is triggered. """
        lock.delete()

    autoreload.add_reload_hook(reload_hook)
    config = load_message_bus_config()
    routes = [(config.route, MessageBusEventHandler)]
    application = web.Application(routes, debug=True)
    application.listen(config.port, config.host)
    create_daemon(ioloop.IOLoop.instance().start)
    ready_hook()
    wait_for_exit_signal()
    stopping_hook()


if __name__ == "__main__":
    main()