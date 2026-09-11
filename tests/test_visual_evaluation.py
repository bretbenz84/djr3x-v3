"""Private-data-free evaluation and independent model-copy contracts."""
import json
from pathlib import Path
import tempfile
import unittest
from r3x.visual_evaluation import classify, file_digest, load_manifest, summarize
from tools.prepare_visual_assets import ASSETS, prepare


class VisualEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()
        self.rows=[]
        for i,split in enumerate(('gallery','probe','probe')):
            path=self.root/f'{i}.png'; path.write_bytes(bytes([i]))
            self.rows.append({'path':path.name,'sha256':file_digest(path),'person_id':'p' if i<2 else None,
                              'split':split,'session':split,'camera':'dev','face_width_px':24})
    def tearDown(self): self.temp.cleanup()
    def manifest(self):
        path=self.root/'manifest.json'
        path.write_text(json.dumps({'version':1,'alignment':'arcface-112-v1','images':self.rows}))
        return path
    def test_manifest_requires_disjoint_sessions_and_original_digests(self):
        self.assertEqual(len(load_manifest(self.manifest())),3)
        self.rows[1]['session']='gallery'
        with self.assertRaisesRegex(ValueError,'SessionLeakage'): load_manifest(self.manifest())
    def test_duplicate_content_cannot_leak_from_gallery_to_probe(self):
        self.rows[1].update(path='0.png',sha256=self.rows[0]['sha256'])
        with self.assertRaisesRegex(ValueError,'Duplicate'): load_manifest(self.manifest())
    def test_wrong_digest_and_outside_image_are_rejected(self):
        self.rows[0]['sha256']='bad'
        with self.assertRaisesRegex(ValueError,'Digest'): load_manifest(self.manifest())
        self.rows[0]['path']='../outside.png'
        with self.assertRaisesRegex(ValueError,'OutsideDataset'): load_manifest(self.manifest())
    def test_unknown_faces_and_ambiguous_matches_abstain(self):
        gallery={'a':[(1.,0.,0.)],'b':[(0.,1.,0.)]}
        self.assertEqual(classify((1.,0.,0.),gallery,.5,.07),'a')
        self.assertIsNone(classify((0.,0.,1.),gallery,.5,.07))
        self.assertIsNone(classify((1.,1.,0.),gallery,.5,.07))
    def test_abstention_and_wrong_names_are_both_measured(self):
        rows=[{'expected':'a','predicted':None,'camera':'robot','face_width_px':24},
              {'expected':None,'predicted':'a','camera':'robot','face_width_px':24}]
        result=summarize(rows)['overall']
        self.assertEqual(result['known_accuracy'],0)
        self.assertEqual(result['unknown_false_accepts'],1)
        self.assertEqual(result['abstentions'],1)
        self.assertEqual(result['named_precision'],0)
    def test_model_copies_are_independent_verified_and_repeatable(self):
        source=self.root/'source'; dest=self.root/'destination'
        for relative,_ in ASSETS.values():
            p=source/relative; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(relative.encode())
        records=prepare(source,dest)
        self.assertEqual(records,prepare(source,dest))
        self.assertTrue(all((dest/r['path']).stat().st_ino != Path(r['source']).stat().st_ino for r in records))
        (dest/records[0]['path']).write_bytes(b'changed')
        self.assertNotEqual(file_digest(Path(records[0]['source'])),file_digest(dest/records[0]['path']))
        with self.assertRaisesRegex(ValueError,'Differs'): prepare(source,dest)
