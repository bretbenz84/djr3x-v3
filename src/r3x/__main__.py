import argparse
import json
from pathlib import Path
import sys
from dataclasses import replace

from .app import App
from .settings import Settings
from .contracts import ProviderFailure


def main() -> int:
    parser = argparse.ArgumentParser(description="DJ-R3X voice-core experiment")
    parser.add_argument("command", choices=("doctor", "boot", "live", "scenario", "report"))
    parser.add_argument("artifact", nargs="?", type=Path)
    parser.add_argument("--profile", choices=("dev-mac",), default=None)
    parser.add_argument("--config", type=Path, help="Explicit TOML settings file")
    args = parser.parse_args()
    app = None
    try:
        # Package is installed editable in this checkout's dedicated environment.
        project = Path(__file__).resolve().parents[2]
        if not (project / "pyproject.toml").is_file():
            raise ValueError("This slice requires an editable checkout installation")
        config_path = args.config or project / "local/settings.toml"
        settings = Settings.load(config_path) if args.config or config_path.exists() else Settings()
        if args.profile is not None:
            settings = replace(settings, profile=args.profile)
        if args.command in {"scenario", "report"}:
            if args.artifact is None:
                parser.error("scenario/report requires a file path")
            if args.command == "scenario":
                from .scenario import run
                return 0 if run(args.artifact) else 1
            from .trace import report
            print(json.dumps(report(args.artifact), indent=2))
            return 0
        app = App(settings, project)
        if args.command == "doctor":
            app.configure_live()
            print(json.dumps(app.doctor(), indent=2))
            return 1 if app.readiness() else 0
        if args.command == "live":
            success = app.live()
            if not success:
                print(json.dumps(app.doctor(), indent=2))
            return 0 if success else 1
        ready = app.start()
        print(json.dumps({"event": "startup", **app.doctor()}, indent=2))
        app.close()
        print(json.dumps({"event": "shutdown", **app.doctor()}, indent=2))
        cleanup_failed = any(r.outcome == "failed" and r.operation in {"cancel", "close"}
                             for r in app.owner.results)
        return 0 if ready and not cleanup_failed else 1
    except (ValueError, OSError, ProviderFailure) as exc:
        print(json.dumps({"outcome": "failed", "error_type": type(exc).__name__,
                          "reason": str(exc) if isinstance(exc, ProviderFailure) else None}), file=sys.stderr)
        return 1
    finally:
        if app is not None and args.command != "live":
            app.close()


if __name__ == "__main__":
    raise SystemExit(main())
