import argparse
import json
from pathlib import Path
import sys
import sqlite3
from dataclasses import replace

from .app import App
from .settings import Settings
from .contracts import ProviderFailure


def main() -> int:
    if len(sys.argv)>1 and sys.argv[1]=="profiles":
        from .management import main as manage
        from .settings import ScratchPaths
        try:
            return manage(sys.argv[2:],ScratchPaths.for_project(Path(__file__).resolve().parents[2]))
        except (ValueError,OSError,ProviderFailure,sqlite3.Error) as exc:
            print(json.dumps({"outcome":"failed","error_type":type(exc).__name__,
                              "reason":str(exc) if isinstance(exc,(ValueError,ProviderFailure)) else None}),file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description="DJ-R3X voice-core experiment")
    parser.add_argument("command", choices=("doctor", "boot", "live", "scenario", "report", "replay"))
    parser.add_argument("artifact", nargs="?", type=Path)
    parser.add_argument("--profile", choices=("dev-mac",), default=None)
    parser.add_argument("--config", type=Path, help="Explicit TOML settings file")
    parser.add_argument("--camera",action="store_true",help="Explicitly enable the configured camera for live mode")
    parser.add_argument("--record-evaluation",action="store_true",help="Explicitly retain live captures for later human labeling")
    parser.add_argument("--brain",action="store_true",help="Enable paid hosted language inference during saved-audio replay")
    parser.add_argument("--oracle-segments",action="store_true",help="Use human timing labels for encoder diagnostics, not production accuracy")
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
        if args.camera or args.record_evaluation:
            if args.command!="live":
                raise ValueError("CameraAndRecordingFlagsRequireLive")
            settings = replace(settings,camera_enabled=args.camera or settings.camera_enabled,
                               record_evaluation=args.record_evaluation or settings.record_evaluation)
        if (args.brain or args.oracle_segments) and args.command!="replay":
            raise ValueError("ReplayFlagsRequireReplay")
        if args.command=="replay":
            if args.artifact is None:
                parser.error("replay requires a reviewed manifest")
            from .replay import run
            from .settings import ScratchPaths
            paths = ScratchPaths.for_project(project)
            paths.prepare()
            return 0 if run(args.artifact.resolve(),paths.root,settings,args.brain,args.oracle_segments) else 1
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
    except (ValueError, OSError, ProviderFailure,sqlite3.Error) as exc:
        print(json.dumps({"outcome": "failed", "error_type": type(exc).__name__,
                          "reason": str(exc) if isinstance(exc, (ProviderFailure,ValueError)) else None}), file=sys.stderr)
        return 1
    finally:
        if app is not None and args.command != "live":
            app.close()


if __name__ == "__main__":
    raise SystemExit(main())
