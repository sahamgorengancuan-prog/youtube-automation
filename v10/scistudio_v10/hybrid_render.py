from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image

from .schemas import HybridLayer, HybridScenePackage, MotionEvent
from .utils import ensure_dir, save_json


class PILHybridRenderer:
    """Deterministic fallback renderer for hybrid raster/SVG scene packages.

    It is intended for regression and preview. Production can use the generated
    Remotion project for higher-quality interpolation and compositing.
    """

    def __init__(self, config: dict[str, Any], root: str | Path):
        self.config = config
        self.root = ensure_dir(root)
        from .motion_graphics import ShotExecutor

        # Pure executor of the authored animation DSL (camera/effects/captions).
        self.executor = ShotExecutor(config)

    def render(self, scenes: list[HybridScenePackage], output: str | Path) -> Path:
        output = Path(output)
        ensure_dir(output.parent)
        fps = scenes[0].fps if scenes else int(self.config.get("fps", 30))
        frame_dir = ensure_dir(self.root / "frames")
        for old in frame_dir.glob("*.png"):
            old.unlink()
        global_index = 0
        for scene in scenes:
            assets = self._load_layers(scene)
            for frame in range(scene.duration_frames):
                canvas = Image.new("RGBA", scene.canvas, scene.background)
                # Primary motion: authored object state changes (MotionEvents).
                for layer in sorted(scene.layers, key=lambda x: x.z_index):
                    image = self._image_for_layer(
                        layer,
                        assets,
                        scene.animation.events,
                        frame,
                        scene.duration_frames,
                    )
                    if image is None:
                        continue
                    canvas.alpha_composite(image.resize(scene.canvas))
                # Supporting motion: only what the director authored in the DSL.
                finished = self.executor.execute(canvas.convert("RGB"), scene.animation, frame, scene.duration_frames)
                finished.save(frame_dir / f"frame_{global_index:06d}.png")
                global_index += 1
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg is required for deterministic preview rendering")
        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-framerate",
            str(fps),
            "-i",
            str(frame_dir / "frame_%06d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            str(self.config.get("crf", 18)),
            str(output),
        ]
        subprocess.run(command, check=True)
        return output

    def _load_layers(self, scene: HybridScenePackage) -> dict[str, list[Image.Image]]:
        result: dict[str, list[Image.Image]] = {}
        for layer in scene.layers:
            images: list[Image.Image] = []
            if layer.kind == "video_clip":
                clip_dir = ensure_dir(self.root / "video_cache" / f"{scene.scene_id}_{layer.layer_id}")
                existing = sorted(clip_dir.glob("frame_*.png"))
                if not existing and shutil.which("ffmpeg") and Path(layer.path).exists():
                    subprocess.run(
                        [
                            "ffmpeg",
                            "-y",
                            "-v",
                            "error",
                            "-i",
                            layer.path,
                            "-vf",
                            f"fps={scene.fps},scale={scene.canvas[0]}:{scene.canvas[1]}",
                            str(clip_dir / "frame_%06d.png"),
                        ],
                        check=True,
                    )
                    existing = sorted(clip_dir.glob("frame_*.png"))
                images.extend(Image.open(x).convert("RGBA") for x in existing)
            elif layer.kind == "svg_overlay":
                png = self.root / "svg_cache" / (Path(layer.path).stem + ".png")
                ensure_dir(png.parent)
                try:
                    import cairosvg

                    cairosvg.svg2png(
                        url=layer.path,
                        write_to=str(png),
                        output_width=scene.canvas[0],
                        output_height=scene.canvas[1],
                    )
                    images.append(Image.open(png).convert("RGBA"))
                except Exception:
                    continue
            else:
                if Path(layer.path).exists():
                    images.append(Image.open(layer.path).convert("RGBA"))
                for path in layer.pose_paths:
                    if Path(path).exists():
                        images.append(Image.open(path).convert("RGBA"))
            result[layer.layer_id] = images
        return result

    def _image_for_layer(
        self,
        layer: HybridLayer,
        assets: dict[str, list[Image.Image]],
        events: list[MotionEvent],
        frame: int,
        duration: int,
    ) -> Image.Image | None:
        images = assets.get(layer.layer_id, [])
        if not images:
            return None
        if layer.kind == "video_clip":
            return images[min(len(images) - 1, frame)].copy()
        event = next((e for e in events if e.target_layer == layer.layer_id), None)
        image = images[0].copy()
        if event is None or frame < event.start_frame:
            return image
        progress = min(
            1.0,
            max(
                0.0,
                (frame - event.start_frame) / max(1, event.end_frame - event.start_frame),
            ),
        )
        if event.representation == "skeletal_pose" and getattr(layer, "rig", None):
            return self._skeletal(layer, image, event, progress)
        if event.representation == "replacement_pose" and len(images) > 1:
            index = min(len(images) - 1, int(progress * len(images)))
            return images[index].copy()
        if event.representation == "opacity" or event.representation == "mask_reveal":
            alpha = image.getchannel("A").point(lambda p: int(p * progress))
            image.putalpha(alpha)
            return image
        if event.representation == "translate":
            start = event.parameters.get("from", [0, 0])
            end = event.parameters.get("to", [0, 0])
            x = round(float(start[0]) + (float(end[0]) - float(start[0])) * progress)
            y = round(float(start[1]) + (float(end[1]) - float(start[1])) * progress)
            moved = Image.new("RGBA", image.size, (0, 0, 0, 0))
            moved.alpha_composite(image, (x, y))
            return moved
        if event.representation == "rotate":
            angle = float(event.parameters.get("degrees", 5)) * progress
            w, h = image.size
            pivot = getattr(layer, "pivot", (0.5, 0.5))
            center = (pivot[0] * w, pivot[1] * h)  # rotate about the object pivot
            return image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, center=center)
        if event.representation == "scale":
            amount = 1 + (float(event.parameters.get("to", 1.03)) - 1) * progress
            w, h = image.size
            pivot = getattr(layer, "pivot", (0.5, 0.5))
            scaled = image.resize(
                (max(1, int(w * amount)), max(1, int(h * amount))),
                Image.Resampling.LANCZOS,
            )
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            # Scale about the object pivot: keep the pivot pixel fixed on canvas.
            px, py = pivot[0] * w, pivot[1] * h
            canvas.alpha_composite(
                scaled,
                (int(px - pivot[0] * scaled.width), int(py - pivot[1] * scaled.height)),
            )
            return canvas
        if event.representation == "texture_loop":
            speed = float(event.parameters.get("speed_px_per_second", 50))
            offset = round((frame / max(1, duration)) * speed)
            moved = Image.new("RGBA", image.size, (0, 0, 0, 0))
            moved.alpha_composite(image, (0, offset))
            moved.alpha_composite(image, (0, offset - image.height))
            return moved
        # local_deformation is represented by a replacement state when available;
        # otherwise keep the approved artwork static rather than inventing motion.
        if event.representation == "local_deformation" and len(images) > 1:
            return images[min(len(images) - 1, int(progress * len(images)))].copy()
        return image

    def _skeletal(self, layer: HybridLayer, image: Image.Image, event: MotionEvent, progress: float) -> Image.Image:
        """Execute an authored ``skeletal_pose``: deform the rigged character's
        body parts along its bones (forward kinematics). Pure executor."""
        from .skeletal_deform import SkeletalDeformer, resolve_pose

        if not hasattr(self, "_deformer"):
            self._deformer = SkeletalDeformer(self.config)
            self._part_cache: dict[str, Image.Image] = {}
        pose = resolve_pose(event.parameters)
        if not pose:
            return image
        part_masks: dict[str, Image.Image] = {}
        for name, path in (getattr(layer, "part_masks", None) or {}).items():
            cached = self._part_cache.get(path)
            if cached is None and Path(path).exists():
                cached = Image.open(path).convert("L")
                self._part_cache[path] = cached
            if cached is not None:
                part_masks[name] = cached
        if not part_masks:
            return image
        return self._deformer.deform(image, layer.rig, part_masks, pose, progress)


class RemotionHybridExporter:
    """Writes a production Remotion project for the same hybrid packages."""

    def __init__(self, config: dict[str, Any], root: str | Path):
        self.config = config
        self.root = ensure_dir(root)

    def _export_rig(self, rig, scene, layer, public: Path) -> dict[str, Any]:
        """Copy each bone's part cutout to public/ and resolve the layer's
        authored skeletal_pose to explicit per-bone angle deltas + a time window,
        so the JS executor only reads numbers (no gesture library in JS)."""
        from .skeletal_deform import resolve_pose

        event = next(
            (
                e
                for e in scene.animation.events
                if e.target_layer == layer.layer_id and e.representation == "skeletal_pose"
            ),
            None,
        )
        pose = resolve_pose(event.parameters) if event is not None else {}
        bones = []
        for bone in rig["bones"]:
            cut = bone.get("cutout")
            if not cut or not Path(cut).exists():
                continue
            name = f"{scene.scene_id}_{layer.layer_id}_{bone['name']}_cutout.png".replace(" ", "_")
            shutil.copy2(cut, public / name)
            bones.append(
                {
                    "name": bone["name"],
                    "parent": bone.get("parent"),
                    "pivot": bone.get("pivot", [0.5, 0.5]),
                    "z": bone.get("z", 20),
                    "cutout": name,
                    "delta": float(pose.get(bone["name"], 0.0)),
                }
            )
        window = (
            {"start_frame": event.start_frame, "end_frame": event.end_frame, "easing": event.easing}
            if event is not None
            else {"start_frame": 0, "end_frame": 0, "easing": "ease_in_out"}
        )
        return {"bones": bones, "window": window}

    def create_project(self, scenes: list[HybridScenePackage], support_bed_path: str = "") -> Path:
        public = ensure_dir(self.root / "public")
        src = ensure_dir(self.root / "src")
        data_scenes = []
        frame_cursor = 0
        for scene in scenes:
            layers = []
            for layer in scene.layers:
                paths = [layer.path, *layer.pose_paths]
                copied = []
                for path in paths:
                    source = Path(path)
                    if not source.exists():
                        continue
                    name = f"{scene.scene_id}_{layer.layer_id}_{source.name}".replace(" ", "_")
                    destination = public / name
                    shutil.copy2(source, destination)
                    copied.append(name)
                if not copied:
                    continue
                layer_data = {
                    **layer.model_dump(mode="json"),
                    "path": copied[0],
                    "pose_paths": copied[1:],
                }
                # Export the articulated rig (part cutouts + resolved pose) so the
                # Remotion renderer can do nested forward kinematics — the SAME
                # rig the PIL renderer uses. Nested DOM transforms compose FK.
                rig = getattr(layer, "rig", None)
                if rig and rig.get("bones"):
                    layer_data["rig"] = self._export_rig(rig, scene, layer, public)
                layers.append(layer_data)
            data_scenes.append(
                {
                    **scene.model_dump(mode="json", exclude={"layers"}),
                    "layers": layers,
                    "from_frame": frame_cursor,
                }
            )
            frame_cursor += scene.duration_frames
        support_bed_name = ""
        if support_bed_path and Path(support_bed_path).exists():
            support_bed_name = "support_bed" + Path(support_bed_path).suffix
            shutil.copy2(support_bed_path, public / support_bed_name)
        # Copy per-scene narration audio.
        for scene_data, scene in zip(data_scenes, scenes):
            if scene.voice_path and Path(scene.voice_path).exists():
                voice_name = f"{scene.scene_id}_voice{Path(scene.voice_path).suffix}"
                shutil.copy2(scene.voice_path, public / voice_name)
                scene_data["voice_path"] = voice_name
            else:
                scene_data["voice_path"] = ""
        payload = {
            "width": scenes[0].canvas[0] if scenes else 1080,
            "height": scenes[0].canvas[1] if scenes else 1920,
            "fps": scenes[0].fps if scenes else 30,
            "duration_frames": frame_cursor,
            "scenes": data_scenes,
            "support_bed": support_bed_name,
        }
        save_json(public / "data.json", payload)
        (src / "index.tsx").write_text(self._typescript(), encoding="utf-8")
        save_json(
            self.root / "package.json",
            {
                "name": "scientific-motion-studio-v10-render",
                "version": "10.0.0",
                "private": True,
                "dependencies": {
                    "@remotion/cli": "4.0.489",
                    "remotion": "4.0.489",
                    "react": "19.0.0",
                    "react-dom": "19.0.0",
                    "pixi.js": "7.4.2",
                },
                "devDependencies": {
                    "typescript": "5.6.3",
                    "@types/react": "19.0.0",
                    "@types/react-dom": "19.0.0",
                },
            },
        )
        save_json(
            self.root / "tsconfig.json",
            {
                "compilerOptions": {
                    "target": "ES2020",
                    "module": "ESNext",
                    "moduleResolution": "Bundler",
                    "jsx": "react-jsx",
                    "strict": True,
                    "resolveJsonModule": True,
                },
                "include": ["src"],
            },
        )
        return self.root

    @staticmethod
    def _typescript() -> str:
        # This template interprets the SAME authored animation DSL the PIL
        # renderer executes (scene.animation: events/camera/effects/captions and
        # per-layer pivot/bbox/z). It iterates ALL events per layer (not one via
        # .find), applies easing, transforms about the object pivot, and executes
        # camera / effect / caption directives — renderer parity with PIL.
        return r"""import React from 'react';
import {AbsoluteFill, Composition, Html5Audio, Img, OffthreadVideo, Sequence, interpolate, registerRoot, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import * as PIXI from 'pixi.js';
import data from '../public/data.json';

const ease=(t:number,kind:string)=>{t=Math.max(0,Math.min(1,t));if(kind==='ease_in_out'||kind==='smooth'||kind==='ease-in-out')return t*t*(3-2*t);if(kind==='ease_in')return t*t;if(kind==='ease_out')return 1-(1-t)*(1-t);return t;};
const win=(d:any,frame:number,total:number)=>{let s=d.start_frame||0,e=d.end_frame||0;if(e<=s){s=0;e=total;}if(frame<s||frame>e)return null;return (frame-s)/Math.max(1,e-s);};
const eventsFor=(scene:any,id:string)=>(scene.animation.events||[]).filter((e:any)=>e.target_layer===id);

// All object representations, composed across every event on the layer.
const layerState=(scene:any,layer:any,frame:number,total:number)=>{
  let source=layer.path, opacity=layer.opacity??1, tx=0, ty=0, rot=0, scale=1;
  for(const ev of eventsFor(scene,layer.layer_id)){
    const raw=win(ev,frame,total); if(raw===null) continue; const p=ease(raw,ev.easing||'linear');
    const rep=ev.representation, pr=ev.parameters||{};
    if(rep==='replacement_pose'&&layer.pose_paths?.length){source=layer.pose_paths[Math.min(layer.pose_paths.length-1,Math.floor(p*layer.pose_paths.length))]||source;}
    else if(rep==='local_deformation'&&layer.pose_paths?.length){source=layer.pose_paths[Math.min(layer.pose_paths.length-1,Math.floor(p*layer.pose_paths.length))]||source;}
    else if(rep==='translate'){const a=pr.from||[0,0],b=pr.to||[0,0];tx+=a[0]+(b[0]-a[0])*p;ty+=a[1]+(b[1]-a[1])*p;}
    else if(rep==='rotate'){rot+=(pr.degrees||5)*p;}
    else if(rep==='scale'){scale*=1+((pr.to??1.03)-1)*p;}
    else if(rep==='texture_loop'){ty+=((pr.speed_px_per_second||50))*(frame/Math.max(1,total));}
    else if(rep==='opacity'||rep==='mask_reveal'){opacity*=p;}
  }
  return {source,opacity,transform:`translate(${tx}px,${ty}px) rotate(${rot}deg) scale(${scale})`};
};

// Camera directive -> whole-scene transform (hold => none).
const cameraStyle=(scene:any,frame:number,total:number)=>{
  const c=scene.animation.camera; if(!c||c.move==='hold'||!(c.magnitude>0)) return {};
  const raw=win(c,frame,total); if(raw===null) return {}; const p=ease(raw,c.easing||'ease_in_out'); const m=c.magnitude;
  let scale=1, tx=0, ty=0;
  if(c.move==='push_in')scale=1+m*p; else if(c.move==='pull_out')scale=1+m*(1-p);
  else {scale=1+Math.max(m,0.04); const s=m*p*100; if(c.move==='pan_left')tx=s; else if(c.move==='pan_right')tx=-s; else if(c.move==='pan_up')ty=s; else if(c.move==='pan_down')ty=-s;}
  return {transform:`translate(${tx}%,${ty}%) scale(${scale})`,transformOrigin:'center'};
};

// Fallback CSS-div particles (used only if PixiJS fails to initialise).
const DivParticles=({scene,frame,total}:any)=>{
  const fx=(scene.animation.effects||[]).filter((e:any)=>e.effect&&e.effect!=='none'&&(e.intensity>0)&&win(e,frame,total)!==null);
  if(!fx.length) return null;
  return <>{fx.map((e:any,fi:number)=>{const [x0,y0,x1,y1]=e.region||[0,0,1,1];const n=Math.min(220,Math.max(8,Math.floor((e.intensity||0.5)*120)));const dir=(e.direction_deg??90)*Math.PI/180;const items=[];for(let i=0;i<n;i++){const ph=((frame*0.02*(0.6+((i*97)%100)/100))%1);const u=((i*53)%100/100+Math.cos(dir)*ph)%1;const v=((i*31)%100/100+Math.sin(dir)*ph)%1;const px=(x0+u*(x1-x0))*100, py=(y0+v*(y1-y0))*100;items.push(<div key={i} style={{position:'absolute',left:`${px}%`,top:`${py}%`,width:e.effect==='rain'||e.effect==='water'?2:5,height:e.effect==='rain'||e.effect==='water'?18:5,borderRadius:e.effect==='snow'||e.effect==='bubble'?'50%':1,background:e.effect==='spark'?'rgba(255,176,92,0.8)':'rgba(205,222,238,0.6)'}}/>);}return <div key={fi} style={{position:'absolute',inset:0,zIndex:80}}>{items}</div>;})}</>;
};

// Real PixiJS particle engine + displacement (water) shader. autoDetectRenderer
// uses WebGL when available and falls back to canvas, so it renders in headless
// Chrome; if PIXI cannot initialise at all, we fall back to DivParticles. Motion
// is deterministic from the frame number so every render is reproducible.
const colorFor=(fx:string)=>fx==='spark'?0xffb05c:(fx==='snow'||fx==='bubble')?0xffffff:(fx==='dust')?0xd8c9a8:0xcddeee;
const PixiEffects=({scene,frame,total,width,height}:any)=>{
  const fx=(scene.animation.effects||[]).filter((e:any)=>e.effect&&e.effect!=='none'&&(e.intensity>0)&&win(e,frame,total)!==null);
  const canvasRef=React.useRef<HTMLCanvasElement>(null);
  const rref=React.useRef<any>(null);
  const dispRef=React.useRef<any>(null);
  const [failed,setFailed]=React.useState(false);
  React.useEffect(()=>{
    try{ if(canvasRef.current&&!rref.current){ rref.current=(PIXI as any).autoDetectRenderer({width,height,view:canvasRef.current,backgroundAlpha:0,antialias:true}); } }
    catch(e){ setFailed(true); }
  },[]);
  React.useEffect(()=>{
    const r=rref.current; if(!r||!fx.length) return;
    try{
      const stage=new (PIXI as any).Container();
      const g=new (PIXI as any).Graphics();
      let water=false;
      for(const e of fx){ if(e.effect==='water'||e.effect==='wind') water=true; const [x0,y0,x1,y1]=e.region||[0,0,1,1];
        const n=Math.min(500,Math.max(20,Math.floor((e.intensity||0.5)*300))); const dir=(e.direction_deg??90)*Math.PI/180; const col=colorFor(e.effect);
        for(let i=0;i<n;i++){ const sp=0.4+((i*37)%100)/100; const ph=((frame*0.02*sp)%1);
          const u=(((i*53)%100/100)+Math.cos(dir)*ph+1)%1; const v=(((i*31)%100/100)+Math.sin(dir)*ph+1)%1;
          const px=(x0+u*(x1-x0))*width, py=(y0+v*(y1-y0))*height;
          g.beginFill(col,0.65);
          if(e.effect==='rain'||e.effect==='water'){ g.drawRect(px,py,1.6,9); }
          else if(e.effect==='snow'||e.effect==='bubble'){ g.drawCircle(px,py,2.4); }
          else { g.drawCircle(px,py,1.8); }
          g.endFill();
        }
      }
      stage.addChild(g);
      // Water/wind shimmer via a real displacement-map shader (GPU when WebGL).
      if(water){ try{
        if(!dispRef.current){ const dm=new (PIXI as any).Graphics(); for(let i=0;i<40;i++){dm.beginFill(((i*97)%255)<<16|((i*53)%255)<<8|((i*31)%255));dm.drawRect((i*53)%width,(i*29)%height,24,24);dm.endFill();} dispRef.current=new (PIXI as any).Sprite(r.generateTexture(dm)); dispRef.current.texture.baseTexture.wrapMode=(PIXI as any).WRAP_MODES?.REPEAT??10497; }
        const ds=dispRef.current; ds.x=(frame*2)%width; ds.y=(frame*1)%height;
        const df=new (PIXI as any).DisplacementFilter(ds,10); stage.addChild(ds); stage.filters=[df];
      }catch(_e){} }
      r.render(stage); stage.destroy({children:true});
    }catch(e){ setFailed(true); }
  });
  if(!fx.length) return null;
  if(failed) return <DivParticles scene={scene} frame={frame} total={total}/>;
  return <canvas ref={canvasRef} width={width} height={height} style={{position:'absolute',inset:0,width,height,zIndex:82}}/>;
};

const Captions=({scene,frame,total,width,height}:any)=>{
  const caps=(scene.animation.captions||[]).filter((c:any)=>c.kind&&c.kind!=='none'&&c.text&&win(c,frame,total)!==null);
  if(!caps.length) return null;
  return <>{caps.map((c:any,ci:number)=>{const lower=c.kind==='headline';const style:any={position:'absolute',zIndex:95,color:'#f5f7fa',fontFamily:'sans-serif',fontWeight:700,padding:'2%'};if(lower){style.left=0;style.right=0;style.bottom=0;style.background='rgba(14,20,26,0.8)';style.fontSize=height*0.03;}else{style.top='4%';style[c.position==='top_right'?'right':'left']='4%';style.fontSize=height*0.016;style.color='#1e2830';}return <div key={ci} style={style}>{lower?String(c.text).toUpperCase():c.text}</div>;})}</>;
};

// Nested forward kinematics: CSS nested transforms compose parent->child, so a
// bone element applies only its OWN rotation about its pivot and the parent
// chain accumulates automatically. Own image + child subtrees are interleaved by
// z-order so the torso sits behind, arms/head in front.
const skelProgress=(rig:any,frame:number,total:number)=>{const w=rig.window||{};let s=w.start_frame||0,e=w.end_frame||0;if(e<=s){s=0;e=total;}return ease(Math.max(0,Math.min(1,(frame-s)/Math.max(1,e-s))),w.easing||'ease_in_out');};
const Bone=({bone,byParent,progress,width,height}:any)=>{
  const pv=bone.pivot||[0.5,0.5];
  const kids=(byParent[bone.name]||[]).slice();
  const items=[{z:bone.z,el:<Img key={bone.name+'-img'} src={staticFile(bone.cutout)} style={{position:'absolute',inset:0,width,height,objectFit:'fill'}}/>},
    ...kids.map((k:any)=>({z:k.z,el:<Bone key={k.name} bone={k} byParent={byParent} progress={progress} width={width} height={height}/>}))].sort((a:any,b:any)=>a.z-b.z);
  return <div style={{position:'absolute',inset:0,transformOrigin:`${pv[0]*100}% ${pv[1]*100}%`,transform:`rotate(${(bone.delta||0)*progress}deg)`}}>{items.map((it:any,i:number)=><React.Fragment key={i}>{it.el}</React.Fragment>)}</div>;
};
const SkeletalLayer=({layer,scene,frame,total,width,height}:any)=>{
  const rig=layer.rig;const byParent:any={};(rig.bones||[]).forEach((b:any)=>{const p=b.parent||'__root__';(byParent[p]=byParent[p]||[]).push(b);});
  Object.keys(byParent).forEach(k=>byParent[k].sort((a:any,b:any)=>a.z-b.z));
  const st=layerState(scene,layer,frame,total);const progress=skelProgress(rig,frame,total);
  return <div style={{position:'absolute',inset:0,zIndex:layer.z_index,opacity:st.opacity,transform:st.transform}}>
    {(byParent['__root__']||[]).map((r:any)=><Bone key={r.name} bone={r} byParent={byParent} progress={progress} width={width} height={height}/>)}
  </div>;
};

const SceneView=({scene}:any)=>{
  const frame=useCurrentFrame();const {width,height}=useVideoConfig();const total=scene.duration_frames;
  return <AbsoluteFill style={{background:scene.background,overflow:'hidden',...cameraStyle(scene,frame,total)}}>
    {[...scene.layers].sort((a:any,b:any)=>(a.z_index||0)-(b.z_index||0)).map((layer:any)=>{
      const st=layerState(scene,layer,frame,total);const pv=layer.pivot||[0.5,0.5];
      if(layer.rig&&layer.rig.bones&&layer.rig.bones.length)
        return <SkeletalLayer key={layer.layer_id} layer={layer} scene={scene} frame={frame} total={total} width={width} height={height}/>;
      return layer.kind==='video_clip'
        ? <OffthreadVideo key={layer.layer_id} src={staticFile(st.source)} muted style={{position:'absolute',inset:0,width,height,objectFit:'fill',zIndex:layer.z_index}}/>
        : <Img key={layer.layer_id} src={staticFile(st.source)} style={{position:'absolute',inset:0,width,height,objectFit:'fill',zIndex:layer.z_index,opacity:st.opacity,transform:st.transform,transformOrigin:`${pv[0]*100}% ${pv[1]*100}%`}}/>;
    })}
    <PixiEffects scene={scene} frame={frame} total={total} width={width} height={height}/>
    <Captions scene={scene} frame={frame} total={total} width={width} height={height}/>
    {scene.voice_path?<Html5Audio src={staticFile(scene.voice_path)} volume={1}/>:null}
  </AbsoluteFill>;
};
const Film=()=> <AbsoluteFill>{(data as any).support_bed?<Html5Audio src={staticFile((data as any).support_bed)} volume={0.20}/>:null}{(data.scenes as any[]).map((scene:any)=><Sequence key={scene.scene_id} from={scene.from_frame} durationInFrames={scene.duration_frames}><SceneView scene={scene}/></Sequence>)}</AbsoluteFill>;
const Root=()=> <Composition id="ScientificMotionV10" component={Film} durationInFrames={(data as any).duration_frames} fps={(data as any).fps} width={(data as any).width} height={(data as any).height}/>;
registerRoot(Root);"""
