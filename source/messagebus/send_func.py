from websocket import create_connection

from source.configuration import Configuration
from source.messagebus.client import MessageBusClient
from source.messagebus.message import Message


def send(message_to_send, data_to_send=None):
    """Send a single message over the websocket.

    Args:
        message_to_send (str): Message to send
        data_to_send (dict): data structure to go along with the
            message, defaults to empty dict.
    """
    data_to_send = data_to_send or {}

    # Calculate the standard Mycroft messagebus websocket address
    config = Configuration.get(cache=False, remote=False)
    config = config.get("websocket")
    url = MessageBusClient.build_url(
        config.get("host"),
        config.get("port"),
        config.get("route"),
        config.get("ssl")
    )

    # Send the provided message/data
    ws = create_connection(url)
    packet = Message(message_to_send, data_to_send).serialize()
    ws.send(packet)
    ws.close()
