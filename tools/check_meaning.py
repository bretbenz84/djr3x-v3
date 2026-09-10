"""Explicit paid text-model acceptance probes through the production core.

All people, voice vectors, and claims are synthetic fixtures in a temporary DB.
Text output only: no TTS, sounddevice, microphone, camera or personal-data import.
Reports are behavioral samples, not acoustic or live-room acceptance.
"""
import json
import argparse
from pathlib import Path
import tempfile
import time
from uuid import uuid4

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Capture,Transcript
from r3x.memory import Memory
from r3x.output import TextOutput
from r3x.session import Session
from r3x.settings import ScratchPaths,Secrets
from r3x.speakers import SpeakerTracker,VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace,report

NS="synthetic-language-check:3"
B,J,U=(1.,0.,0.),(0.,1.,0.),(0.,0.,1.)


def observation(vector):
    return VoiceObservation(NS,vector,((0.,2.,vector),),3.,1.,False,"synthetic_not_audio_accuracy")


def seed(memory):
    ids={}
    with memory.transaction() as conn:
        for name,aliases,vector in (("Bret Benziger",["Bret"],B),("Jeffrey Benziger",["Jeff","Jeff Benziger"],J)):
            pid=memory.add_person(conn,name,aliases,None)
            ids[name.split()[0]]=pid
            samples=[]
            for i in range(3):
                capture=Capture(uuid4().hex,"seed",uuid4().bytes*6000,0,3,3)
                samples.append((capture,Transcript("Synthetic fixture voice evidence.",0,True),observation(vector),"synthetic-seed"))
            memory.save_voice(conn,pid,samples,NS)
    return ids


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--variants",action="store_true",help="Use alternate phrasing outside the initial calibration set")
    args=parser.parse_args()
    variants={
        "mentioned_person":"The guy I was talking about is Jeff Benziger.",
        "activity_not_name":"Tomorrow I'm heading out to camp in the mountains.",
        "comment_not_occupation":"You're giving me the creeps, Rex. Back off a little.",
        "question_not_biography":"Was Jimmy Carter involved with Habitat for Humanity?",
        "joke_not_biography":"If tacos paid my rent I'd move into that restaurant. I'm joking; I almost never eat there.",
        "self_report":"Make a note for me: I collect old radios.",
        "correction_after_restart":"That collection note needs fixing. I collect clocks, not radios.",
        "recall_after_restart":"Remind me what collection you have on record for me.",
        "third_party_source":"A fact worth keeping about Jeff Benziger: he repairs vintage watches in his free time.",
        "uncertain_self_report":"I work as a museum curator; could you remember that about me?",
        "natural_introduction":"Jordan Avery here, by the way. That's what you can call me.",
        "identity_correction_from_bystander":"You've got his name wrong. Stop associating that name with him.",
        "ambiguous_nickname":"Can you keep a note that Jeff builds wooden ships?",
        "write_failure":"My favorite color is purple; please make a note of that."}
    paths=ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    paths.prepare()
    folder=paths.root/"validation"/uuid4().hex
    folder.mkdir(parents=True)
    trace=Trace("meaning-check",folder/"session.jsonl")
    brain=OpenAIBrain(Secrets.load(paths.root).openai_api_key)
    cases=[]
    try:
        brain.start()
        with tempfile.TemporaryDirectory() as tmp:
            memory=Memory(Path(tmp).resolve()/"core.sqlite3")
            memory.start()
            ids=seed(memory)
            def new_session():
                return Session(uuid4().hex,brain,TextOutput(trace),trace,CoreState(memory,SpeakerTracker(NS),trace))
            session=new_session()
            trace.emit("validation_start",evidence="hosted_model_synthetic_identity_text_only",
                       model=brain.capability.model,audio_played=False,video_captured=False)
            def turn(case,text,vector,check):
                if args.variants:text=variants[case]
                before_lines=len((folder/"session.jsonl").read_text().splitlines())
                now=time.monotonic()
                capture=Capture(uuid4().hex,session.session_id,uuid4().bytes*6000,now-4,now-1,now,
                                voiced_intervals=((0.,3.),),device="synthetic fixture")
                receipt=session.process_turn(capture,Transcript(text,-.01,True),session.revision,observation(vector))
                expected = {"completed","suppressed"} if case=="comment_not_occupation" else {"completed"}
                passed=receipt.status in expected and bool(check())
                events=[json.loads(line) for line in (folder/"session.jsonl").read_text().splitlines()[before_lines:]]
                results=[e for e in events if e["event"]=="tool_result"]
                if case=="write_failure":
                    passed=passed and any(e["name"]=="record_claim" and e["outcome"]=="failed" for e in results)
                cases.append({"case":case,"text":text,"passed":passed,"delivery":receipt.status,
                              "reply":" ".join(receipt.completed_text),
                              "tool_outcomes":[{"name":e["name"],"outcome":e["outcome"],"reason":e["reason"]} for e in results]})
                trace.emit("validation_case",**cases[-1])
            turn("mentioned_person","That’s Jeff Benziger.",B,
                 lambda:len(memory.people())==2 and memory.person(ids["Bret"])["name"]=="Bret Benziger" and session.state.current[4].person_id==ids["Bret"] and not session.state.enrollments and not memory.claims(include_candidates=True))
            session.state.question_delivered("What should I call you?",None,time.monotonic())
            session.history.append({"role":"assistant","content":"What should I call you?"})
            turn("activity_not_name","I’m going camping tomorrow.",U,lambda:len(memory.people())==2 and not session.state.enrollments)
            session.state.question_delivered("Jeff, what do you do for work?",ids["Jeffrey"],time.monotonic())
            session.history.append({"role":"assistant","content":"Jeff, what do you do for work?"})
            turn("comment_not_occupation","You’re creepy.",J,lambda:not memory.claims(include_candidates=True))
            turn("question_not_biography","What do you think of Jimmy Carter?",J,lambda:not memory.claims(include_candidates=True))
            turn("joke_not_biography","I should live at that taco place. Kidding—I barely go there.",J,lambda:not memory.claims(include_candidates=True))
            turn("self_report","Please remember I collect old radios.",B,
                 lambda:any("radio" in c["value"].lower() and c["subject_id"]==ids["Bret"] and c["said_by"]==ids["Bret"] for c in memory.claims()))
            session.close()
            memory.close()
            memory=Memory(Path(tmp).resolve()/"core.sqlite3")
            memory.start()
            session=new_session()
            turn("correction_after_restart","I meant clocks, not radios. Please correct what I told you I collect.",B,
                 lambda:any("clock" in c["value"].lower() for c in memory.claims(ids["Bret"])) and not any("radio" in c["value"].lower() for c in memory.claims(ids["Bret"])))
            turn("recall_after_restart","What do you remember that I collect?",B,
                 lambda:any("clock" in c["value"].lower() for c in memory.claims(ids["Bret"])))
            turn("third_party_source","Jeff repairs vintage watches in his spare time. Remember that about him.",B,
                 lambda:any("watch" in (c["predicate"]+" "+c["value"]).lower() and c["subject_id"]==ids["Jeffrey"] and c["said_by"]==ids["Bret"] and c["kind"]=="third_party" for c in memory.claims()))
            before=len(memory.claims())
            people_before={p['id'] for p in memory.people()}
            turn("uncertain_self_report","Please remember I’m a museum curator.",U,
                 lambda:len(memory.claims())==before and {p['id'] for p in memory.people()}==people_before
                 and not any("curator" in (c["predicate"]+" "+c["value"]).lower() for c in memory.claims(include_candidates=True)))
            turn("natural_introduction","I’m Jordan Avery, by the way.",U,
                 lambda:bool(memory.resolve("Jordan Avery")) and bool(session.state.enrollments))
            turn("identity_correction_from_bystander","That’s not his name. Stop learning that name for him.",B,
                 lambda:not session.state.enrollments)
            with memory.transaction() as conn:
                memory.add_person(conn,"Jeff Morgan",["Jeff"],None)
            before=len(memory.claims())
            turn("ambiguous_nickname","Remember that Jeff builds wooden ships.",B,lambda:len(memory.claims())==before)
            memory.connection.execute("CREATE TRIGGER reject_claim BEFORE INSERT ON claims BEGIN SELECT RAISE(ABORT,'synthetic'); END")
            turn("write_failure","Please remember my favorite color is purple.",B,
                 lambda:not any("purple" in c["value"].lower() for c in memory.claims()))
            session.close()
            memory.close()
    except Exception as exc:
        cases.append({"case":"runner","passed":False,"error_type":type(exc).__name__})
    finally:
        brain.close()
        trace.close()
    evidence={"evidence":"real hosted model; synthetic identity, text and database; silent text output",
              "cases":cases,"passed":bool(cases) and all(c["passed"] for c in cases),"runtime":report(folder/"session.jsonl"),
              "audio_played":False,"video_captured":False,"wording_set":"variants" if args.variants else "calibration"}
    (folder/"evidence.json").write_text(json.dumps(evidence,indent=2))
    print(json.dumps({"passed":evidence["passed"],"evidence":str(folder/"evidence.json"),
                      "failed":[c["case"] for c in cases if not c["passed"]]}))
    return 0 if evidence["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
