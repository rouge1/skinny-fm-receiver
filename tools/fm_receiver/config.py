"""Where the window's settings live, and the one way they are written.

Settings go to ``~/.config/fm-receiver/config.json`` (or
``$XDG_CONFIG_HOME``), not into the project, so a checkout stays clean and
two checkouts share one set. ``FMRX_CONFIG`` names another file - the tests
use it so they never touch the real one.

Every write goes through :func:`update_config`, which merges into what is
there and replaces the file atomically - the same rule the RF bench toolkit
keeps (``update_app_config``): a whole-file write from one place would delete
keys another place owns.
"""

import json
import os
import sys

from . import PROJECT_DIR


def config_path():
    override = os.environ.get('FMRX_CONFIG')
    if override:
        return override
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(
        os.path.expanduser('~'), '.config')
    return os.path.join(base, 'fm-receiver', 'config.json')


def control_path():
    """The running window's control socket (``control.py``), for ``fmctl``
    too - so this module stays free of Qt. ``FMRX_CONTROL`` names another:
    the tests use it so they never meet a window the user has open."""
    override = os.environ.get('FMRX_CONTROL')
    if override:
        return override
    # Linux: the session's runtime folder, the user's only. macOS's TMPDIR
    # is per user too.
    base = os.environ.get('XDG_RUNTIME_DIR') if sys.platform.startswith('linux') else None
    if base and os.path.isdir(base):
        return os.path.join(base, 'fm-receiver.sock')
    base = os.environ.get('TMPDIR') or '/tmp'
    return os.path.join(base, f'fm-receiver-{os.getuid()}.sock')


def default_recording_dir():
    return os.path.join(PROJECT_DIR, 'recordings')


def load_config(path=None):
    """The saved settings, or an empty dict if there are none or they are
    unreadable - a bad file must never stop the window opening."""
    path = path or config_path()
    try:
        with open(path) as fh:
            config = json.load(fh)
        return config if isinstance(config, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        print(f"FM receiver: ignoring unreadable settings {path}: {exc}",
              file=sys.stderr)
        return {}


def update_config(changes, path=None):
    """Merge ``changes`` into the settings file and replace it atomically."""
    path = path or config_path()
    config = load_config(path)
    config.update(changes)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, 'w') as fh:
        json.dump(config, fh, indent=4)
    os.replace(tmp, path)
    return config
