"""Allow ``python -m nev`` to launch the TCP server."""

from nev.server import main

raise SystemExit(main())
