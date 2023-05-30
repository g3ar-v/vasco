from elevenlabs import generate, set_api_key
from elevenlabs.api import Voices
from pathlib import Path
from core.configuration import Configuration
from .tts import TTS, TTSValidator
from core.util.log import LOG


class ElevenLabsTTS(TTS):
    def __init__(self, lang, config):
        super(ElevenLabsTTS, self).__init__(lang, config, ElevenLabsTTSValidator(self))
        self.config = Configuration.get().get('tts', {}).get('elevenlabs', {})
        self.voice_id: str = self.config.get('voice_id')
        self.api_key = self.config.get('api_key')
        self.stability = self.config.get('stability')
        self.similarity_boost = self.config.get('similarity_boost')
        set_api_key(self.api_key)
        voices = Voices.from_api()
        # self.voice = voices[voices.index(self.voice_id)]
        # dynamically select based on config
        self.voice = voices[3]
        self.voice.settings.stability = self.stability
        self.voice.settings.similarity_boost = self.similarity_boost
        # self.type = 'mp3'

    def get_tts(self, sentence, wav_file):
        audio = generate(
            text=sentence,
            voice=self.voice
        )
        Path(wav_file).write_bytes(audio)
        # LOG.info(os.path.dirname(os.path.realpath(__file__)))
        # save(audio, "audio.wav")
        LOG.info(wav_file)
        return (wav_file, None)


class ElevenLabsTTSValidator(TTSValidator):

    def __init__(self, tts):
        super(ElevenLabsTTSValidator, self).__init__(tts)

    def validate_lang(self):
        # Assuming Eleven Labs API supports the language set in Mycroft
        return True

    def validate_connection(self):
        # Assuming the API key and voice ID are correct
        return True

    def get_tts_class(self):
        return ElevenLabsTTS