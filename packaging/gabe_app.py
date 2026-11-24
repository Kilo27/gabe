"""Entry point for the packaged desktop app. `Gabe check` checks the app's config folder."""
import sys

if sys.argv[1:2] == ["check"]:
    from gabe.desktop import check_main

    raise SystemExit(check_main())

from gabe.desktop import main

raise SystemExit(main())
