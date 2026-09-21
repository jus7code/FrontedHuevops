"""Pruebas CPU: geometría, particiones, métricas y persistencia (YOLO simulado).
Ejecutar: python -m unittest discover -s tests -p test_huevios_pipeline.py
Requiere numpy, opencv-python-headless, PyYAML; no descarga modelos ni necesita torch.
"""
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("huevios_yolov8",ROOT/"cuadernos"/"huevios_yolov8.py")
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)


class HueviosTests(unittest.TestCase):
    def test_notebook_is_standalone_and_syntax_valid(self):
        nb = json.loads((ROOT/"cuadernos"/"Huevios.ipynb").read_text(encoding="utf-8"))
        embedded = None
        for cell in nb["cells"]:
            if cell["cell_type"] == "code":
                source = "".join(cell["source"])
                if source.startswith("%%writefile"):
                    source = source.split("\n",1)[1]
                    embedded = source
                ast.parse(source)
                self.assertEqual(cell["outputs"],[])
        self.assertEqual(embedded.strip(),(ROOT/"cuadernos"/"huevios_yolov8.py").read_text(encoding="utf-8").strip())

    def test_geometry_and_invalid_annotations(self):
        box = hp.parse_label("0 0.5 0.5 0.4 0.2",hp.CLASS_NAMES)
        np.testing.assert_allclose(box["box"],[.3,.4,.7,.6])
        polygon = hp.parse_label("1 0.1 0.2 0.8 0.2 0.8 0.9 0.1 0.9",hp.CLASS_NAMES)
        np.testing.assert_allclose(polygon["box"],[.1,.2,.8,.9])
        for invalid in ("0 nan .5 .2 .2","3 .5 .5 .2 .2","0 .5 .5 0 .2","0 .5 .5 1.1 .2"):
            with self.assertRaises(ValueError):
                hp.parse_label(invalid,hp.CLASS_NAMES)
        image = np.zeros((20,40,3),dtype=np.uint8)
        crop,origin = hp.crop_box(image,[-5,-5,20,20])
        self.assertEqual(origin[:2],(0,0))
        self.assertEqual(crop.shape[:2],(20,22))

    def test_identical_images_share_split_and_missing_labels_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for split in ("train","valid","test"):
                (root/split/"images").mkdir(parents=True)
                (root/split/"labels").mkdir()
            image = np.random.default_rng(4).integers(0,255,(30,40,3),dtype=np.uint8)
            for split,name in (("train","a"),("test","b")):
                cv2.imwrite(str(root/split/"images"/(name+".png")),image)
                (root/split/"labels"/(name+".txt")).write_text("0 .5 .5 .5 .5\n")
            cv2.imwrite(str(root/"valid"/"images"/"missing.png"),image)
            config = {"names":["Normal"],"train":"../train/images","val":"../valid/images","test":"../test/images"}
            (root/"data.yaml").write_text(yaml.safe_dump(config))
            with contextlib.redirect_stdout(io.StringIO()):
                records = hp.collect_records({"sample":root/"data.yaml"},root/"audit",{"sample":{"Normal":"Sano"}})
            self.assertEqual(len(records),2)
            self.assertEqual(records[0]["split"],records[1]["split"])
            self.assertEqual(records[0]["group_id"],records[1]["group_id"])
            rejected = json.loads((root/"audit"/"rejected.json").read_text())
            self.assertEqual(len(rejected),1)

    def test_preparation_only_augments_train_and_rejects_conflicts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            records = []
            rng = np.random.default_rng(8)
            for split in hp.SPLITS:
                for i in range(6):
                    image = rng.integers(0,255,(24,30,3),dtype=np.uint8)
                    path = root/f"{split}{i}.png"
                    cv2.imwrite(str(path),image)
                    records.append({"path":str(path),"source":"test","split":split,"group_id":split+str(i),
                                    "hash":hashlib.sha256(image.tobytes()).hexdigest(),
                                    "labels":[{"name":"Sano","box":[.1,.1,.4,.8]},
                                              {"name":"Roto","box":[.6,.1,.9,.8]}]})
            conflict = dict(records[0])
            conflict["labels"] = [{"name":"Roto","box":[.1,.1,.4,.8]},{"name":"Roto","box":[.6,.1,.9,.8]}]
            with contextlib.redirect_stdout(io.StringIO()):
                result = hp.prepare_dataset(records+[conflict],root/"out")
            self.assertEqual(result["conflicting_images"],2)
            self.assertEqual(result["images"]["train"],5)
            self.assertEqual(result["images"]["train_augmented"],5)
            folder = Path(result["root"])
            self.assertEqual(len(list((folder/"val"/"images").glob("*"))),6)
            self.assertEqual(len(list((folder/"test"/"images").glob("*"))),6)
            self.assertEqual(len(list((folder/"train"/"images").glob("*"))),10)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(hp.prepare_dataset(records+[conflict],root/"out"),result)

    def test_wrong_state_and_duplicate_boxes_count_as_errors(self):
        samples = [
            {"truth":[{"class":0,"box":[0,0,10,10]}],
             "predictions":[{"class":1,"box":[0,0,10,10],"conf":.9}]},
            {"truth":[{"class":1,"box":[0,0,10,10]}],
             "predictions":[{"class":1,"box":[0,0,10,10],"conf":.9},
                            {"class":1,"box":[0,0,10,10],"conf":.8}]},
            {"truth":[],"predictions":[{"class":0,"box":[0,0,10,10],"conf":.9}]},
        ]
        report = hp.operating_metrics(samples,.5)
        self.assertEqual(report["classes"]["Sano"]["fn"],1)
        self.assertEqual(report["classes"]["Sano"]["fp"],1)
        self.assertEqual(report["classes"]["Roto"]["tp"],1)
        self.assertEqual(report["classes"]["Roto"]["fp"],2)
        self.assertEqual(report["negative_frames_with_false_positives"],1)

    def test_halfway_checkpoint_resume_and_changed_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calls = []
            class FakeYOLO:
                def __init__(self,weights):
                    self.callbacks = {}
                    self.trainer = types.SimpleNamespace(save_dir=root/"local"/"train")
                    self.trainer.best = self.trainer.save_dir/"weights"/"best.pt"
                    self.trainer.last = self.trainer.save_dir/"weights"/"last.pt"
                def add_callback(self,name,callback):
                    self.callbacks[name] = callback
                def train(self,**kwargs):
                    calls.append(kwargs)
                    self.trainer.best.parent.mkdir(parents=True,exist_ok=True)
                    for epoch in (0,39,79):
                        self.trainer.epoch = epoch
                        self.trainer.best.write_text(f"best {epoch}")
                        self.trainer.last.write_text(f"last {epoch}")
                        self.callbacks["on_model_save"](self.trainer)
            ultralytics = types.ModuleType("ultralytics")
            ultralytics.YOLO = FakeYOLO
            ultralytics.__version__ = "test"
            torch = types.ModuleType("torch")
            torch.__version__ = "test"
            torch.cuda = types.SimpleNamespace(is_available=lambda:False)
            prepared = {"signature":"stable","yaml":"data.yaml"}
            with patch.dict(sys.modules,{"ultralytics":ultralytics,"torch":torch}), contextlib.redirect_stdout(io.StringIO()):
                best = hp.train_detector(prepared,root/"local",root/"drive")
                self.assertTrue(best.is_file())
                self.assertEqual((root/"drive"/"midpoint.pt").read_text(),"last 39")
                self.assertEqual((root/"drive"/"last.pt").read_text(),"last 79")
                hp.train_detector(prepared,root/"local",root/"drive")
                self.assertEqual(len(calls),1)  # Terminado: no vuelve a entrenar.
                (root/"drive"/"completed.json").unlink()
                with patch.object(hp,"checkpoint_epoch",return_value=(39,True)):
                    hp.train_detector(prepared,root/"local",root/"drive")
                self.assertTrue(calls[-1]["resume"])
                with self.assertRaises(ValueError):
                    hp.train_detector(prepared,root/"local",root/"drive",imgsz=800)

if __name__ == "__main__":
    unittest.main()
