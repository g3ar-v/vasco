# Expose core  modules to skills and other programs
from os.path import abspath, dirname, join

from core.api import Api
from core.messagebus.message import Message
from core.skills.context import adds_context, removes_context
from core.skills import (MycroftSkill, FallbackSkill,
                         intent_handler, intent_file_handler)
from core.skills.intent_service import AdaptIntent
from core.util.log import LOG

CORE_ROOT_PATH = abspath(join(dirname(__file__), '..'))

__all__ = ['CORE_ROOT_PATH',
           'Api',
           'Message',
           'adds_context',
           'removes_context',
           'MycroftSkill',
           'FallbackSkill',
           'intent_handler',
           'intent_file_handler',
           'AdaptIntent']

LOG.init()  # read log level from config