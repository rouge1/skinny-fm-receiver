"""FM receiver: a wideband FFT sweep to find stations, and an IQ receiver -
FM stereo, RDS, audio and recording - to listen to one.

Run it with the ``fm-receiver`` script in the project directory, or
``python -m fm_receiver`` with ``tools/`` on the path. What it can do is
listed in ``knowledge/capabilities.md``; how to use it in
``knowledge/usage.md``.
"""

import os

__version__ = '1.0'

#: This package, and the project it lives in (two levels up: tools/fm_receiver).
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(os.path.dirname(PACKAGE_DIR))
ASSET_DIR = os.path.join(PACKAGE_DIR, 'assets')
