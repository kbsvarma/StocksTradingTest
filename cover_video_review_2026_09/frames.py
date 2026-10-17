from pathlib import Path
import av
from PIL import Image,ImageDraw
P=Path(__file__).resolve().parent;D=P/'frames';D.mkdir(exist_ok=True)
c=av.open('/Users/varmakammili/Downloads/Stanford professor took two average stocks.mp4');s=c.streams.video[0]
print('duration',c.duration/1e6,'resolution',s.width,s.height,'streams',[(x.type,str(x.codec_context.name)) for x in c.streams],flush=True)
images=[]
for sec in list(range(0,int(c.duration/1e6),60))+[int(c.duration/1e6)-2]:
 c.seek(int(sec/s.time_base),stream=s,backward=True)
 for frame in c.decode(s):
  if float(frame.pts*s.time_base)>=sec:
   img=frame.to_image();img.save(D/f'{sec:04d}.jpg');images.append((sec,img));break
for offset in range(0,len(images),9):
 batch=images[offset:offset+9];sheet=Image.new('RGB',(1293,3*268),'white');draw=ImageDraw.Draw(sheet)
 for j,(sec,img) in enumerate(batch):
  x=(j%3)*431;y=(j//3)*268;img=img.resize((431,240));sheet.paste(img,(x,y+24));draw.text((x+5,y+5),f'{sec//60:02d}:{sec%60:02d}',fill='black')
 sheet.save(P/f'contact_{offset//9:02d}.jpg')
print('frames',len(images),flush=True)
