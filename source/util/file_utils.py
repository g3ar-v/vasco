"""file utils.

This module contains functions handling CORE's resource files and things like
accessing and curating CORE's cache.
"""

import os
import tempfile
from os.path import dirname
from stat import S_ISREG, ST_MODE, ST_MTIME, ST_SIZE
from threading import RLock
from typing import List

import psutil
import xdg.BaseDirectory
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .log import LOG

# from core import CORE_ROOT_PATH


class FileWatcher:
    def __init__(
        self,
        files: List[str],
        callback: callable,
        recursive: bool = False,
        ignore_creation: bool = False,
    ):
        """
        Initialize a FileWatcher to monitor the specified files for changes
        @param files: list of paths to monitor for file changes
        @param callback: function to call on file change with modified file path
        @param recursive: If true, recursively include directory contents
        @param ignore_creation: If true, ignore file creation events
        """
        self.observer = Observer()
        self.handlers = []
        for file_path in files:
            if os.path.isfile(file_path):
                watch_dir = dirname(file_path)
            else:
                watch_dir = file_path
            self.observer.schedule(
                FileEventHandler(file_path, callback, ignore_creation),
                watch_dir,
                recursive=recursive,
            )
        self.observer.start()

    def shutdown(self):
        """
        Remove observer scheduled events and stop the observer.
        """
        self.observer.unschedule_all()
        self.observer.stop()


class FileEventHandler(FileSystemEventHandler):
    def __init__(
        self, file_path: str, callback: callable, ignore_creation: bool = False
    ):
        """
        Create a handler for file change events
        @param file_path: file_path being watched Unused(?)
        @param callback: function to call on file change with modified file path
        @param ignore_creation: if True, only track file modification events
        """
        super().__init__()
        self._callback = callback
        self._file_path = file_path
        if ignore_creation:
            self._events = "modified"
        else:
            self._events = ("created", "modified")
        self._changed_files = []
        self._lock = RLock()

    def on_any_event(self, event):
        if event.is_directory:
            return
        with self._lock:
            if event.event_type == "closed":
                if event.src_path in self._changed_files:
                    self._changed_files.remove(event.src_path)
                    # fire event, it is now safe
                    try:
                        self._callback(event.src_path)
                    except:
                        LOG.exception(
                            "An error occurred handling file " "change event callback"
                        )

            elif event.event_type in self._events:
                if event.src_path not in self._changed_files:
                    self._changed_files.append(event.src_path)


# def find_resource(self, res_name, res_dirname=None):
#         """Find a resource file.

#         Searches for the given filename using this scheme:

#         1. Search the resource lang directory:

#            <skill>/<res_dirname>/<lang>/<res_name>

#         2. Search the resource directory:

#            <skill>/<res_dirname>/<res_name>

#         3. Search the locale lang directory or other subdirectory:

#            <skill>/locale/<lang>/<res_name> or

#            <skill>/locale/<lang>/.../<res_name>

#         Args:
#             res_name (string): The resource name to be found
#             res_dirname (string, optional): A skill resource directory, such
#                                             'dialog', 'vocab', 'regex' or 'ui'.
#                                             Defaults to None.

#         Returns:
#             string: The full path to the resource file or None if not found
#         """
#         result = _find_resource(res_name, self.lang, res_dirname)
#         if not result and self.lang != "en-us":
#             # when resource not found try fallback to en-us
#             LOG.warning(
#                 "Resource '{}' for lang '{}' not found: trying 'en-us'".format(
#                     res_name, self.lang
#                 )
#             )
#             result = _find_resource(res_name, "en-us", res_dirname)
#         return result

# def _find_resource(res_name, lang, res_dirname=None):
#         """Finds a resource by name, lang and dir"""
#         if res_dirname:
#             # Try the old translated directory (dialog/vocab/regex)
#             path = join(CORE_ROOT_PATH, res_dirname, lang, res_name)
#             if exists(path):
#                 return path

#             # Try old-style non-translated resource
#             path = join(CORE_ROOT_PATH, res_dirname, res_name)
#             if exists(path):
#                 return path

#         # New scheme:  search for res_name under the 'locale' folder
#         root_path = join(CORE_ROOT_PATH, "locale", lang)
#         for path, _, files in walk(root_path):
#             if res_name in files:
#                 return join(path, res_name)
#         # Not found
#         return None


def read_stripped_lines(filename):
    """Read a file and return a list of stripped lines.

    Args:
        filename (str): path to file to read.

    Returns:
        (list) list of lines stripped from leading and ending white chars.
    """
    with open(filename, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                yield line


def read_dict(filename, div="="):
    """Read file into dict.

    A file containing:
        foo = bar
        baz = bog

    results in a dict
    {
        'foo': 'bar',
        'baz': 'bog'
    }

    Args:
        filename (str):   path to file
        div (str): deviders between dict keys and values

    Returns:
        (dict) generated dictionary
    """
    d = {}
    with open(filename, "r") as f:
        for line in f:
            key, val = line.split(div)
            d[key.strip()] = val.strip()
    return d


def mb_to_bytes(size):
    """Takes a size in MB and returns the number of bytes.

    Args:
        size(int/float): size in Mega Bytes

    Returns:
        (int/float) size in bytes
    """
    return size * 1024 * 1024


def _get_cache_entries(directory):
    """Get information tuple for all regular files in directory.

    Args:
        directory (str): path to directory to check

    Returns:
        (tuple) (modification time, size, filepath)
    """
    entries = (os.path.join(directory, fn) for fn in os.listdir(directory))
    entries = ((os.stat(path), path) for path in entries)

    # leave only regular files, insert modification date
    return (
        (stat[ST_MTIME], stat[ST_SIZE], path)
        for stat, path in entries
        if S_ISREG(stat[ST_MODE])
    )


def _delete_oldest(entries, bytes_needed):
    """Delete files with oldest modification date until space is freed.

    Args:
        entries (tuple): file + file stats tuple
        bytes_needed (int): disk space that needs to be freed

    Returns:
        (list) all removed paths
    """
    deleted_files = []
    space_freed = 0
    for moddate, fsize, path in sorted(entries):
        try:
            os.remove(path)
            space_freed += fsize
            deleted_files.append(path)
        except Exception:
            pass

        if space_freed > bytes_needed:
            break  # deleted enough!

    return deleted_files


def curate_cache(directory, min_free_percent=5.0, min_free_disk=50):
    """Clear out the directory if needed.

    The curation will only occur if both the precentage and actual disk space
    is below the limit. This assumes all the files in the directory can be
    deleted as freely.

    Args:
        directory (str): directory path that holds cached files
        min_free_percent (float): percentage (0.0-100.0) of drive to keep free,
                                  default is 5% if not specified.
        min_free_disk (float): minimum allowed disk space in MB, default
                               value is 50 MB if not specified.
    """
    # Simpleminded implementation -- keep a certain percentage of the
    # disk available.
    # TODO: Would be easy to add more options, like whitelisted files, etc.
    deleted_files = []
    space = psutil.disk_usage(directory)

    min_free_disk = mb_to_bytes(min_free_disk)
    percent_free = 100.0 - space.percent
    if percent_free < min_free_percent and space.free < min_free_disk:
        LOG.info("Low diskspace detected, cleaning cache")
        # calculate how many bytes we need to delete
        bytes_needed = (min_free_percent - percent_free) / 100.0 * space.total
        bytes_needed = int(bytes_needed + 1.0)

        # get all entries in the directory w/ stats
        entries = _get_cache_entries(directory)
        # delete as many as needed starting with the oldest
        deleted_files = _delete_oldest(entries, bytes_needed)

    return deleted_files


def get_cache_directory(domain=None):
    """Get a directory for caching data.

    This directory can be used to hold temporary caches of data to
    speed up performance.  This directory will likely be part of a
    small RAM disk and may be cleared at any time.  So code that
    uses these cached files must be able to fallback and regenerate
    the file.

    Args:
        # domain (str): The cache domain.  Basically just a subdirectory.

    Returns:
        (str) a path to the directory where you can cache data
    """
    import source.configuration

    config = source.configuration.Configuration.get()
    directory = config.get("cache_path")
    if not directory:
        # If not defined, use /tmp/core/cache
        directory = get_temp_path("core", "cache")
    return ensure_directory_exists(directory, domain)


def ensure_directory_exists(directory, domain=None, permissions=0o777):
    """Create a directory and give access rights to all

    Args:
        directory (str): Root directory
        domain (str): Domain. Basically a subdirectory to prevent things like
                      overlapping signal filenames.
        rights (int): Directory permissions (default is 0o777)

    Returns:
        (str) a path to the directory
    """
    if domain:
        directory = os.path.join(directory, domain)

    # Expand and normalize the path
    directory = os.path.normpath(directory)
    directory = os.path.expanduser(directory)

    if not os.path.isdir(directory):
        try:
            save = os.umask(0)
            os.makedirs(directory, permissions)
        except OSError:
            LOG.warning("Failed to create: " + directory)
        finally:
            os.umask(save)

    return directory


def create_file(filename):
    """Create the file filename and create any directories needed

    Args:
        filename: Path to the file to be created
    """
    ensure_directory_exists(os.path.dirname(filename), permissions=0o775)
    with open(filename, "w") as f:
        f.write("")


def get_temp_path(*args):
    """Generate a valid path in the system temp directory.

    This method accepts one or more strings as arguments. The arguments are
    joined and returned as a complete path inside the systems temp directory.
    Importantly, this will not create any directories or files.

    Example usage: get_temp_path('core', 'audio', 'example.wav')
    Will return the equivalent of: '/tmp/core/audio/example.wav'

    Args:
        path_element (str): directories and/or filename

    Returns:
        (str) a valid path in the systems temp directory
    """
    try:
        path = os.path.join(tempfile.gettempdir(), *args)
    except TypeError:
        raise TypeError(
            "Could not create a temp path, get_temp_path() only " "accepts Strings"
        )
    return path
