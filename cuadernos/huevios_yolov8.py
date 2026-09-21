"""Preparación, entrenamiento reanudable e inferencia YOLOv8 para HuevOps.
El cuaderno incluye una copia de este módulo para ejecutarse solo en Colab.
"""
from __future__ import annotations
import csv
import hashlib
import json
import math
import os
import re
import shutil
import time
from collections import Counter, defaultdict, deque
from pathlib import Path

import cv2
import numpy as np
import yaml

PIPELINE_VERSION = "huevios-single-v8-1"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SPLITS = ("train", "val", "test")
CLASS_NAMES = {0: "Sano", 1: "Roto"}


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def atomic_copy(source, target):
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, target)


def read_image(path):
    image = cv2.imread(str(path), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    if image is None or min(image.shape[:2]) < 8:
        raise ValueError(f"Imagen ilegible o demasiado pequeña: {path}")
    return image


def parse_label(line, names):
    fields = line.split()
    if not fields:
        return None
    values = np.array([float(x) for x in fields], dtype=float)
    if not np.isfinite(values).all() or not values[0].is_integer():
        raise ValueError("Etiqueta no finita o clase no entera")
    cls = int(values[0])
    if cls not in names:
        raise ValueError(f"Clase desconocida: {cls}")
    coords = values[1:]
    if (coords < -1e-6).any() or (coords > 1 + 1e-6).any():
        raise ValueError("Coordenadas fuera de [0,1]")
    coords = np.clip(coords, 0, 1)
    polygon = None
    if len(coords) == 4:
        cx, cy, w, h = coords
        if w <= 0 or h <= 0:
            raise ValueError("Caja vacía")
        xyxy = np.array([cx-w/2, cy-h/2, cx+w/2, cy+h/2]).clip(0, 1)
    elif len(coords) >= 6 and len(coords) % 2 == 0:
        polygon = coords.reshape(-1, 2)
        if abs(cv2.contourArea(polygon.astype(np.float32))) < 1e-7:
            raise ValueError("Polígono vacío")
        xyxy = np.r_[polygon.min(axis=0), polygon.max(axis=0)]
    else:
        raise ValueError("Se esperaba una caja YOLO o un polígono")
    if (xyxy[2:] <= xyxy[:2]).any():
        raise ValueError("Caja sin área")
    return {"name": names[cls], "box": xyxy.tolist(),
            "polygon": None if polygon is None else polygon.tolist()}


def find_yaml(root, prefer_clean=False):
    candidates = sorted(Path(root).rglob("data.yaml"))
    if prefer_clean:
        clean = [p for p in candidates if "Clean_No Augmentation" in str(p)]
        if len(clean) != 1:
            raise ValueError(f"Selecciona explícitamente el data.yaml limpio de Kaggle: {candidates}")
        return clean[0]
    direct = Path(root) / "data.yaml"
    if direct.is_file():
        return direct
    if len(candidates) != 1:
        raise ValueError(f"Se esperaba un solo data.yaml en {root}: {candidates}")
    return candidates[0]


def image_directories(yaml_path, config, split):
    raw = config.get(split)
    if raw is None:
        return []
    if not isinstance(raw, (str, list)):
        raise ValueError(f"Split no soportado: {split}={raw}")
    paths = raw if isinstance(raw, list) else [raw]
    result = []
    base = Path(yaml_path).parent
    for entry in paths:
        p = Path(entry)
        aliases = ("valid", "val") if split == "val" else (split,)
        candidates = [p] if p.is_absolute() else [base / p, base / str(config.get("path", ".")) / p]
        candidates += [base / alias / "images" for alias in aliases]
        valid = next((q.resolve() for q in candidates if q.is_dir()), None)
        if valid is None:
            raise FileNotFoundError(f"No se encontraron imágenes de {split} en {base}")
        result.append(valid)
    return result


def source_group(source, stem):
    # Mantiene variantes Roboflow y copias del mismo original juntas.
    stem = stem.split(".rf.")[0].lower()
    stem = re.sub(r"[-_ ]copy.*$", "", stem)
    stem = re.sub(r"[-_]aug(?:mented)?[-_0-9]*$", "", stem)
    return source + "/" + stem


def collect_records(sources, audit_dir, class_mappings, group_csv=None):
    records, rejected = [], []
    overrides = {}
    if group_csv and Path(group_csv).is_file():
        with open(group_csv, newline="", encoding="utf-8") as file:
            overrides = {(r["source"], r["filename"]): r["group"] for r in csv.DictReader(file)}
    for source, yaml_path in sources.items():
        config = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
        raw_names = config["names"]
        names = dict(enumerate(raw_names)) if isinstance(raw_names, list) else {int(k): v for k, v in raw_names.items()}
        mapping = class_mappings[source]
        if not set(names.values()).issubset(mapping):
            raise ValueError(f"Falta mapear clases de {source}: {set(names.values()) - set(mapping)}")
        seen_paths = set()
        for split in SPLITS:
            for image_dir in image_directories(yaml_path, config, split):
                for image_path in sorted(image_dir.rglob("*")):
                    if image_path.suffix.lower() not in IMAGE_EXTENSIONS or image_path in seen_paths:
                        continue
                    seen_paths.add(image_path)
                    label_path = image_dir.parent / "labels" / image_path.relative_to(image_dir).with_suffix(".txt")
                    try:
                        if not label_path.is_file():
                            raise ValueError("Falta etiqueta; no se asume un negativo")
                        image = read_image(image_path)
                        labels = [parse_label(line, names) for line in label_path.read_text().splitlines() if line.strip()]
                        for label in labels:
                            label["original_class"] = label["name"]
                            label["name"] = mapping[label["name"]]
                            if label["name"] not in ("Sano", "Roto"):
                                raise ValueError("Clase excluida: se omite la imagen completa")
                        height, width = image.shape[:2]
                        # Una etiqueta minúscula en el borde no se convierte en fondo.
                        if any((l["box"][2]-l["box"][0])*width < 3 or (l["box"][3]-l["box"][1])*height < 3 for l in labels):
                            raise ValueError("Anotación menor de 3 píxeles: revisar imagen completa")
                        pixel_hash = hashlib.sha256(str(image.shape).encode() + image.tobytes()).hexdigest()
                        # dHash solo para agrupar casi duplicados, nunca para clasificar el estado.
                        tiny = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (9, 8))
                        dhash = int.from_bytes(np.packbits(tiny[:, 1:] > tiny[:, :-1]).tobytes(), "big")
                        group = overrides.get((source, image_path.name), source_group(source, image_path.stem))
                        records.append({"source": source, "path": str(image_path), "labels": labels,
                                        "hash": pixel_hash, "dhash": dhash, "group": group,
                                        "width": width, "height": height, "original_split": split})
                    except (ValueError, OSError) as exc:
                        rejected.append({"path": str(image_path), "reason": str(exc)})
    if not records:
        raise ValueError("No hay imágenes válidas")
    audit_dir = Path(audit_dir)
    atomic_json(audit_dir / "rejected.json", rejected)
    # Unión global: misma imagen, variantes de nombre y hashes cercanos no cruzan splits.
    parent = list(range(len(records)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    def union(i, j):
        a, b = find(i), find(j)
        if a != b:
            parent[max(a, b)] = min(a, b)
    keys, buckets = {}, defaultdict(list)
    for i, record in enumerate(records):
        for key in (("pixels", record["hash"]), ("group", record["group"])):
            if key in keys:
                union(i, keys[key])
            else:
                keys[key] = i
        h = record["dhash"]
        # Cuatro bandas garantizan candidato cuando hay <=3 bits de diferencia.
        candidates = set()
        for band in range(4):
            candidates.update(buckets[(band, (h >> (16*band)) & 65535)])
        for j in candidates:
            if (h ^ records[j]["dhash"]).bit_count() <= 3:
                union(i, j)
        for band in range(4):
            buckets[(band, (h >> (16*band)) & 65535)].append(i)
    members = defaultdict(list)
    for i in range(len(records)):
        members[find(i)].append(i)
    group_sizes = []
    groups = []
    for indices in members.values():
        identity = min(records[i]["hash"] for i in indices)
        counts = Counter(l["name"] for i in indices for l in records[i]["labels"])
        vector = np.array([len(indices),counts["Sano"],counts["Roto"]],dtype=float)
        groups.append((indices,identity,vector))
    totals = sum((g[2] for g in groups),np.zeros(3))
    targets = np.array([.70,.15,.15])[:,None]*totals[None,:]
    assigned = np.zeros_like(targets)
    # Mantiene grupos completos y aproxima tamaños y equilibrio de ambas clases.
    for indices,identity,vector in sorted(groups,key=lambda g:(-len(g[0]),g[1])):
        costs = []
        for s in range(3):
            before = ((assigned[s]-targets[s])**2/np.maximum(targets[s],1)).sum()
            after = ((assigned[s]+vector-targets[s])**2/np.maximum(targets[s],1)).sum()
            costs.append(after-before)
        destination = int(np.argmin(costs))
        assigned[destination] += vector
        for i in indices:
            records[i]["split"] = SPLITS[destination]
            records[i]["group_id"] = identity
        group_sizes.append(len(indices))
    report = {"images": len(records), "rejected": len(rejected), "groups": len(members),
              "largest_group": max(group_sizes),
              "splits": dict(Counter(r["split"] for r in records)),
              "classes": dict(Counter(r["source"]+"/"+l["name"] for r in records for l in r["labels"])),
              "note": "dHash es conservador; revisar grupos grandes y añadir IDs de video/lote en groups.csv."}
    atomic_json(audit_dir / "audit.json", report)
    atomic_json(audit_dir / "records.json", records)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return records


def show_audit(records, destination, per_source=6):
    import matplotlib.pyplot as plt
    sources = sorted({r["source"] for r in records})
    fig, axes = plt.subplots(len(sources), per_source, figsize=(4*per_source, 4*len(sources)), squeeze=False)
    for row, source in enumerate(sources):
        candidates = [r for r in records if r["source"] == source]
        # Prioriza variedad de clases; selección reproducible.
        chosen = sorted(candidates, key=lambda r: hashlib.sha256(r["path"].encode()).hexdigest())[:per_source]
        for ax, record in zip(axes[row], chosen):
            image = read_image(record["path"])
            h, w = image.shape[:2]
            for label in record["labels"]:
                box = np.array(label["box"]) * [w, h, w, h]
                x1, y1, x2, y2 = box.astype(int)
                cv2.rectangle(image, (x1,y1), (x2,y2), (0,255,0), 2)
                cv2.putText(image, label["name"], (x1,max(15,y1)), cv2.FONT_HERSHEY_SIMPLEX,.4,(0,0,255),1)
                if label["polygon"]:
                    polygon = (np.array(label["polygon"]) * [w,h]).astype(np.int32)
                    cv2.polylines(image, [polygon], True, (255,0,0), 2)
            ax.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            ax.set_title(source+" / "+record["split"])
        for ax in axes[row]:
            ax.axis("off")
    fig.tight_layout()
    fig.savefig(str(destination), dpi=110)
    plt.show()


def crop_box(image, box, margin=.08):
    h, w = image.shape[:2]
    x1,y1,x2,y2 = np.array(box, dtype=float)
    dx,dy = (x2-x1)*margin, (y2-y1)*margin
    x1,y1 = max(0,math.floor(x1-dx)), max(0,math.floor(y1-dy))
    x2,y2 = min(w,math.ceil(x2+dx)), min(h,math.ceil(y2+dy))
    if x2 <= x1 or y2 <= y1:
        raise ValueError("Recorte vacío")
    return image[y1:y2,x1:x2].copy(), (x1,y1,x2,y2)


def square_pad(image):
    h,w = image.shape[:2]
    side = max(h,w)
    return cv2.copyMakeBorder(image, (side-h)//2, side-h-(side-h)//2,
                             (side-w)//2, side-w-(side-w)//2, cv2.BORDER_CONSTANT, value=(114,114,114))


def box_line(box, cls=0):
    x1,y1,x2,y2 = box
    return f"{cls} " + " ".join(f"{v:.8f}" for v in ((x1+x2)/2,(y1+y2)/2,x2-x1,y2-y1))


def polygon_line(polygon, cls=0):
    return f"{cls} " + " ".join(f"{v:.8f}" for point in polygon for v in point)


def write_sample(root, split, stem, image, labels):
    root = Path(root)
    images, annotations = root / split / "images", root / split / "labels"
    images.mkdir(parents=True, exist_ok=True)
    annotations.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(images / (stem+".png")), image):
        raise IOError("No se pudo guardar una imagen")
    (annotations / (stem+".txt")).write_text("\n".join(labels)+("\n" if labels else ""), encoding="utf-8")



def photometric_variant(image, rng):
    """Aumentos moderados SOLO en train, sin mover las cajas ni borrar grietas."""
    rgb = image.astype(np.float32) / 255
    rgb = np.power(rgb, rng.uniform(.8, 1.25)) * rng.uniform(.85, 1.15)
    h,w = image.shape[:2]
    if rng.random() < .45:
        gradient = np.linspace(rng.uniform(.70,.95),1,w,dtype=np.float32)[None,:,None]
        rgb *= gradient
    rgb += rng.normal(0, rng.uniform(0,.008), rgb.shape).astype(np.float32)
    output = np.clip(rgb*255,0,255).astype(np.uint8)
    if rng.random() < .15:
        output = cv2.GaussianBlur(output,(3,3),.45)
    if rng.random() < .30:
        ok, encoded = cv2.imencode(".jpg",output,[cv2.IMWRITE_JPEG_QUALITY,int(rng.integers(85,98))])
        if ok:
            output = cv2.imdecode(encoded,cv2.IMREAD_COLOR)
    return output


def annotation_signature(record):
    return sorted((label["name"],*(round(v,3) for v in label["box"])) for label in record["labels"])


def prepare_dataset(records, output, augment=True):
    # No publica un marcador hasta que la preparación está completa.
    payload = {"version":PIPELINE_VERSION,"records":records,"augment":augment}
    signature = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:16]
    root = Path(output)/signature
    marker = root/"prepared.json"
    if marker.is_file():
        return json.loads(marker.read_text())
    root.mkdir(parents=True,exist_ok=True)
    by_hash = defaultdict(list)
    for record in records:
        by_hash[record["hash"]].append(record)
    kept, conflicts = [], []
    for variants in by_hash.values():
        signatures = {json.dumps(annotation_signature(r)) for r in variants}
        if len(signatures) > 1:
            conflicts.extend({"path":r["path"],"reason":"Mismos píxeles con etiquetas distintas"} for r in variants)
            continue
        kept.append(variants[0])
    counts, classes = Counter(), Counter()
    rng = np.random.default_rng(42)
    exported = []
    for record in kept:
        image = read_image(record["path"])
        stem, split = record["hash"][:24],record["split"]
        labels = [box_line(label["box"],0 if label["name"]=="Sano" else 1) for label in record["labels"]]
        write_sample(root,split,stem,image,labels)
        counts[split] += 1
        for label in record["labels"]:
            classes[split+"/"+label["name"]] += 1
        exported.append({"file":str(root/split/"images"/(stem+".png")),
                         "source":record["source"],"source_file":record["path"],
                         "split":split,"group":record["group_id"]})
        if split == "train" and augment and labels:
            write_sample(root,split,stem+"_light",photometric_variant(image,rng),labels)
            counts["train_augmented"] += 1
    for split in SPLITS:
        if counts[split] == 0:
            raise ValueError(f"No hay imágenes en {split}; revisar grupos")
        for status in CLASS_NAMES.values():
            if classes[split+"/"+status] < 5:
                raise ValueError(f"Menos de 5 anotaciones {status} en {split}. Revisar partición por grupos.")
    data = {"path":str(root.resolve()),"train":"train/images","val":"val/images","test":"test/images",
            "names":CLASS_NAMES}
    (root/"data.yaml").write_text(yaml.safe_dump(data,sort_keys=False),encoding="utf-8")
    atomic_json(root/"conflicts.json",conflicts)
    atomic_json(root/"provenance.json",exported)
    result = {"root":str(root),"yaml":str(root/"data.yaml"),"signature":signature,
              "images":dict(counts),"instances":dict(classes),"conflicting_images":len(conflicts)}
    atomic_json(marker,result)
    print(json.dumps(result,indent=2,ensure_ascii=False))
    return result


def add_verified_negatives(sources, mappings, negative_root):
    """Carpeta opcional de imágenes SIN huevos, revisadas manualmente.
    Requiere labels .txt vacíos junto a images; no adivina que un archivo sin etiqueta sea negativo.
    """
    if negative_root:
        yaml_path = find_yaml(negative_root)
        config = yaml.safe_load(yaml_path.read_text())
        # Las imágenes vacías aportan negativos; las positivas requieren anotaciones normales.
        sources["camera"] = str(yaml_path)
        raw = config["names"]
        names = raw if isinstance(raw,list) else raw.values()
        mappings["camera"] = {name:name for name in names if name in ("Sano","Roto")}
    return sources,mappings


def checkpoint_epoch(path):
    import torch
    # Solo checkpoints propios de este entrenamiento.
    ckpt = torch.load(str(path),map_location="cpu",weights_only=False)
    return int(ckpt.get("epoch",-1)), ckpt.get("optimizer") is not None


def train_detector(prepared, local_root, persistent_root, epochs=80, imgsz=640, batch=8,
                   base_model="yolov8s.pt", device=0, patience=20):
    from ultralytics import YOLO, __version__
    import torch
    local, persistent = Path(local_root),Path(persistent_root)
    local.mkdir(parents=True,exist_ok=True)
    persistent.mkdir(parents=True,exist_ok=True)
    config = {"pipeline":PIPELINE_VERSION,"signature":prepared["signature"],"epochs":epochs,
              "imgsz":imgsz,"base_model":base_model,"ultralytics":__version__,"torch":torch.__version__,
              "patience":patience}
    config_path = persistent/"training_config.json"
    for existing in (config_path,local/"training_config.json"):
        if existing.is_file() and json.loads(existing.read_text()) != config:
            raise ValueError("Cambió el dataset, versión o entrenamiento. Usa otro RUN_ID; no mezcles checkpoints.")
    atomic_json(config_path,config)
    atomic_json(local/"training_config.json",config)
    weights = local/"train"/"weights"
    weights.mkdir(parents=True,exist_ok=True)
    for filename in ("last.pt","best.pt","midpoint.pt"):
        saved = persistent/filename
        current = weights/filename
        if saved.is_file() and (not current.is_file() or saved.stat().st_mtime > current.stat().st_mtime):
            atomic_copy(saved,current)
    done = persistent/"completed.json"
    if done.is_file():
        if not (weights/"best.pt").is_file():
            raise FileNotFoundError("Hay marcador de finalización, pero falta best.pt")
        print("Entrenamiento ya terminado; reutilizando best.pt")
        return weights/"best.pt"
    last = weights/"last.pt"
    resume = False
    candidates = []
    for candidate in (last,persistent/"last.pt",weights/"midpoint.pt",persistent/"midpoint.pt"):
        if candidate.is_file():
            try:
                saved_epoch, optimizer = checkpoint_epoch(candidate)
                candidates.append((saved_epoch,optimizer,candidate))
            except Exception:
                print(f"Checkpoint ilegible, se intentará otra copia: {candidate}")
    if last.is_file() and not candidates:
        raise RuntimeError("No queda ningún checkpoint legible; restaura una copia de Drive.")
    if candidates:
        # Un last finalizado está depurado (epoch=-1); se reconoce antes de elegir el mayor epoch.
        finished = next((c for c in candidates if c[0] == -1 and not c[1] and c[2].name == "last.pt"),None)
        epoch,has_optimizer,chosen = finished or max(candidates,key=lambda c:c[0])
        if chosen.resolve() != last.resolve():
            atomic_copy(chosen,last)
        if epoch >= 0 and has_optimizer:
            resume = True
            print(f"Reanudando después de la época {epoch+1}")
        elif (weights/"best.pt").is_file():
            # Ultralytics elimina el optimizador al finalizar normalmente.
            atomic_json(done,{"completed":True,"recovered_finished_run":True})
            return weights/"best.pt"
        else:
            raise RuntimeError("Checkpoint no reanudable y no existe best.pt")
    model = YOLO(str(last) if resume else base_model)
    halfway = math.ceil(epochs/2)
    def persist(trainer):
        epoch = trainer.epoch+1
        # on_model_save ocurre después de escribir pesos/optimizador.
        for filename in ("last.pt","best.pt"):
            source = Path(trainer.save_dir)/"weights"/filename
            if source.is_file():
                atomic_copy(source,persistent/filename)
        if epoch == halfway:
            atomic_copy(Path(trainer.last),persistent/"midpoint.pt")
            atomic_copy(Path(trainer.last),weights/"midpoint.pt")
        atomic_json(persistent/"progress.json",{"epoch":epoch,"epochs":epochs,"halfway":halfway})
        results = Path(trainer.save_dir)/"results.csv"
        if results.is_file():
            atomic_copy(results,persistent/"results.csv")
    model.add_callback("on_model_save",persist)
    if resume:
        model.train(resume=True,device=device,batch=batch,workers=2)
    else:
        model.train(
            data=prepared["yaml"],epochs=epochs,imgsz=imgsz,batch=batch,device=device,
            project=str(local),name="train",exist_ok=True,seed=42,deterministic=True,
            patience=patience,save=True,save_period=-1,workers=2,cache=False,
            optimizer="auto",cos_lr=True,amp=torch.cuda.is_available(),plots=True,
            hsv_h=.01,hsv_s=.25,hsv_v=.25,degrees=12,translate=.08,scale=.25,
            fliplr=.5,flipud=.1,mosaic=.25,close_mosaic=10,mixup=0,copy_paste=0,
        )
    best = Path(model.trainer.best)
    if not best.is_file():
        raise FileNotFoundError(f"Falta el mejor modelo: {best}")
    atomic_copy(best,persistent/"best.pt")
    atomic_json(done,{"completed":True,"best":str(best),"halfway_epoch":halfway,
                     "note":"Si early stopping terminó antes de la mitad, conserva last y best."})
    return best


def box_iou(a,b):
    x1,y1 = max(a[0],b[0]),max(a[1],b[1])
    x2,y2 = min(a[2],b[2]),min(a[3],b[3])
    inter = max(0,x2-x1)*max(0,y2-y1)
    aa = max(0,a[2]-a[0])*max(0,a[3]-a[1])
    bb = max(0,b[2]-b[0])*max(0,b[3]-b[1])
    return inter/max(aa+bb-inter,1e-9)


def operating_metrics(samples, threshold, iou_threshold=.5):
    # Match geométrico primero; clase incorrecta = FP de la predicha + FN de la real.
    tp,fp,fn = np.zeros(2,int),np.zeros(2,int),np.zeros(2,int)
    false_positive_frames = 0
    negative_frames = 0
    for sample in samples:
        truth = sample["truth"]
        predictions = sorted((p for p in sample["predictions"] if p["conf"] >= threshold),
                             key=lambda p:p["conf"],reverse=True)
        available = set(range(len(truth)))
        if not truth:
            negative_frames += 1
            false_positive_frames += bool(predictions)
        for prediction in predictions:
            overlaps = [(box_iou(prediction["box"],truth[j]["box"]),j) for j in available]
            overlap,j = max(overlaps,default=(0,-1))
            pc = prediction["class"]
            if overlap >= iou_threshold:
                available.remove(j)
                tc = truth[j]["class"]
                if pc == tc:
                    tp[tc] += 1
                else:
                    fp[pc] += 1
                    fn[tc] += 1
            else:
                fp[pc] += 1
        for j in available:
            fn[truth[j]["class"]] += 1
    precision = np.divide(tp,tp+fp,out=np.zeros(2,float),where=(tp+fp)>0)
    recall = np.divide(tp,tp+fn,out=np.zeros(2,float),where=(tp+fn)>0)
    f1 = np.divide(2*precision*recall,precision+recall,out=np.zeros(2,float),where=(precision+recall)>0)
    return {"threshold":float(threshold),"macro_f1":float(f1.mean()),
            "classes":{CLASS_NAMES[i]:{"precision":float(precision[i]),"recall":float(recall[i]),
                                      "f1":float(f1[i]),"tp":int(tp[i]),"fp":int(fp[i]),"fn":int(fn[i])} for i in range(2)},
            "negative_frames":negative_frames,"negative_frames_with_false_positives":false_positive_frames}


def prediction_samples(model, image_dir, imgsz=640, device=0):
    paths = sorted(p for p in Path(image_dir).rglob("*") if p.suffix.lower() in IMAGE_EXTENSIONS)
    samples = []
    for start in range(0,len(paths),8):
        chunk = paths[start:start+8]
        results = model.predict([str(p) for p in chunk],imgsz=imgsz,device=device,conf=.01,
                                iou=.5,agnostic_nms=True,verbose=False)
        for path,result in zip(chunk,results):
            h,w = result.orig_shape
            labels = path.parent.parent/"labels"/(path.stem+".txt")
            truth = []
            for line in labels.read_text().splitlines():
                annotation = parse_label(line,CLASS_NAMES)
                cls = 0 if annotation["name"]=="Sano" else 1
                truth.append({"class":cls,"box":(np.array(annotation["box"])*[w,h,w,h]).tolist()})
            predictions = [{"class":int(c),"conf":float(s),"box":b.tolist()}
                           for b,s,c in zip(result.boxes.xyxy.cpu().numpy(),result.boxes.conf.cpu().numpy(),result.boxes.cls.cpu().numpy())]
            samples.append({"path":str(path),"truth":truth,"predictions":predictions})
    return samples


def evaluate_detector(best, prepared, output, imgsz=640, device=0):
    from ultralytics import YOLO
    output = Path(output)
    model = YOLO(str(best))
    reports = {}
    for split in ("val","test"):
        metrics = model.val(data=prepared["yaml"],split=split,imgsz=imgsz,device=device,
                            project=str(output/"validation"),name=split,plots=True,
                            conf=.001,iou=.5,agnostic_nms=True,verbose=False)
        reports[split] = {str(k):float(v) for k,v in metrics.results_dict.items()}
    # Selección del umbral SOLO con val. Test se informa una vez con umbral congelado.
    validation = prediction_samples(model,Path(prepared["root"])/"val"/"images",imgsz,device)
    sweep = [operating_metrics(validation,float(t)) for t in np.arange(.15,.86,.05)]
    selected = max(sweep,key=lambda r:r["macro_f1"])
    testing = prediction_samples(model,Path(prepared["root"])/"test"/"images",imgsz,device)
    reports["operating_point_val"] = selected
    reports["operating_point_test"] = operating_metrics(testing,selected["threshold"])
    reports["threshold_sweep_val"] = sweep
    reports["limitations"] = ["Test agrupado por duplicados; no garantiza escenas/lotes nuevos sin groups.csv.",
                              "Sin negativos de cámara no se estima fiabilidad frente a obstáculos.",
                              "La confianza del modelo no es una probabilidad calibrada."]
    atomic_json(output/"metrics.json",reports)
    atomic_json(output/"thresholds.json",{"confidence":selected["threshold"],"iou":.5,"imgsz":imgsz,
                                        "calibrated_probability":False})
    atomic_json(output/"test_predictions.json",testing)
    print(json.dumps(reports["operating_point_test"],indent=2,ensure_ascii=False))
    return reports


class EggVideoDetector:
    """Un único modelo; seguimiento por ID y voto temporal solo de clase.
    Las cajas siempre son las del frame actual: nunca dibuja cajas antiguas.
    """
    def __init__(self,weights,thresholds=None,device=0):
        from ultralytics import YOLO
        self.model = YOLO(str(weights))
        config = json.loads(Path(thresholds).read_text()) if thresholds else {}
        self.conf = config.get("confidence",.5)
        self.iou = config.get("iou",.5)
        self.imgsz = config.get("imgsz",640)
        self.device = device
        self.history = {}
        self.frame_index = 0

    def process(self,frame,tracking=True):
        self.frame_index += 1
        start = time.perf_counter()
        kwargs = dict(imgsz=self.imgsz,conf=self.conf,iou=self.iou,device=self.device,
                      agnostic_nms=True,verbose=False)
        result = (self.model.track(frame,persist=True,tracker="bytetrack.yaml",**kwargs)[0]
                  if tracking else self.model.predict(frame,**kwargs)[0])
        canvas = frame.copy()
        detections = []
        ids = result.boxes.id
        for i,box in enumerate(result.boxes):
            cls,confidence = int(box.cls[0]),float(box.conf[0])
            xyxy = box.xyxy[0].cpu().numpy().tolist()
            track_id = int(ids[i]) if ids is not None else None
            status = CLASS_NAMES[cls]
            if tracking and track_id is not None:
                history = self.history.setdefault(track_id,{"votes":deque(maxlen=5),"last":0})
                history["last"] = self.frame_index
                history["votes"].append(cls)
                votes = list(history["votes"])
                # Señal rota actual se muestra inmediatamente; sano requiere estabilidad.
                status = "Roto" if cls == 1 else ("Sano" if len(votes)>=3 and all(v==0 for v in votes[-3:]) else "Incierto")
            detections.append({"track_id":track_id,"egg_box_xyxy":xyxy,"status":status,
                               "raw_status":CLASS_NAMES[cls],"confidence":confidence})
            color = (0,0,255) if status=="Roto" else ((0,180,0) if status=="Sano" else (0,180,255))
            x1,y1,x2,y2 = map(int,xyxy)
            cv2.rectangle(canvas,(x1,y1),(x2,y2),color,2)
            cv2.putText(canvas,f"{status} {confidence:.2f}",(x1,max(y1-8,18)),
                        cv2.FONT_HERSHEY_SIMPLEX,.6,color,2)
        self.history = {k:v for k,v in self.history.items() if self.frame_index-v["last"]<=30}
        elapsed = time.perf_counter()-start
        return canvas,{"detections":detections,"latency_ms":elapsed*1000,
                       "model_pipeline_fps":1/max(elapsed,1e-9)}


def process_video(weights,source,destination,thresholds=None,device=0,max_frames=None):
    # En Colab source debe ser un vídeo subido o una URL accesible.
    # La cámara del PC no es cv2.VideoCapture(0) dentro de la VM remota.
    detector = EggVideoDetector(weights,thresholds,device)
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        raise ValueError(f"No se pudo abrir el vídeo: {source}")
    writer = None
    latencies = []
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not np.isfinite(fps) or fps<=0:
            fps = 25
        index = 0
        while max_frames is None or index<max_frames:
            ok,frame = capture.read()
            if not ok:
                break
            annotated,report = detector.process(frame)
            if writer is None:
                h,w = frame.shape[:2]
                writer = cv2.VideoWriter(str(destination),cv2.VideoWriter_fourcc(*"mp4v"),fps,(w,h))
                if not writer.isOpened():
                    raise IOError("No se pudo crear el vídeo de salida")
            writer.write(annotated)
            latencies.append(report["latency_ms"])
            index += 1
    finally:
        capture.release()
        if writer is not None:
            writer.release()
    if not latencies:
        raise ValueError("Vídeo sin frames legibles")
    measured = latencies[5:] if len(latencies)>5 else latencies
    stats = {"frames":len(latencies),"mean_latency_ms":float(np.mean(measured)),
             "p95_latency_ms":float(np.percentile(measured,95)),
             "pipeline_fps":1000/float(np.mean(measured)),
             "note":"Medición de inferencia y dibujo, sin decodificación/escritura/red; no es FPS de extremo a extremo."}
    atomic_json(str(destination)+".metrics.json",stats)
    return stats
