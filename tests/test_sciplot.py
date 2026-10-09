import hashlib, json, math, subprocess, sys, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'skills'/'sciplot'/'scripts'
sys.path.insert(0,str(SCRIPTS))
from plot_csv import read_numeric_csv
from layout_qa import audit_geometry_document
class SciPlotTests(unittest.TestCase):
 def test_csv_and_unknown(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'d.csv';p.write_text('x,y\n2,4\n1,\n3,NaN\n4,8\n',encoding='utf8')
   r=read_numeric_csv(p,'x',['y']);self.assertEqual(r['x'],[2,1,3,4]);self.assertTrue(math.isnan(r['y'][1]))
   self.assertEqual(audit_geometry_document({'ok':True})['summary']['status'],'unknown')
 def test_export_and_no_overwrite(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d); src=d/'d.csv'; src.write_text('x,y\n0,0\n1,1\n2,\n3,2\n4,3\n',encoding='utf8'); out=d/'out'
   cmd=[sys.executable,str(SCRIPTS/'plot_csv.py'),str(src),'--x','x','--y','y','--outdir',str(out)]
   r=subprocess.run(cmd,capture_output=True,text=True,encoding='utf8');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
   self.assertTrue((out/'figure.pdf').read_bytes().startswith(b'%PDF-'));self.assertIn('<svg',(out/'figure.svg').read_text(encoding='utf8'))
   prov=json.loads((out/'provenance.json').read_text(encoding='utf8'));self.assertEqual(prov['source_sha256'],hashlib.sha256(src.read_bytes()).hexdigest())
   self.assertEqual(subprocess.run(cmd,capture_output=True).returncode,2)
if __name__=='__main__': unittest.main()
