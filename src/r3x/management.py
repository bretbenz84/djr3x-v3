"""Offline management commands. A live lease excludes concurrent stale caches."""
import argparse
import json
from pathlib import Path

from .lease import LiveLease
from .memory import Memory
from . import profiles


def private_path(path,root):
    path = path.absolute()
    if path.resolve()!=path or not path.is_relative_to(root):
        raise ValueError("ManagementArtifactsMustStayUnderLocal")
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    return path


def main(argv,paths):
    parser = argparse.ArgumentParser(prog="r3x profiles")
    commands = parser.add_subparsers(dest="action",required=True)
    commands.add_parser("list")
    for name in ("recordings","forget","reset-voice"):
        commands.add_parser(name).add_argument("person_id")
    review = commands.add_parser("review")
    review.add_argument("person_id")
    review.add_argument("--status",choices=("suspect","supported"),required=True)
    delete = commands.add_parser("delete-recording")
    delete.add_argument("recording_id")
    export = commands.add_parser("export")
    export.add_argument("recording_id")
    export.add_argument("--out",type=Path,required=True)
    preview = commands.add_parser("preview-v2")
    preview.add_argument("--source",type=Path,required=True)
    preview.add_argument("--source-person-id",type=int,action="append",required=True)
    preview.add_argument("--out",type=Path,required=True)
    reembed = commands.add_parser("reembed")
    reembed.add_argument("person_id")
    reembed.add_argument("--out",type=Path,required=True)
    for name in ("apply-import","apply-reembed"):
        commands.add_parser(name).add_argument("preview",type=Path)
    args = parser.parse_args(argv)
    paths.prepare()
    lease = LiveLease(paths.root/"sessions/live.lock")
    lease.acquire()
    memory = Memory(paths.root/"core.sqlite3")
    try:
        if args.action=="preview-v2":
            path = private_path(args.out,paths.root)
            preview = profiles.preview_v2(args.source,args.source_person_id,path)
            result = {"preview":str(path),"digest":preview["preview_digest"],"people":[
                {"source_id":p["source_id"],"name":p["name"],"recordings":len(p["recordings"]),
                 "legacy_references":len(p["legacy_embeddings"])} for p in preview["people"]],
                 "applied":False,"policy":preview["policy"]}
        else:
            memory.start()
            if args.action=="list":
                result = [{**person,"recordings":memory.rows("SELECT count(*) AS n FROM recordings WHERE person_id=?",(person["id"],))[0]["n"]} for person in memory.people(include_retired=True)]
            elif args.action=="recordings":
                result = memory.rows("SELECT id,digest,sample_rate,metadata FROM recordings WHERE person_id=?",(args.person_id,))
            elif args.action=="apply-import":
                result = {"mapping":profiles.apply_import(memory,private_path(args.preview,paths.root))}
            elif args.action=="reembed":
                from .adapters.voice import CAMPlus
                encoder = CAMPlus(paths.root/"models/campplus.onnx")
                try:
                    encoder.start()
                    preview = profiles.preview_reembed(memory,encoder,args.person_id,private_path(args.out,paths.root))
                    result = {"preview":str(args.out),"digest":preview["preview_digest"],"changes":len(preview["changes"]),"applied":False}
                finally:
                    encoder.close()
            elif args.action=="apply-reembed":
                result = profiles.apply_reembed(memory,private_path(args.preview,paths.root))
            elif args.action=="review":
                profiles.review(memory,args.person_id,args.status)
                result = {"person_id":args.person_id,"status":args.status}
            elif args.action=="forget":
                if not memory.person(args.person_id):
                    raise ValueError("UnknownPersonId")
                with memory.transaction() as conn:
                    memory.forget(conn,args.person_id)
                    memory.advance(conn)
                result = {"forgotten":args.person_id,"scope":"active memory, accepted originals, derived profiles, and registered recording/export/preview files"}
            elif args.action=="delete-recording":
                result = profiles.delete_recording(memory,args.recording_id)
            elif args.action=="reset-voice":
                result = profiles.reset_voice(memory,args.person_id)
            elif args.action=="export":
                row = memory.rows("SELECT r.pcm,r.person_id,o.wav FROM recordings r LEFT JOIN originals o ON o.recording_id=r.id WHERE r.id=?",(args.recording_id,))
                if not row:
                    raise ValueError("UnknownRecordingId")
                path = private_path(args.out,paths.root)
                if row[0]["wav"]:
                    with path.open("xb") as handle:
                        handle.write(row[0]["wav"])
                else:
                    from .dataset import write_wav
                    write_wav(path,row[0]["pcm"])
                with memory.transaction() as conn:
                    memory.register_artifact(conn,path,row[0]["person_id"],args.recording_id)
                result = {"export":str(path)}
        print(json.dumps(result,indent=2))
        return 0
    finally:
        memory.close()
        lease.close()
