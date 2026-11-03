#!/usr/bin/env python3
"""Create original presentation frames for an honest offline demo."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import json, textwrap
P=Path(__file__).parent
A=P/"demo_assets";A.mkdir(exist_ok=True)
W,H=1280,720
font="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
bold="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
def f(sz,heavy=False):return ImageFont.truetype(bold if heavy else font,sz)
def txt(d,x,y,s,size=26,c="#F4F7FF",heavy=False):
 d.text((x,y),s,font=f(size,heavy),fill=c)
def frame(kicker,title,detail,n):
 im=Image.new("RGB",(W,H),(10,22,40));d=ImageDraw.Draw(im)
 for y in range(H):
  d.line((0,y,W,y),fill=(int(10+y*.012),int(22+y*.015),int(40+y*.022)))
 d.ellipse((930,-250,1490,330),outline="#1C3561",width=55)
 d.rounded_rectangle((66,47,365,100),24,fill="#24385A")
 txt(d,86,63,"SIMPLISAINS / SCIENCE",16,"#D4E6FF",True)
 txt(d,70,139,kicker.upper(),18,"#4AD3C3",True)
 txt(d,70,183,title,46,"#FFFFFF",True)
 txt(d,72,252,detail,22,"#BED0E9")
 d.line((71,640,1208,640),fill="#375071",width=2)
 txt(d,73,655,"META GLOBAL AI DEVELOPER HACKATHON | HONEST PROTOTYPE DEMO",15,"#A0B8D7",True)
 txt(d,1125,653,str(n).zfill(2)+"/09",16,"#B5CDE8",True)
 return im,d
def card(d,x,y,w,h,heading,body,color="#254269"):
 d.rounded_rectangle((x,y,x+w,y+h),radius=19,fill=color,outline="#3B6594",width=2)
 txt(d,x+20,y+18,heading,23,"#FFFFFF",True)
 for k,line in enumerate(body):txt(d,x+21,y+69+k*36,line,17,"#D0DDEF")
def save(im,i):im.save(A/f"frame_{i:02d}.png")

im,d=frame("The challenge","Every scene should earn its science.","Evidence-grounded visual storytelling for everyone.",1)
txt(d,75,352,"A science short should be accurate, clear, and traceable.",28)
card(d,76,458,541,112,"Research to visual plan",["Explain what the viewer will see."],"#1C4E70")
card(d,648,458,547,112,"Independent source check",["Review evidence before publication."],"#1C4E70")
save(im,1)

im,d=frame("Problem","Confidently wrong is still wrong.","A beautiful scene can communicate an unsupported idea.",2)
card(d,74,350,355,212,"Claim drift",["Creative narration can","outrun source evidence."])
card(d,463,350,355,212,"Visual drift",["Scene direction may imply","the wrong causal story."])
card(d,849,350,355,212,"Missing sources",["Without reference IDs,","claims cannot be audited."])
save(im,2)

im,d=frame("Our architecture","Three roles. Explicit gates.","The model is never its own final scientific authority.",3)
for x,heading,lines in [(75,"01 / DIRECTOR",["Translate source excerpts","into visual scenes."]),(467,"02 / CRITIC",["Challenge unsupported","claims and visuals."]),(860,"03 / REPAIR",["Amend problems; retain","human approval."])]:
 card(d,x,358,342,202,heading,lines)
save(im,3)

im,d=frame("Evidence first","One question. One known source.","A deterministic NASA fixture for reproducible tests.",4)
card(d,76,347,1126,228,"NASA SPACE PLACE / SOURCE S1",[
 "Why is the sky blue, but sunsets often look red?",
 "Shorter blue wavelengths are scattered more strongly by air.",
 "A longer atmospheric path changes the light reaching your eyes."
],"#204466")
save(im,4)

im,d=frame("Interactive preview","A real local web application.","Screenshot of the running offline UI, not a Meta model result.",5)
shot=Image.open(A/"preview_full.png").convert("RGB")
shot.thumbnail((1000,430))
d.rounded_rectangle((112,310,1170,602),radius=14,fill="#24395D")
im.paste(shot.crop((0,0,shot.width, min(shot.height,720))).resize((1015,570)).crop((0,0,1015,267)),(132,325))
txt(d,146,603,"Demo runs at localhost:8891  |  Python standard library HTTP server",16,"#C9DBF5")
save(im,5)

im,d=frame("Actual preview output","A trace, not a black box.","Deterministic example produced by the offline service.",6)
card(d,75,339,545,251,"SCENE 01 / EVIDENCE S1",[
 "Visual: sunlight interacting",
 "with atmosphere.",
 "Narration explains stronger",
 "scattering of blue light."
])
card(d,644,339,557,251,"SCENE 02 / EVIDENCE S1",[
 "Visual: low Sun with a longer",
 "path through the atmosphere.",
 "Narration explains why more",
 "short-wave light scatters."
])
save(im,6)

im,d=frame("Safety boundaries","The output can be blocked.","Unknown evidence IDs and malformed claims fail closed.",7)
card(d,76,348,549,218,"ALLOWED",["Two to three scenes","Valid references (S1)","Critic response has a schema"],"#20574F")
card(d,651,348,548,218,"BLOCKED",["Invented reference S777","False critic approval","Missing or invalid content"],"#6B3548")
save(im,7)

im,d=frame("Verified engineering","17 automated tests passing.","Local results verified on 3 November 2026.",8)
card(d,74,352,355,215,"17 / 17 TESTS",["Standalone preview and","agent orchestration."])
card(d,462,352,356,215,"0 API CALLS",["No claim of successful","Muse inference yet."])
card(d,846,352,356,215,"MIT SOURCE",["Self-contained, testable","public-source package."])
save(im,8)

im,d=frame("Next steps","Truth before submission.","Integration, public video, and official submission remain to be verified.",9)
for i,(header,detail) in enumerate([
 ("Meta API","Key stored privately; authenticated test pending"),
 ("Open-source GitHub","Prepared locally; public URL pending"),
 ("Cerebral Valley","Browser verification blocks the submission form")]):
 y=329+i*93
 card(d,75,y,1130,78,header,[detail],"#254269")
save(im,9)
print("rendered",len(list(A.glob("frame_*.png"))),"original frames")
