"""Explicit hosted semantic probes with fictional text; no audio, camera or personal data."""
import json
from pathlib import Path
from uuid import uuid4

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Cancellation, Snapshot
from r3x.settings import Secrets, ScratchPaths

CASES = {
    'animal_not_toy': [('user','That yellow object is a dog bone toy.'),
        ('assistant','Got it, a dog bone toy.'),('user','This is Milo.'),
        ('assistant','Do you mean the dog, or the toy near him?'),('user','The dog dummy.')],
    'sound_alike_after_correction': [('user','This is Milo, my dog.'),
        ('assistant','You mean the toy?'),('user','I said the dog.'),
        ('assistant','Got it, the dog, not the toy.'),('user','See the dock.')],
    'unrelated_sound_alike': [('user','I enjoy photographing birds in my garden.'),
        ('assistant','What birds visit?'),('user','The robbing is back on its perch.')],
    'genuine_topic_change': [('user','This is my dog Milo.'),
        ('assistant','Hello, Milo.'),('user','Different subject: the boat dock needs repair.')],
}

def main():
    root=Path(__file__).resolve().parents[1]/'local';ScratchPaths(root).prepare()
    brain=OpenAIBrain(Secrets.load(root).openai_api_key);brain.start()
    rows=[]
    try:
        for name,dialogue in CASES.items():
            snapshot=Snapshot('fictional',uuid4().hex,0,tuple({'role':role,'content':text} for role,text in dialogue),
                context={'source_turn_id':'fictional','speaker':{'state':'unresolved'},'faces':[]},tools_enabled=False)
            output=''.join(brain.reply(snapshot,Cancellation()))
            rows.append({'case':name,'dialogue':dialogue,'reply':output})
            print(name,output,flush=True)
    finally:brain.close()
    path=root/'validation'/('conversation-repairs-'+uuid4().hex+'.json')
    path.write_text(json.dumps({'evidence':'Hosted fictional text only; not acoustic or visual recognition validation','rows':rows},indent=2))
    print(path)

if __name__=='__main__':main()
