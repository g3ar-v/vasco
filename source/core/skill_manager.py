# Copyright 2017 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
""""Load, update and manage skills on this device."""
import os
from glob import glob
from inspect import signature
from threading import Event, Lock, Thread
from time import monotonic, sleep

from source import Message
from source.configuration import Configuration
from source.configuration.locations import get_core_config_dir
from source.messagebus.client import MessageBusClient
from source.util.file_utils import FileWatcher
from source.util.log import LOG

from .skill_loader import SkillLoader

SKILL_MAIN_MODULE = "__init__.py"


class UploadQueue:
    """Queue for holding loaders with data that still needs to be uploaded.

    This queue can be used during startup to capture all loaders
    and then processing can be triggered at a later stage when the system is
    connected to the backend.

    After all queued settingsmeta has been processed and the queue is empty
    the queue will set the self.started flag.
    """

    def __init__(self):
        self._queue = []
        self.started = False
        self.lock = Lock()

    def start(self):
        """Start processing of the queue."""
        self.started = True
        self.send()

    def stop(self):
        """Stop the queue, and hinder any further transmissions."""
        self.started = False

    def send(self):
        """Loop through all stored loaders triggering settingsmeta upload."""
        with self.lock:
            queue = self._queue
            self._queue = []
        if queue:
            LOG.info("New Settings meta to upload.")
            for loader in queue:
                if self.started:
                    loader.instance.settings_meta.upload()
                else:
                    break

    def __len__(self):
        return len(self._queue)

    def put(self, loader):
        """Append a skill loader to the queue.

        If a loader is already present it's removed in favor of the new entry.
        """
        if self.started:
            LOG.info("Updating settings meta during runtime...")
        with self.lock:
            # Remove existing loader
            self._queue = [e for e in self._queue if e != loader]
            self._queue.append(loader)


def _shutdown_skill(instance):
    """Shutdown a skill.

    Call the default_shutdown method of the skill, will produce a warning if
    the shutdown process takes longer than 1 second.

    Args:
        instance (SystemSkill): Skill instance to shutdown
    """
    try:
        ref_time = monotonic()
        # Perform the shutdown
        instance.default_shutdown()

        shutdown_time = monotonic() - ref_time
        if shutdown_time > 1:
            LOG.warning(f"{instance.skill_id} shutdown took {shutdown_time} seconds")
    except Exception:
        LOG.exception(f"Failed to shut down skill: {instance.skill_id}")


class SkillManager(Thread):
    _msm = None

    def __init__(
        self,
        bus,
        watchdog=None,
    ):
        """Constructor

        Args:
            bus (event emitter): messagebus connection
            watchdog (callable): optional watchdog function
        """
        super(SkillManager, self).__init__()
        self.bus = bus
        self._settings_watchdog = None
        # Set watchdog to argument or function returning None
        self._watchdog = watchdog or (lambda: None)

        self._lock = Lock()
        self._setup_event = Event()
        self._stop_event = Event()
        self._connected_event = Event()
        self._internet_loaded = Event()
        self._allow_state_reloads = True
        self.upload_queue = UploadQueue()

        self._alive_status = False  # Set True when all skills have been loaded
        self._loaded_status = False  # True after all skills has loaded

        self.config = Configuration.get()

        self.skills_dir_path = self.config.get("skills").get("directory")
        self.skill_loaders = {}
        self.plugin_skills = {}
        self.initial_load_complete = False
        self.num_install_retries = 0
        self.empty_skill_dirs = set()  # Save a record of empty skill dirs.

        self._define_message_bus_events()
        self.daemon = True

        # self.status.bind(self.bus)
        self._init_filewatcher()

    def _init_filewatcher(self):
        # monitor skill settings files for changes
        sspath = f"{get_core_config_dir('core')}/skills/"
        os.makedirs(sspath, exist_ok=True)
        self._settings_watchdog = FileWatcher(
            [sspath],
            callback=self._handle_settings_file_change,
            recursive=True,
            ignore_creation=True,
        )

    def _handle_settings_file_change(self, path: str):
        if path.endswith("/settings.json"):
            skill_id = path.split("/")[-2]
            LOG.info(f"skill settings.json change detected for {skill_id}")
            self.bus.emit(
                Message("core.skills.settings_changed", {"skill_id": skill_id})
            )

    def _define_message_bus_events(self):
        """Define message bus events with handlers defined in this class."""
        # Update upon request
        self.bus.on("skill.converse.request", self.handle_converse_request)

        # Update on initial connection
        self.bus.on("core.internet.connected", lambda x: self._connected_event.set())

        self.bus.on("skillmanager.list", self.send_skill_list)
        self.bus.on("skillmanager.deactivate", self.deactivate_skill)
        self.bus.on("skillmanager.keep", self.deactivate_except)
        self.bus.on("skillmanager.activate", self.activate_skill)
        # self.bus.on("core.skills.initialized", self.handle_check_device_readiness)
        self.bus.on("core.skills.trained", self.handle_initial_training)

        # load skills waiting for connectivity
        # self.bus.on("core.internet.connected", self.handle_internet_connected)
        # self.bus.on("core.internet.disconnected", self.handle_internet_disconnected)

    def handle_check_device_readiness(self, message):
        ready = False
        while not ready:
            try:
                ready = self.is_device_ready()
            except TimeoutError:
                LOG.warning("System should already have reported ready!")
                sleep(5)

        LOG.info("System is all loaded and ready to roll!")
        self.bus.emit(message.reply("core.ready"))
        is_ready = False
        # different setups will have different needs
        # eg, a server does not care about audio
        # pairing -> device is paired
        # internet -> device is connected to the internet - NOT IMPLEMENTED
        # skills -> skills reported ready
        # speech -> stt reported ready
        # audio -> audio playback reported ready
        # gui -> gui websocket reported ready - NOT IMPLEMENTED
        # enclosure -> enclosure/HAL reported ready - NOT IMPLEMENTED
        services = {k: False for k in self.config.get("ready_settings", ["skills"])}
        start = monotonic()
        while not is_ready:
            # is_ready = self.check_services_ready(services)
            if is_ready:
                break
            elif monotonic() - start >= 60:
                raise TimeoutError(
                    f"Timeout waiting for services start. services={services}"
                )
            else:
                sleep(3)
        return is_ready

    # NOTE: There might be an error from here on what config file is being accessed
    # check it out later and clean
    @property
    def skills_config(self):
        return self.config["skills"]

    def _get_internal_skill_bus(self):
        if not self.config["websocket"].get("shared_connection", True):
            # see BusBricker skill to understand why this matters
            # any skill can manipulate the bus from other skills
            # this patch ensures each skill gets it's own
            # connection that can't be manipulated by others
            # https://github.com/EvilJarbas/BusBrickerSkill
            bus = MessageBusClient()
            bus.run_in_thread()
        else:
            bus = self.bus
        return bus

    def handle_initial_training(self, message):
        self.initial_load_complete = True

    def run(self):
        """Load skills and update periodically from disk and internet."""
        self._remove_git_locks()
        LOG.debug("removed git locks")
        # self.load_priority()
        self._load_on_startup()

        # wait for initial intents training
        LOG.debug("Waiting for initial training")
        while not self.initial_load_complete:
            sleep(0.5)

        if not self._connected_event.is_set():
            LOG.info("Offline Skills loaded, waiting for Internet to load more!")

        # Scan the file folder that contains Skills.  If a Skill is updated,
        # unload the existing version from memory and reload from the disk.
        while not self._stop_event.is_set():
            try:
                self._unload_removed_skills()
                self._reload_modified_skills()
                self._load_new_skills()
                self._watchdog()
                sleep(2)  # Pause briefly before beginning next scan
            except Exception:
                LOG.exception(
                    "Something really unexpected has occurred "
                    "and the skill manager loop safety harness was "
                    "hit."
                )
                sleep(30)

    def _remove_git_locks(self):
        """If git gets killed from an abrupt shutdown it leaves lock files."""

        lock_path = os.path.join(self.skills_dir_path, "/.git/index.lock")
        for i in glob(lock_path):
            LOG.warning("Found and removed git lock file: " + i)
            os.remove(i)

    def _load_on_startup(self):
        """Handle initial skill load."""
        self._load_new_skills()
        self.bus.emit(Message("core.skills.initialized"))
        self._loaded_status = True

    def _reload_modified_skills(self):
        """Handle reload of recently changed skill(s)"""
        for skill_dir in self._get_skill_directories():
            try:
                skill_loader = self.skill_loaders.get(skill_dir)
                if skill_loader is not None and skill_loader.reload_needed():
                    # If reload succeed add settingsmeta to upload queue
                    if skill_loader.reload():
                        self.upload_queue.put(skill_loader)
            except Exception:
                LOG.exception(
                    "Unhandled exception occured while "
                    "reloading {}".format(skill_dir)
                )

    def _load_new_skills(self):
        """Handle load of skills installed since startup."""
        for skill_dir in self._get_skill_directories():
            if skill_dir not in self.skill_loaders:
                loader = self._load_skill(skill_dir)
                if loader:
                    self.upload_queue.put(loader)
        self._alive_status = True

    def _get_skill_loader(self, skill_directory, init_bus=True):
        bus = None
        if init_bus:
            bus = self._get_internal_skill_bus()
        return SkillLoader(bus, skill_directory)

    def _load_skill(self, skill_directory):
        skill_loader = self._get_skill_loader(skill_directory)
        try:
            load_status = skill_loader.load()
        except Exception:
            LOG.exception(f"Load of skill {skill_directory} failed!")
            load_status = False
        finally:
            self.skill_loaders[skill_directory] = skill_loader

        return skill_loader if load_status else None

    def _unload_skill(self, skill_dir):
        if skill_dir in self.skill_loaders:
            skill = self.skill_loaders[skill_dir]
            LOG.info(f"removing {skill.skill_id}")
            try:
                skill.unload()
            except Exception:
                LOG.exception("Failed to shutdown skill " + skill.id)
            del self.skill_loaders[skill_dir]

    def _get_skill_directories(self):
        skill_glob = glob(os.path.join(self.skills_dir_path, "*/"))

        skill_directories = []
        for skill_dir in skill_glob:
            # TODO: all python packages must have __init__.py!  Better way?
            # check if folder is a skill (must have __init__.py)
            if SKILL_MAIN_MODULE in os.listdir(skill_dir):
                skill_directories.append(skill_dir.rstrip("/"))
                if skill_dir in self.empty_skill_dirs:
                    self.empty_skill_dirs.discard(skill_dir)
            else:
                if skill_dir not in self.empty_skill_dirs:
                    self.empty_skill_dirs.add(skill_dir)
                    LOG.debug("Found skills directory with no skill: " + skill_dir)

        return skill_directories

    def _unload_removed_skills(self):
        """Shutdown removed skills."""
        skill_dirs = self._get_skill_directories()
        # Find loaded skills that don't exist on disk
        removed_skills = [s for s in self.skill_loaders.keys() if s not in skill_dirs]
        for skill_dir in removed_skills:
            skill = self.skill_loaders[skill_dir]
            LOG.info("removing {}".format(skill.skill_id))
            try:
                skill.unload()
            except Exception:
                LOG.exception("Failed to shutdown skill " + skill.id)
            del self.skill_loaders[skill_dir]

    def is_alive(self, message=None):
        """Respond to is_alive status request."""
        return self._alive_status

    def is_all_loaded(self, message=None):
        """Respond to all_loaded status request."""
        return self._loaded_status

    def send_skill_list(self, _):
        """Send list of loaded skills."""
        try:
            message_data = {}
            for skill_dir, skill_loader in self.skill_loaders.items():
                message_data[skill_loader.skill_id] = dict(
                    active=skill_loader.active and skill_loader.loaded,
                    id=skill_loader.skill_id,
                )
            self.bus.emit(Message("core.skills.list", data=message_data))
        except Exception:
            LOG.exception("Failed to send skill list")

    def deactivate_skill(self, message):
        """Deactivate a skill."""
        try:
            skills = {**self.skill_loaders, **self.plugin_skills}
            for skill_loader in skills.values():
                if message.data["skill"] == skill_loader.skill_id:
                    LOG.info("Deactivating skill: " + skill_loader.skill_id)
                    skill_loader.deactivate()
        except Exception:
            LOG.exception("Failed to deactivate " + message.data["skill"])

    def deactivate_except(self, message):
        """Deactivate all skills except the provided."""
        try:
            skill_to_keep = message.data["skill"]
            LOG.info("Deactivating all skills except {}".format(skill_to_keep))
            loaded_skill_file_names = [
                os.path.basename(skill_dir) for skill_dir in self.skill_loaders
            ]
            if skill_to_keep in loaded_skill_file_names:
                for skill in self.skill_loaders.values():
                    if skill.skill_id != skill_to_keep:
                        skill.deactivate()
            else:
                LOG.info("Couldn't find skill " + message.data["skill"])
        except Exception:
            LOG.exception("An error occurred during skill deactivation!")

    def activate_skill(self, message):
        """Activate a deactivated skill."""
        try:
            for skill_loader in self.skill_loaders.values():
                if (
                    message.data["skill"] in ("all", skill_loader.skill_id)
                    and not skill_loader.active
                ):
                    skill_loader.activate()
        except Exception:
            LOG.exception("Couldn't activate skill")

    def stop(self):
        """Tell the manager to shutdown."""
        # self.status.set_stopping()
        self._stop_event.set()

        # Do a clean shutdown of all skills
        for skill_loader in self.skill_loaders.values():
            if skill_loader.instance is not None:
                _shutdown_skill(skill_loader.instance)

        if self._settings_watchdog:
            self._settings_watchdog.shutdown()

    def handle_converse_request(self, message):
        """Check if the targeted skill id can handle conversation

        If supported, the conversation is invoked.
        """
        skill_id = message.data["skill_id"]

        # loop trough skills list and call converse for skill with skill_id
        skill_found = False
        for skill_loader in self.skill_loaders.values():
            if skill_loader.skill_id == skill_id:
                skill_found = True
                if not skill_loader.loaded:
                    error_message = "converse requested but skill not loaded"
                    self._emit_converse_error(message, skill_id, error_message)
                    break
                try:
                    # check the signature of a converse method
                    # to either pass a message or not
                    if len(signature(skill_loader.instance.converse).parameters) == 1:
                        result = skill_loader.instance.converse(message=message)
                    else:
                        utterances = message.data["utterances"]
                        lang = message.data["lang"]
                        result = skill_loader.instance.converse(
                            utterances=utterances, lang=lang
                        )
                    self._emit_converse_response(result, message, skill_loader)
                except Exception:
                    error_message = "exception in converse method"
                    LOG.exception(error_message)
                    self._emit_converse_error(message, skill_id, error_message)
                finally:
                    break

        if not skill_found:
            error_message = "skill id does not exist"
            self._emit_converse_error(message, skill_id, error_message)

    def _emit_converse_error(self, message, skill_id, error_msg):
        """Emit a message reporting the error back to the intent service."""
        reply = message.reply(
            "skill.converse.response", data=dict(skill_id=skill_id, error=error_msg)
        )
        self.bus.emit(reply)

    def _emit_converse_response(self, result, message, skill_loader):
        reply = message.reply(
            "skill.converse.response",
            data=dict(skill_id=skill_loader.skill_id, result=result),
        )
        self.bus.emit(reply)
