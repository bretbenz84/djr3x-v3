"""Small file-backed reference gallery. Only the state owner applies model proposals."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
from .scene import SceneImage

PHOTO_TOOLS = {'inspect_visual_memory','remember_object','edit_object_memory'}

def names_subject(text, name):
    """Validate a model-selected literal reference, never infer a subject from speech."""
    text=text.casefold();name=name.casefold().strip()
    if not name:return False
    start=0
    while (index:=text.find(name,start))>=0:
        end=index+len(name)
        if (index==0 or not text[index-1].isalnum()) and (end==len(text) or not text[end].isalnum()):return True
        start=end
    return False

class PhotoMemory:
    def __init__(self,root: Path):
        self.root=root
        self.data={'version':1,'revision':0,'entities':{},'operations':{}}
        self.evidence={}

    def _path(self,name):
        path=self.root/name
        if self.root.resolve()!=self.root or path.resolve()!=path or not path.is_relative_to(self.root):
            raise ValueError('PhotoPathNotIndependent')
        return path

    def start(self):
        self._path('catalog.json');self.root.mkdir(parents=True,exist_ok=True)
        path=self._path('catalog.json')
        if path.exists():
            if path.stat().st_size>2_000_000:raise ValueError('PhotoCatalogTooLarge')
            self.data=json.loads(path.read_text())
            if self.data['version']!=1 or len(self.data['entities'])>32:raise ValueError('PhotoCatalogVersionOrCapacity')
        else:self._commit(copy.deepcopy(self.data))
        # Remove photos left by an interrupted save/delete; catalog is the commit point.
        referenced={r['file'] for e in self.data['entities'].values() for r in e['references']}
        for path in self.root.glob('*/*.png'):
            if str(path.relative_to(self.root)) not in referenced:self._path(str(path.relative_to(self.root))).unlink()

    def close(self):self.evidence.clear()

    def catalog(self):
        return [{'entity_id':k,'name':v['name'],'kind':v['kind'],'notes':v['notes'],
                 'reference_count':len(v['references'])} for k,v in self.data['entities'].items()]

    def offer(self,image,target,now):
        self.evidence={k:v for k,v in self.evidence.items() if now-v['offered_at']<=45}
        while len(self.evidence)>=3:self.evidence.pop(next(iter(self.evidence)))
        key='photo-'+uuid4().hex
        self.evidence[key]={'image':image,'target':target,'offered_at':now}
        return key

    def references(self,ids=None):
        images=[];labels=[]
        automatic = ids is None
        # A small library fits beside two current images within the eight-image budget.
        # Larger libraries require model-selected candidates, never an arbitrary subset.
        if automatic:ids=list(self.data['entities']) if len(self.data['entities'])<=6 else []
        elif len(ids)>3 or len(set(ids))!=len(ids):raise ValueError('AtMostThreePhotoCandidates')
        for key in ids:
            entity=self.data['entities'].get(key)
            if not entity:raise ValueError('UnknownPhotoEntity')
            for reference in entity['references'][-1 if automatic else -2:]:
                data=self._path(reference['file']).read_bytes()
                if len(data)>1_100_000 or hashlib.sha256(data).hexdigest()!=reference['sha256']:raise ValueError('PhotoReferenceChanged')
                images.append(SceneImage(reference['sha256'],reference['captured_at'],'data:image/png;base64,'+base64.b64encode(data).decode()))
                labels.append({'entity_id':key,'name':entity['name'],'kind':entity['kind'],'notes':entity['notes'],
                               'source':'historical user-taught reference; not a current sighting'})
        return tuple(images),labels

    def _commit(self,data):
        data['revision']+=1
        data['operations']=dict(list(data['operations'].items())[-64:])
        path=self._path('catalog.json');temp=self._path('catalog.tmp')
        with temp.open('w') as f:
            json.dump(data,f,indent=2,ensure_ascii=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
        self.data=data

    def apply(self,call,source,now):
        """Source is exact human text/attribution supplied by CoreState, never the model."""
        from .semantics import validate
        validate(call.name,call.arguments)
        op=source['session_id']+':'+call.call_id
        if op in self.data['operations']:return self.data['operations'][op]
        a=call.arguments
        if a['source_turn_id']!=source['turn_id']:
            raise ValueError('PhotoTeachingSourceMismatch')
        data=copy.deepcopy(self.data);created=None;remove=[]
        if call.name=='remember_object':
            if not names_subject(source['text'],a['name']):raise ValueError('PhotoSubjectNeedsExplicitName')
            evidence=self.evidence.get(a['evidence_id'])
            if not evidence or not 0<=now-evidence['offered_at']<=45:raise ValueError('PhotoEvidenceExpired')
            key=a['entity_id'] or uuid4().hex
            if a['entity_id'] and key not in data['entities']:raise ValueError('UnknownPhotoEntity')
            if key not in data['entities'] and len(data['entities'])>=32:raise ValueError('PhotoGalleryFull')
            image=evidence['image'];raw=base64.b64decode(image.data_url.split(',',1)[1],validate=True)
            if not raw.startswith(b'\x89PNG\r\n\x1a\n') or len(raw)>1_100_000:raise ValueError('InvalidPhotoImage')
            digest=hashlib.sha256(raw).hexdigest()
            entity=data['entities'].setdefault(key,{'name':a['name'],'kind':a['kind'],'notes':a['notes'],'references':[],'history':[]})
            # Adding another view is not an implicit rename or correction.
            if (entity['name'],entity['kind'])!=(a['name'],a['kind']):raise ValueError('UseExplicitPhotoCorrection')
            if not any(r['sha256']==digest for r in entity['references']):
                if len(entity['references'])>=6:raise ValueError('PhotoReferenceCapacity')
                relative=key+'/'+digest+'.png';path=self._path(relative);path.parent.mkdir(exist_ok=True)
                with path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
                created=path
                entity['references'].append({'file':relative,'sha256':digest,'captured_at':image.at,
                    'frame_id':image.frame_id,'target':evidence['target'],'source':source})
            entity['history']=(entity['history']+[{'action':'teach','source':source}])[-24:]
            result={'entity_id':key,'name':entity['name'],'reference_count':len(entity['references']),'status':'saved'}
        elif call.name=='edit_object_memory':
            key=a['entity_id'];entity=data['entities'].get(key)
            if entity is None:raise ValueError('UnknownPhotoEntity')
            if not names_subject(source['text'],entity['name']):raise ValueError('PhotoSubjectNeedsExplicitName')
            if a['action']=='forget':
                remove=[self._path(r['file']) for r in entity['references']];del data['entities'][key]
                result={'entity_id':key,'status':'forgotten'}
            else:
                previous_notes=entity['notes']
                if a['remove_notes'] is not None:
                    if a['remove_notes'] not in previous_notes:raise ValueError('PhotoCorrectionTextMismatch')
                    if not names_subject(source['text'],a['remove_notes']):raise ValueError('PhotoCorrectionNeedsQuotedDetail')
                    previous_notes=previous_notes.replace(a['remove_notes'],'',1).strip(' ;\n')
                notes=a['notes']
                if notes is None or notes in previous_notes:notes=previous_notes
                elif previous_notes:notes=previous_notes.rstrip(' ;')+'; '+notes
                if len(notes)>4000:raise ValueError('PhotoNotesCapacity')
                entity['history']=(entity['history']+[{'action':'correct','previous':{k:entity[k] for k in ('name','notes')},'source':source}])[-24:]
                entity['name'],entity['notes']=a['name'],notes
                result={'entity_id':key,'name':entity['name'],'status':'updated'}
        else:raise ValueError('UnknownPhotoOperation')
        data['operations'][op]=result
        try:self._commit(data)
        except Exception:
            if created:created.unlink(missing_ok=True)
            raise
        for path in remove:path.unlink(missing_ok=True)
        if remove:self.evidence.clear()
        return result
