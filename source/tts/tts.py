import os
import os.path
import random
import re
from abc import ABCMeta, abstractmethod
from copy import deepcopy
from os.path import dirname, exists, isdir, join
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from warnings import warn

# from core.enclosure.api import EnclosureAPI
from source.api import SystemApi
from source.configuration import Configuration
from source.messagebus.message import Message
from source.util import (check_for_signal, create_signal, play_mp3, play_wav,
                         resolve_resource_file)
from source.util.file_utils import get_temp_path
from source.util.log import LOG
from source.util.metrics import Stopwatch
from source.util.plugins import load_plugin

from .cache import TextToSpeechCache, hash_sentence

_TTS_ENV = deepcopy(os.environ)
_TTS_ENV["PULSE_PROP"] = "media.role=phone"

EMPTY_PLAYBACK_QUEUE_TUPLE = (None, None, None, None, None)

SSML_TAGS = re.compile(r"<[^>]*>")
WHITESPACE_AFTER_PERIOD = re.compile(r"\b([A-za-z][\.])(\s+)")
SENTENCE_DELIMITERS = re.compile(r"(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\;|\?)\s")


def default_preprocess_utterance(utterance):
    """Default method for preprocessing core utterances for TTS.

    Args:
        utteance (str): Input utterance

    Returns:
        [str]: list of preprocessed sentences
    """

    utterance = WHITESPACE_AFTER_PERIOD.sub(r"\g<1>", utterance)
    chunks = SENTENCE_DELIMITERS.split(utterance)
    return chunks


class PlaybackThread(Thread):
    """Thread class for playing back tts audio and sending
    viseme data to enclosure.
    """

    def __init__(self, queue):
        super(PlaybackThread, self).__init__()
        self.queue = queue
        self.tts = []
        self.bus = None
        self.api = SystemApi()

        self._terminated = False
        self._processing_queue = False
        self.interrupted_utterance = None
        # self.enclosure = None
        self.p = None
        # Check if the tts shall have a ducking role set
        if Configuration.get().get("tts", {}).get("pulse_duck"):
            self.pulse_env = _TTS_ENV
        else:
            self.pulse_env = None


    def set_bus(self, bus):
        """Provide bus instance to the TTS Playback thread.

        Args:
            bus (BusClient): bus client
        """
        self.bus = bus

    def attach_tts(self, tts):
        """Add TTS to be cache checked."""
        self.tts.append(tts)

    def detach_tts(self, tts):
        """Remove TTS from cache check."""
        self.tts.remove(tts)

    def clear_queue(self):
        """Remove all pending playbacks."""
        while not self.queue.empty():
            self.queue.get()
        try:
            self.p.terminate()
        except Exception:
            pass

    def run(self):
        """Thread main loop. Get audio and extra data from queue and play.

        The queue messages is a tuple containing
        snd_type: 'mp3' or 'wav' telling the loop what format the data is in
        data: path to temporary audio data
        visemes: list of visemes to display while playing
        listen: if listening should be triggered at the end of the sentence.

        Playback of audio is started and the visemes are sent over the bus
        the loop then wait for the playback process to finish before starting
        checking the next position in queue.

        If the queue is empty the end_audio() is called possibly triggering
        listening.
        """
        while not self._terminated:
            try:
                (snd_type, data, visemes, ident, listen) = self.queue.get(timeout=2)
                if not self._processing_queue:
                    self._processing_queue = True
                    self.begin_audio()

                stopwatch = Stopwatch()
                with stopwatch:
                    if snd_type == "wav":
                        self.p = play_wav(data, environment=self.pulse_env)
                    elif snd_type == "mp3":
                        self.p = play_mp3(data, environment=self.pulse_env)
                    if self.p:
                        self.p.communicate()
                        self.p.wait()

                if self.queue.empty():
                    self.end_audio(listen)
                    self._processing_queue = False

            except Empty:
                pass
            except Exception as e:
                LOG.exception(e)
                if self._processing_queue:
                    self.end_audio(listen)
                    self._processing_queue = False

    # TODO: add dynamic source variable
    def begin_audio(self):
        """Perform befining of speech actions."""
        # Create signals informing start of speech
        if self.bus:
            context = {
                "client_name": "core_audio_pbthread",
            }
            self.bus.emit(
                Message("recognizer_loop:audio_output_start", context=context)
            )

        else:
            LOG.warning("Speech started before bus was attached.")

    # TODO: add dynamic source variable
    def end_audio(self, listen):
        """Perform end of speech output actions.

        Will inform the system that speech has ended and trigger the TTS's
        cache checks. Listening will be triggered if requested.

        Args:
            listen (bool): True if listening event should be emitted
        """
        if self.bus:
            # Send end of speech signals to the system
            context = {
                "client_name": "core_audio_pbthread",
            }
            self.bus.emit(Message("recognizer_loop:audio_output_end", context=context))

            if listen:
                self.bus.emit(Message("core.mic.listen", context=context))

            # Clear cache for all attached tts objects
            # This is basically the only safe time
            for tts in self.tts:
                tts.cache.curate()

            # This check will clear the filesystem IPC "signal"
            check_for_signal("isSpeaking")
        else:
            LOG.warning("Speech started before bus was attached.")

    def clear(self):
        """Clear all pending actions for the TTS playback thread."""
        self.clear_queue()

    def set_interrupted_utterance(self, value):
        self.interrupted_utterance = value

    def stop(self):
        """Stop thread"""
        self._terminated = True
        self.clear_queue()


class TTS(metaclass=ABCMeta):
    """TTS abstract class to be implemented by all TTS engines.

    It aggregates the minimum required parameters and exposes
    ``execute(sentence)`` and ``validate_ssml(sentence)`` functions.

    Args:
        lang (str):
        config (dict): Configuration for this specific tts engine
        validator (TTSValidator): Used to verify proper installation
        phonetic_spelling (bool): Whether to spell certain words phonetically
        ssml_tags (list): Supported ssml properties. Ex. ['speak', 'prosody']
    """

    queue = None
    playback = None

    def __init__(
        self,
        lang,
        config,
        validator,
        audio_ext="wav",
        phonetic_spelling=True,
        ssml_tags=None,
    ):
        super(TTS, self).__init__()
        self.bus = None  # initalized in "init" step
        self.lang = lang or "en-us"
        self.config = config
        self.validator = validator
        self.phonetic_spelling = phonetic_spelling
        self.audio_ext = audio_ext
        self.ssml_tags = ssml_tags or []

        self.voice = config.get("voice")
        self.filename = get_temp_path("tts.wav")
        self.enclosure = None
        self.interrupted_utterance = None
        random.seed()

        if TTS.queue is None:
            TTS.queue = Queue()
            TTS.playback = PlaybackThread(TTS.queue)
            TTS.playback.start()

        self.spellings = self.load_spellings()
        self.tts_name = type(self).__name__
        self.cache = TextToSpeechCache(self.config, self.tts_name, self.audio_ext)
        self.cache.clear()

    @property
    def available_languages(self) -> set:
        """Return languages supported by this TTS implementation in this state

        This property should be overridden by the derived class to advertise
        what languages that engine supports.

        Returns:
            set: supported languages
        """
        return set()

    def load_spellings(self):
        """Load phonetic spellings of words as dictionary."""
        path = join("text", self.lang.lower(), "phonetic_spellings.txt")
        spellings_file = resolve_resource_file(path)
        if not spellings_file:
            return {}
        try:
            with open(spellings_file) as f:
                lines = filter(bool, f.read().split("\n"))
            lines = [i.split(":") for i in lines]
            return {key.strip(): value.strip() for key, value in lines}
        except ValueError:
            LOG.exception("Failed to load phonetic spellings.")
            return {}

    def begin_audio(self):
        """Helper function for child classes to call in execute()."""
        # Create signals informing start of speech
        self.bus.emit(Message("recognizer_loop:audio_output_start"))

    def end_audio(self, listen=True):
        """Helper function for child classes to call in execute().

        Sends the recognizer_loop:audio_output_end message (indicating
        that speaking is done for the moment) as well as trigger listening
        if it has been requested. It also checks if cache directory needs
        cleaning to free up disk space.

        Args:
            listen (bool): indication if listening trigger should be sent.
        """
        context = {"client_name": "core_audio_tts", "source": "llm"}

        self.bus.emit(Message("recognizer_loop:audio_output_end", context=context))
        if listen:
            self.bus.emit(Message("core.mic.listen", context=context))

        self.cache.curate()
        # This check will clear the "signal"
        check_for_signal("isSpeaking")

    def init(self, bus):
        """Performs intial setup of TTS object.

        Args:
            bus:    messagebus connection
        """
        self.bus = bus
        TTS.playback.set_bus(bus)
        TTS.playback.attach_tts(self)
        # self.enclosure = EnclosureAPI(self.bus)
        # TTS.playback.enclosure = self.enclosure

    def get_tts(self, sentence, wav_file):
        """Abstract method that a tts implementation needs to implement.

        Should get data from tts.

        Args:
            sentence(str): Sentence to synthesize
            wav_file(str): output file

        Returns:
            tuple: (wav_file, phoneme)
        """
        pass

    def stream_tts(self, sententce):
        """Abstract method that a tts implementation needs to implement.

        Should stream synthesized speech.

        Args:
            sentence(str): Sentence to synthesize.
        """
        pass

    def modify_tag(self, tag):
        """Override to modify each supported ssml tag.

        Args:
            tag (str): SSML tag to check and possibly transform.
        """
        return tag

    @staticmethod
    def remove_ssml(text):
        """Removes SSML tags from a string.

        Args:
            text (str): input string

        Returns:
            str: input string stripped from tags.
        """
        return re.sub("<[^>]*>", "", text).replace("  ", " ")

    def validate_ssml(self, utterance):
        """Check if engine supports ssml, if not remove all tags.

        Remove unsupported / invalid tags

        Args:
            utterance (str): Sentence to validate

        Returns:
            str: validated_sentence
        """
        # if ssml is not supported by TTS engine remove all tags
        if not self.ssml_tags:
            return self.remove_ssml(utterance)

        # find ssml tags in string
        tags = SSML_TAGS.findall(utterance)

        for tag in tags:
            if any(supported in tag for supported in self.ssml_tags):
                utterance = utterance.replace(tag, self.modify_tag(tag))
            else:
                # remove unsupported tag
                utterance = utterance.replace(tag, "")

        # return text with supported ssml tags only
        return utterance.replace("  ", " ")

    def preprocess_utterance(self, utterance):
        """Preprocess utterance into list of chunks suitable for the TTS.

        Perform general chunking and TTS specific chunking.
        """
        # Remove any whitespace present after the period,
        # if a character (only alpha) ends with a period
        # ex: A. Lincoln -> A.Lincoln
        # so that we don't split at the period
        chunks = default_preprocess_utterance(utterance)
        result = []
        for chunk in chunks:
            result += self._preprocess_sentence(chunk)
        return result

    def _preprocess_sentence(self, sentence):
        """Default preprocessing is no preprocessing.

        This method can be overridden to create chunks suitable to the
        TTS engine in question.

        Args:
            sentence (str): sentence to preprocess

        Returns:
            list: list of sentence parts
        """
        return [sentence]

    def execute(self, sentence, ident=None, listen=True):
        """Convert sentence to speech, preprocessing out unsupported ssml

        The method caches results if possible using the hash of the
        sentence.

        Args:
            sentence: (str) Sentence to be spoken
            ident: (str) Id reference to current interaction
            listen: (bool) True if listen should be triggered at the end
                    of the utterance.
        """
        sentence = self.validate_ssml(sentence)

        create_signal("isSpeaking")
        self._execute(sentence, ident, listen)

    def _execute(self, sentence, ident, listen):
        if self.phonetic_spelling:
            for word in re.findall(r"[\w']+", sentence):
                if word.lower() in self.spellings:
                    sentence = sentence.replace(word, self.spellings[word.lower()])

        # TODO: 22.02 This is no longer needed and can be removed
        # Just kept for compatibility for now
        chunks = self._preprocess_sentence(sentence)
        # Apply the listen flag to the last chunk, set the rest to False
        chunks = [
            (chunks[i], listen if i == len(chunks) - 1 else False)
            for i in range(len(chunks))
        ]

        for sentence, l in chunks:
            sentence_hash = hash_sentence(sentence)
            if sentence_hash in self.cache:
                audio_file, phoneme_file = self._get_sentence_from_cache(sentence_hash)
                if phoneme_file is None:
                    phonemes = None
                else:
                    phonemes = phoneme_file.load()

            else:
                audio_file = self.cache.define_audio_file(sentence_hash)
                # TODO 21.08: remove mutation of audio_file.path.
                returned_file, phonemes = self.get_tts(sentence, str(audio_file.path))
                # Convert to Path as needed
                returned_file = Path(returned_file)
                if returned_file != audio_file.path:
                    warn(
                        DeprecationWarning(
                            f"{self.tts_name} is saving files "
                            "to a different path than requested. If you are "
                            "the maintainer of this plugin, please adhere to "
                            "the file path argument provided. Modified paths "
                            "will be ignored in a future release."
                        )
                    )
                    audio_file.path = returned_file
                if phonemes:
                    phoneme_file = self.cache.define_phoneme_file(sentence_hash)
                    phoneme_file.save(phonemes)
                else:
                    phoneme_file = None
                self.cache.cached_sentences[sentence_hash] = (audio_file, phoneme_file)
            viseme = self.viseme(phonemes) if phonemes else None
            TTS.queue.put((self.audio_ext, str(audio_file.path), viseme, ident, l))

    def _get_sentence_from_cache(self, sentence_hash):
        cached_sentence = self.cache.cached_sentences[sentence_hash]
        audio_file, phoneme_file = cached_sentence
        LOG.info("Found {} in TTS cache".format(audio_file.name))

        return audio_file, phoneme_file

    def viseme(self, phonemes):
        """Create visemes from phonemes.

        May be implemented to convert TTS phonemes into mouth
        visuals.

        Args:
            phonemes (str): String with phoneme data

        Returns:
            list: visemes
        """
        return None


class TTSValidator(metaclass=ABCMeta):
    """TTS Validator abstract class to be implemented by all TTS engines.

    It exposes and implements ``validate(tts)`` function as a template to
    validate the TTS engines.
    """

    def __init__(self, tts):
        self.tts = tts

    def validate(self):
        self.validate_dependencies()
        self.validate_instance()
        self.validate_filename()
        self.validate_lang()
        self.validate_connection()

    def validate_dependencies(self):
        """Determine if all the TTS's external dependencies are satisfied."""
        pass

    def validate_instance(self):
        clazz = self.get_tts_class()
        if not isinstance(self.tts, clazz):
            raise AttributeError("tts must be instance of " + clazz.__name__)

    def validate_filename(self):
        filename = self.tts.filename
        if not (filename and filename.endswith(".wav")):
            raise AttributeError("file: %s must be in .wav format!" % filename)

        dir_path = dirname(filename)
        if not (exists(dir_path) and isdir(dir_path)):
            raise AttributeError("filename: %s is not valid!" % filename)

    @abstractmethod
    def validate_lang(self):
        """Ensure the TTS supports current language."""

    @abstractmethod
    def validate_connection(self):
        """Ensure the TTS can connect to it's backend.

        This can mean for example being able to launch the correct executable
        or contact a webserver.
        """

    @abstractmethod
    def get_tts_class(self):
        """Return TTS class that this validator is for."""


def load_tts_plugin(module_name):
    """Wrapper function for loading tts plugin.

    Args:
        (str) tts module name from config
    Returns:
        class: found tts plugin class
    """
    return load_plugin("core.plugin.tts", module_name)


class TTSFactory:
    """Factory class instantiating the configured TTS engine.

    The factory can select between a range of built-in TTS engines and also
    from TTS engine plugins.
    """

    from source.tts.elevenlabs_tts import ElevenLabsTTS
    from source.tts.mimic3_tts import Mimic3
    from source.tts.openai_tts import OpenAITTS

    CLASSES = {"mimic3": Mimic3, "elevenlabs": ElevenLabsTTS, "openai": OpenAITTS}

    @staticmethod
    def create():
        """Factory method to create a TTS engine based on configuration.

        The configuration file ``core.conf`` contains a ``tts`` section with
        the name of a TTS module to be read by this method.

        "tts": {
            "module": <engine_name>
        }
        """
        config = Configuration.get().get("audio")
        lang = config.get("lang", "en-us")
        tts_module = config.get("tts").get("module")
        tts_config = config.get("tts", {}).get(tts_module, {})
        tts_lang = tts_config.get("lang", lang)
        try:
            if tts_module in TTSFactory.CLASSES:
                clazz = TTSFactory.CLASSES[tts_module]
            else:
                clazz = load_tts_plugin(tts_module)
                LOG.info("LOADED PLUGIN {}".format(tts_module))
            if clazz is None:
                raise ValueError("TTS module not found")

            tts = clazz(tts_lang, tts_config)
            tts.validator.validate()
        except Exception:
            # Fallback to mimic if an error occurs while loading.
            if tts_module != "mimic3":
                LOG.exception(
                    "The selected TTS backend couldn't be loaded. "
                    "Falling back to Mimic"
                )
                clazz = TTSFactory.CLASSES.get("mimic3")
                tts_config = config.get("tts", {}).get("mimic3", {})
                tts = clazz(tts_lang, tts_config)
                tts.validator.validate()
            else:
                LOG.exception("The TTS could not be loaded.")
                raise
        return tts
