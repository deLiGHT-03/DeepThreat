"""The application object a hosting platform serves.

Run from source the app is started by ``run.py``, which picks a free port and opens a browser --
neither of which a server has any use for. A platform instead imports an application object and
hands it to its own WSGI server, so this is that object::

    waitress-serve --host=0.0.0.0 --port=$PORT wsgi:app

One thing is read from the environment here rather than in the app itself: the port, because a
platform assigns it. Everything else the app needs to know about being served (the session key)
it reads where it builds the app, so this stays the two lines it looks like.
"""

from __future__ import annotations

from app import create_app

app = create_app()
